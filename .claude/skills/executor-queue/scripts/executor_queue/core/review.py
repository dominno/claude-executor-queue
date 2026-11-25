"""Review rules: inherited review bases, sensitive paths, declared scope and stale bases (CORE)."""

import fnmatch
import os
from pathlib import Path
import re
import shlex
import sys

from . import repo, seams
from .brief import BriefModel, read_brief_text
from .config import check_mode, config
from .repo import ancestor, base_in_main, commit_known, known_main, same_worktree, task_brief
from .state import valid_id
from .util import display_name, fail, listed_paths, SHA_RE, short, STATUS_DIR


def inherited_review_base(a, root, known, previous, worktree, base):
    """Oldest start of earlier unpassed attempts (adapters with inherits_review_base) whose commits the new
    assignment base contains.

    Ancestry decides, not the worktree path: a dropped attempt whose branch a new worktree checks out
    still brings its commits. Rejected attempts are retired; queued ones never ran.
    """
    follows = getattr(a, 'follows', None)
    found = []
    if follows:
        valid_id(follows)
        if follows.casefold() == a.id.casefold():
            fail('--follows must name an earlier task, not {} itself'.format(a.id))
        predecessor = known.get(follows)
        if predecessor is None:
            similar = [k for k in known if k.casefold() == follows.casefold()]
            fail('--follows {}: unknown task{}; see xq.py status'.format(
                follows, '; did you mean {}?'.format(similar[0]) if similar else ''))
        if predecessor.get('executor') != a.executor:
            fail('--follows {} used executor {}; a follow-up must use the same executor ({})'.format(
                follows, predecessor.get('executor') or '-', a.executor))
        recorded = predecessor.get('worktree')
        if not same_worktree(recorded, worktree):
            gone = isinstance(recorded, str) and os.path.isabs(recorded) and not os.path.isdir(recorded)
            start = predecessor.get('base') or predecessor.get('review_base')
            descends = bool(base and start and commit_known(root, start) and ancestor(start, base, root))
            if not (gone or descends):
                fail('--follows {} ran in worktree "{}"; assign the follow-up with --worktree <that worktree> or a '
                     'worktree whose HEAD descends from its base'.format(
                         follows, display_name(str(recorded or '-'))[:200]))
        value = predecessor.get('review_base') or predecessor.get('base')
        if value:
            found.append((follows, value))
    if seams.ADAPTERS[a.executor].inherits_review_base and base and not base_in_main(root, base):
        for tid, task in sorted(known.items()):
            reason = str(task.get('reason') or '')
            if (tid == follows or task.get('executor') != a.executor or task.get('verdict') == 'pass' or
                    task.get('status') == 'queued' or
                    (task.get('closed') and reason != 'dropped' and not reason.startswith('superseded-by:'))):
                continue
            start = task.get('base') or task.get('review_base')
            if (not isinstance(start, str) or not SHA_RE.fullmatch(start) or not commit_known(root, start) or
                    (start != base and not ancestor(start, base, root))):
                continue
            value = task.get('review_base') or start
            if isinstance(value, str) and SHA_RE.fullmatch(value) and commit_known(root, value):
                found.append((tid, value))
    if not found:
        return None
    values = list(dict.fromkeys(value for _, value in found))
    oldest = values[0]
    if len(values) > 1:
        merged = seams.git('merge-base', '--octopus', *values, check=False, cwd=root).strip()
        oldest = merged if SHA_RE.fullmatch(merged) else oldest
    sources = [tid for tid, value in found if value == oldest and tid != follows]
    if sources and oldest != base:
        print('NOTE review base inherited from {}'.format(', '.join(sources[:5])))
    return oldest


# Paths in an executor delivery that can steer agents, run code on install/test/checkout,
# or change repository policy. Matching is case-insensitive (macOS/Windows filesystems).
SENSITIVE_NAMES = frozenset((
    # Agent instructions and control files.
    'AGENTS.md', 'AGENTS.override.md', 'CLAUDE.md', 'CLAUDE.local.md', 'GEMINI.md', '.cursorrules',
    '.windsurfrules', '.mcp.json', '.agy-bridge.json',
    # Git and build policy.
    '.gitattributes', '.gitmodules', '.lfsconfig', 'Makefile', 'GNUmakefile', 'makefile', 'Justfile', 'justfile',
    'build.rs', 'Rakefile', '.gitlab-ci.yml', 'gradle-wrapper.properties',
    # Package manifests, tool versions and auto-run configuration.
    'package.json', 'pyproject.toml', 'setup.py', 'setup.cfg', 'conftest.py', 'pytest.ini', 'tox.ini', 'noxfile.py',
    '.pre-commit-config.yaml', '.npmrc', '.yarnrc', '.yarnrc.yml', '.envrc', '.env', 'mise.toml', '.mise.toml',
    '.tool-versions', '.pnpmfile.cjs', 'go.mod', 'Cargo.toml', 'Gemfile',
    # Lockfiles.
    'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'Pipfile.lock', 'poetry.lock', 'uv.lock', 'Cargo.lock',
    'Gemfile.lock', 'composer.lock', 'bun.lockb', 'npm-shrinkwrap.json', 'packages.lock.json', 'go.sum',
    # JVM, C/C++, Bazel, Scala and Nix builds, task runners and containers.
    'build.gradle', 'build.gradle.kts', 'settings.gradle', 'settings.gradle.kts', 'gradlew', 'pom.xml',
    'CMakeLists.txt', 'meson.build', 'BUILD', 'BUILD.bazel', 'WORKSPACE', 'WORKSPACE.bazel', '.bazelrc', 'build.sbt',
    'Taskfile.yml', 'flake.nix', 'Dockerfile', '.devcontainer.json',
    # Interpreter start-up hooks, debugger rc files and runtime version pins.
    '.pdbrc', 'sitecustomize.py', 'usercustomize.py', '.nvmrc', '.node-version', '.python-version'))
# Kept a superset of agy-bridge's workspace.py EDIT_DENY (tests check the copy).
SENSITIVE_DIRS = frozenset(('.claude', '.github', '.husky', 'hooks', '.githooks', '.vscode', '.idea', '.devcontainer',
                            '.gemini', '.codex', '.cursor', '.agy-bridge', '.cargo', '.agent', '_agents', '_agent',
                            '.windsurf', '.clinerules', '.roo', '.kiro', '.direnv', '.yarn', '.mvn', '.circleci'))
SENSITIVE_GLOBS = ('*.lock', '.env.*', 'requirements*.txt', '*.pth', '*.gradle', '*.gradle.kts', 'Dockerfile*',
                   'docker-compose*.yml', 'docker-compose*.yaml', 'compose*.yml', 'compose*.yaml', '*.config.js', '*.config.ts',
                   '*.config.mjs', '*.config.cjs', '.eslintrc*')
# Names that are sensitive only as the file itself: a build/ output directory is not a Bazel BUILD file.
SENSITIVE_FILE_ONLY = frozenset(('BUILD',))
_SENSITIVE_NAMES_CF = frozenset(name.casefold() for name in SENSITIVE_NAMES)
_SENSITIVE_DIR_NAMES_CF = _SENSITIVE_NAMES_CF - frozenset(name.casefold() for name in SENSITIVE_FILE_ONLY)
_SENSITIVE_DIRS_CF = frozenset(name.casefold() for name in SENSITIVE_DIRS)
_SENSITIVE_GLOBS_CF = tuple(glob.casefold() for glob in SENSITIVE_GLOBS)


def sensitive_path(path, tid, brief=None, symlinks=()):
    name = Path(path).name.casefold()
    return (path == brief or path in symlinks or name in _SENSITIVE_NAMES_CF or
            any(fnmatch.fnmatchcase(name, pattern) for pattern in _SENSITIVE_GLOBS_CF) or
            # Any component, like agy-bridge's EDIT_DENY: a file name used as a directory still counts.
            any(part.casefold() in _SENSITIVE_DIRS_CF or part.casefold() in _SENSITIVE_DIR_NAMES_CF
                for part in Path(path).parts) or
            # Every .agents path, at any depth (config, briefs, other status files), except this task's
            # own status; the exemption is exact-case so .Agents/status/<id>.json stays sensitive.
            (path != STATUS_DIR + '/' + tid + '.json' and
             any(part.casefold() == '.agents' for part in Path(path).parts)))


def sensitive_paths(root, base, head, tid, brief=None, cache=None):
    if cache is None:
        cache = {}
    key = (base, head)
    if key not in cache:
        changed, special = repo.history_paths(root, base, head)
        cache[key] = (sorted(changed), special)
    changed, special = cache[key]
    return [path for path in changed if sensitive_path(path, tid, brief, special)]


def sensitive_warning(path, special=None):
    reason = 'submodule pointer; ' if isinstance(special, dict) and special.get(path) == 'submodule pointer' else ''
    return 'WARN SENSITIVE {} ({}untrusted executor change; inspect before running tests or merging)'.format(
        display_name(path), reason)


SENSITIVE_PRINT_LIMIT = 20


def print_sensitive(lines, prefix='', limit=None):
    """Print WARN SENSITIVE lines, at most SENSITIVE_PRINT_LIMIT, then one '+N more' line."""
    limit = SENSITIVE_PRINT_LIMIT if limit is None else limit
    for line in lines[:limit]:
        print(prefix + line, file=sys.stderr)
    if len(lines) > limit:
        print('{}WARN SENSITIVE (+{} more not shown; verify lists the paths it records)'.format(
            prefix, len(lines) - limit), file=sys.stderr)


def print_reports(reports):
    for lines, prefix in reports:
        print_sensitive(lines, prefix)


def sensitive_review_base(root, t, head, main=None):
    """Start of the reviewed range for t's executor (ExecutorAdapter.sensitive_review_base)."""
    return seams.adapter_for(t.get('executor')).sensitive_review_base(root, t, head, main)


def main_fork_review_base(root, t, head, main=None):
    """The older of the recorded review base (earlier unpassed attempts or the assignment base) and the fork
    from main, so every commit not yet in main is reviewed."""
    base = t.get('review_base') or t.get('base')
    main = known_main(root, main or t.get('main'))
    fork = seams.git('merge-base', head, 'refs/heads/' + main, check=False, cwd=root).strip() if main else ''
    fork = fork if SHA_RE.fullmatch(fork) else None
    if not base or not fork or base == fork:
        return base or fork
    if not commit_known(root, base):
        return base  # sensitive_review reports a base missing from this repository.
    older = seams.git('merge-base', base, fork, check=False, cwd=root).strip()
    return older if SHA_RE.fullmatch(older) else base


def sensitive_review(root, t, a, head, event, base, replacing=None):
    """Integrated deliveries are untrusted: name auto-run/agent/policy paths and require an explicit ack.

    --force skips the acknowledgement only for reconciliation of the recorded delivery; a pass that
    replaces it with another head (replacing) always needs --ack-sensitive when there are hits.
    """
    brief = task_brief(root, t)
    hits, special = [], {}
    if seams.run(['git', 'cat-file', '-e', base + '^{commit}'], cwd=root).returncode:
        print('WARN cannot list sensitive paths: recorded base {} is not in this repository'.format(short(base)),
              file=sys.stderr)
    else:
        cache = {}
        hits = sensitive_paths(root, base, head, a.id, brief=brief, cache=cache)
        special = cache[(base, head)][1]
    adapter = seams.adapter_for(t.get('executor'))
    if hits:
        hits = adapter.reviewed_hits(root, t, a.id, head, hits)
    local = adapter.local_sensitive(root, t, a.id, brief)
    print_sensitive([sensitive_warning(path, special) for path in hits] + [sensitive_warning(path) for path in local])
    hits = hits + local
    event['sensitive'] = hits[:50]
    if len(hits) > 50:
        event['sensitive_more'] = len(hits) - 50
    if not hits:
        return
    if not a.ack_sensitive and (not a.force or replacing):
        checkout = t.get('worktree') or str(root)
        fail('delivery touches {} sensitive path(s): {}; inspect them (git -C {} diff {}..{} -- <path>{}) and re-run '
             'with --ack-sensitive'.format(len(hits), listed_paths(hits, 5), shlex.quote(checkout),
                                           short(base), short(head),
                                           '; git -C {} status --porcelain --ignored -uall'.format(
                                               shlex.quote(checkout)) if local else ''))
    if a.ack_sensitive:
        event['ack_sensitive'] = True


BRACE_RE = re.compile(r'\{([^{}]*)\}')
SCOPE_PRINT_LIMIT = 10  # below SENSITIVE_PRINT_LIMIT: poll/finish print the lines through print_reports


def expand_braces(pattern):
    """Every alternative of the {a,b} groups in a declared path (non-nested), in order."""
    match = BRACE_RE.search(pattern)
    if not match:
        return [pattern]
    head, tail = pattern[:match.start()], pattern[match.end():]
    return [item for choice in match.group(1).split(',') for item in expand_braces(head + choice + tail)]


def segments_match(parts, globs):
    """fnmatchcase per path segment; a '**' segment matches zero or more whole segments."""
    if not globs:
        return not parts
    if globs[0] == '**':
        return any(segments_match(parts[skip:], globs[1:]) for skip in range(len(parts) + 1))
    return bool(parts) and fnmatch.fnmatchcase(parts[0], globs[0]) and segments_match(parts[1:], globs[1:])


def scope_match(path, pattern):
    """True when a repo-relative changed path is inside one declared Files entry.

    '/'-aware fnmatch, case-sensitive like Git paths: '*', '?' and '[...]' never cross '/', a '**' segment
    spans any number of directories, {a,b} lists alternatives, a leading './' is ignored, and an entry that
    ends in '/' or has no wildcard also covers everything below it (a directory)."""
    parts = path.split('/')
    for option in expand_braces(pattern.replace('\\', '/')):
        option = option[2:] if option.startswith('./') else option
        globs = [part for part in option.split('/') if part]
        if not globs:
            continue
        if segments_match(parts, globs):
            return True
        if (option.endswith('/') or not any(ch in option for ch in '*?[')) and segments_match(
                parts[:len(globs)], globs) and len(parts) > len(globs):
            return True
    return False


def out_of_scope(paths, declared):
    """Changed paths no declared Files entry covers, in input order."""
    return [path for path in paths if not any(scope_match(path, pattern) for pattern in declared)]


def declared_files(root, task):
    """The Files: entries of the task brief's ## Acceptance ([] when none are declared), or None when the brief
    cannot be read."""
    brief = task.get('brief')
    if not isinstance(brief, str) or not brief:
        return None
    path = Path(brief) if os.path.isabs(brief) else root / brief
    try:
        return BriefModel(read_brief_text(path)).files()
    except (OSError, SystemExit):
        return None


def scope_findings(root, task, paths):
    """(paths outside the declared Files, or None when not checked; WARN lines) for an executor's delivered
    paths (None: the executor has no delivered range). Mode off (config scope_check) checks nothing."""
    if paths is None or check_mode(config(root), 'scope_check') == 'off':
        return None, []
    tid = task.get('id') or '-'
    declared = declared_files(root, task)
    if declared is None:
        return None, ['WARN SCOPE {}: cannot read the brief; scope not checked'.format(tid)]
    if not declared:
        return None, ['WARN SCOPE {}: no declared Files in the brief\'s ## Acceptance; scope not checked'.format(tid)]
    outside = out_of_scope(paths, declared)
    lines = ['WARN SCOPE {}: {} is outside the declared Files'.format(tid, display_name(path)[:200])
             for path in outside[:SCOPE_PRINT_LIMIT]]
    if len(outside) > SCOPE_PRINT_LIMIT:
        lines.append('WARN SCOPE {}: +{} more outside the declared Files'.format(tid, len(outside) - SCOPE_PRINT_LIMIT))
    return outside, lines


def scope_review(root, t, a, head, event, paths):
    """Pass gate for diff within the declared Files (config scope_check: off|warn|enforce, default warn).

    Out-of-scope paths are recorded as out_of_scope; enforce refuses the pass unless --accept-scope <reason>,
    which is recorded as scope_accepted. A brief without Files: is reported, never refused."""
    outside, lines = scope_findings(root, t, paths)
    for line in lines:
        print(line, file=sys.stderr)
    if not outside:
        if a.accept_scope:
            print('NOTE --accept-scope unused: no out-of-scope paths were found', file=sys.stderr)
        return
    event['out_of_scope'] = outside[:50]
    if a.accept_scope:
        event['scope_accepted'] = a.accept_scope.strip()[:2000]
    elif check_mode(config(root), 'scope_check') == 'enforce':
        fail('delivery changes {} path(s) outside the brief\'s declared Files: {}; widen Files: in the brief, '
             'revert them, or re-run with --accept-scope "<reason>" (scope_check is enforce)'.format(
                 len(outside), listed_paths(outside, 5)))


def stale_base_main(root):
    """The default branch a new worktree must contain: config main / origin/HEAD, else the primary checkout's
    branch; None when none resolves to a local branch (offline: local refs only, no fetch)."""
    main = known_main(root)
    if main:
        return main
    local = seams.git('symbolic-ref', '--quiet', '--short', 'HEAD', check=False, cwd=root).strip()
    if local and not seams.run(['git', 'rev-parse', '--verify', '--quiet', 'refs/heads/' + local], cwd=root).returncode:
        return local
    return None


def stale_base_finding(root, worktree, base):
    """(mode, message) for a worktree base missing default-branch commits (config stale_base: off|warn|refuse,
    default warn); message names the commit count and the exact fix, None when nothing is behind or not checked."""
    mode = check_mode(config(root), 'stale_base')
    if mode == 'off' or not base:
        return mode, None
    main = stale_base_main(root)
    if not main:
        print('NOTE stale-base check skipped: no default branch resolves (set "main" in .agents/config.json)')
        return mode, None
    behind = seams.git('rev-list', '--count', base + '..refs/heads/' + main, check=False, cwd=root).strip()
    if not behind.isdigit() or behind == '0':
        return mode, None
    wt, ref = shlex.quote(worktree), shlex.quote(main)
    return mode, ('stale base: worktree {} HEAD {} is {} commit(s) behind {}; update it first: git -C {} rebase {} '
                  '(or git -C {} merge {})'.format(wt, short(base), behind, main, wt, ref, wt, ref))


def stale_base_check(root, worktree, base, force=False):
    """Assign admission: WARN a stale worktree base; refuse mode blocks it unless force."""
    mode, message = stale_base_finding(root, worktree, base)
    if message and mode == 'refuse' and not force:
        fail(message + '; --force records the assignment anyway (stale_base is refuse)')
    if message:
        print('WARN ' + message, file=sys.stderr)
