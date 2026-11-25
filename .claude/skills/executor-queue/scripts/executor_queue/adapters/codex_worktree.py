"""Codex worktree inspection (ADAPTERS): the checkout scan, sensitive and untracked files, and the delivery
found in a Codex worktree."""

from contextlib import contextmanager
import fnmatch
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile

from ..core import paths, seams
from ..core.repo import ancestor, commit_known, known_main, resolve_commit, same_worktree, task_brief
from ..core.review import (print_reports, scope_findings, sensitive_path, sensitive_paths, sensitive_review_base,
                           sensitive_warning, stale_base_main)
from ..core.util import display_name, fail, listed_paths, SHA_RE
from ..port import runner
from ..port.runner import open_process, worktree_env, worktree_git, WORKTREE_GIT_CONFIG


REMOVED_WORKTREE_STEP = ('record finish --id {0} --status failed --summary "worktree removed" then close --id {0} '
                         '--reason dropped (or finish --force --head <sha> if the delivery commit survives)')


VERIFY_REJECT_STEP = ('record xq.py verify --id {0} --verdict reject ... (reject needs no worktree), then close '
                      '--id {0} --reason rejected')


def existing_worktree(task, root, context='finish'):
    checkout = task.get('worktree') or str(root)
    if not os.path.isdir(checkout):
        tid = task.get('id') or '<id>'
        if context == 'verify':
            step = ('commit the delivery and pass --head <sha> (a commit reachable in the primary checkout) if the '
                    'delivery commit survives, or ' + VERIFY_REJECT_STEP)
        else:
            step = REMOVED_WORKTREE_STEP
        fail('worktree "{}" no longer exists; {}'.format(display_name(checkout), step.format(tid)))
    return checkout


def same_repository(root, checkout):
    """The checkout shares root's object store and is the primary checkout or a registered linked worktree.

    A .git file may point anywhere and borrow the real store through its commondir; only a git dir directly
    under <common>/worktrees/<name> whose gitdir file names <checkout>/.git is one `git worktree add` made.
    """
    shown = display_name(str(checkout))[:200]
    lines = worktree_git(checkout, 'rev-parse', '--path-format=absolute', '--git-common-dir',
                         '--absolute-git-dir').splitlines()
    root_common = Path(runner.git('rev-parse', '--path-format=absolute', '--git-common-dir', cwd=root).strip()).resolve()
    if len(lines) != 2:
        fail('Codex worktree {} is not a Git checkout of this repository'.format(shown))
    common, gitdir = (Path(line).resolve() for line in lines)
    if common != root_common:
        fail('Codex worktree {} belongs to a different repository (git common dir {}, expected {}); run xq from {} '
             'or unset XQ_ROOT'.format(shown, display_name(str(common))[:200],
                                       display_name(str(root_common))[:200],
                                       display_name(str(root))[:200]))
    if gitdir == common:
        return
    registered = None
    if gitdir.parent == common / 'worktrees':
        try:
            with paths.safe_open(gitdir / 'gitdir', os.O_RDONLY, 'r', encoding='utf-8', errors='replace') as stream:
                registered = Path(stream.read(4096).strip())
        except (OSError, SystemExit):
            registered = None
    try:
        expected = (Path(checkout) / '.git').resolve()
        matches = registered is not None and registered.is_absolute() and registered.resolve() == expected
    except (OSError, RuntimeError):
        matches = False
    if not matches:
        fail('Codex worktree {} has a redirected git dir {} (not under {}/worktrees); recreate it with git worktree '
             'add'.format(shown, display_name(str(gitdir))[:200], display_name(str(common))[:200]))


WORKTREE_SCAN_LIMIT = 5000


WORKTREE_STATUS_TIMEOUT = 600


def worktree_filter_overrides(checkout):
    """-c pairs blanking every filter driver the worktree's own config.worktree defines.

    A filter.<x>.clean/process there plus a `* filter=<x>` attribute would run a command during git
    status. Reading config runs nothing; the file is read even when extensions.worktreeConfig is off.
    """
    gitdir = worktree_git(checkout, 'rev-parse', '--absolute-git-dir').strip()
    config = Path(gitdir) / 'config.worktree'
    if not os.path.lexists(str(config)):
        return ()
    r = runner.run(['git', 'config', '--file', str(config), '--includes', '--name-only', '-z', '--get-regexp',
             r'^filter\.'], cwd=checkout, env=worktree_env())
    if r.returncode not in (0, 1):
        fail('cannot read filter drivers from {}: {}'.format(display_name(str(config))[:200],
                                                            r.stderr.strip()[:300]))
    names = []
    for key in r.stdout.split('\0') if r.returncode == 0 else ():
        if key.count('.') < 2:
            continue
        name = key[len('filter.'):key.rindex('.')]
        if '=' in name or '\n' in name:
            fail('Codex worktree {} defines a filter driver with an unusable name in config.worktree; remove '
                 'it (git -C {} config --worktree --remove-section filter.<name>)'.format(
                     display_name(str(checkout))[:200], shlex.quote(str(checkout))))
        if name not in names:
            names.append(name)
    pairs = []
    for name in names:
        for key, value in (('clean', ''), ('smudge', ''), ('process', ''), ('required', 'false')):
            pairs += ['-c', 'filter.{}.{}={}'.format(name, key, value)]
    return tuple(pairs)


@contextmanager
def scratch_index(checkout):
    """A fresh index read from the worktree's HEAD, in a private temporary directory.

    The worktree's own index is executor-writable: a forged entry (HEAD's blob id with the edited file's
    stat data) or skip-worktree/assume-unchanged flags hide edits from git status. A fresh index has no
    stat data and no flags, so status compares every tracked file's content with HEAD.
    """
    directory = tempfile.mkdtemp(prefix='xq-index-')
    try:
        index = os.path.join(directory, 'index')
        has_head = worktree_git(checkout, 'rev-parse', '--verify', '--quiet', 'HEAD^{commit}', check=False).strip()
        worktree_git(checkout, 'read-tree', *(['HEAD'] if has_head else ['--empty']), index=index)
        yield index
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def worktree_status(checkout, limit=WORKTREE_SCAN_LIMIT, index=None, config=()):
    """(HEAD, tracked dirty paths, untracked paths, truncated, deleted) from NUL-separated porcelain v2.

    -z keeps executor-chosen names unquoted (a quoted ".github/..." would hide the directory), and
    -uall lists files inside untracked directories instead of collapsing them. deleted is the set of
    tracked paths missing from the worktree.
    """
    fields = worktree_git(checkout, 'status', '--porcelain=v2', '-z', '--branch', '--untracked-files=all',
                          '--ignore-submodules=none', index=index, config=config,
                          timeout=WORKTREE_STATUS_TIMEOUT).split('\0')
    head, tracked, untracked, deleted = '', [], [], set()
    position = 0
    while position < len(fields):
        field = fields[position]
        position += 1
        if field.startswith('# branch.oid '):
            head = field[len('# branch.oid '):]
        elif field.startswith('? '):
            untracked.append(field[2:])
        elif field[:2] in ('1 ', '2 ', 'u '):
            parts = field.split(' ', {'1': 8, '2': 9, 'u': 10}[field[0]])
            if len(parts) == {'1': 9, '2': 10, 'u': 11}[field[0]]:
                tracked.append(parts[-1])
                if field[0] == '1' and parts[1][1:] == 'D':
                    deleted.add(parts[-1])
            if field[0] == '2':
                position += 1  # The rename source follows as its own field.
    truncated = len(tracked) + len(untracked) > limit
    return head, tracked[:limit], untracked[:max(0, limit - len(tracked))], truncated, deleted


def ls_files_stream(checkout, args, keep, limit=WORKTREE_SCAN_LIMIT, index=None):
    """Stream NUL-separated `git ls-files -z <args>` in a worktree; keep(entry) returns the item to collect or None.

    Bounded by the collected items (not the entries read): (items, truncated), or (None, False) on failure.
    """
    argv = ['git', *WORKTREE_GIT_CONFIG, 'ls-files', '-z', *args]
    try:
        process = open_process(argv, cwd=checkout, env=worktree_env(index))
    except OSError:
        return None, False
    items, pending, truncated = [], b'', False
    try:
        while True:
            chunk = process.stdout.read(65536)
            if not chunk:
                break
            *complete, pending = (pending + chunk).split(b'\0')
            for entry in complete:
                item = keep(entry.decode('utf-8', errors='replace'))
                if item is not None:
                    items.append(item)
            if len(items) > limit:
                truncated = True
                process.kill()
                break
    finally:
        process.stdout.close()
        code = process.wait()
    if code and not truncated:
        return None, False
    return items[:limit], truncated


def ignored_paths(checkout, limit=WORKTREE_SCAN_LIMIT, pathspecs=(), index=None):
    """Ignored files in a worktree (each file, not collapsed directories), bounded; (paths, truncated).

    With index (a scratch index of HEAD), an ignored file staged but not committed is listed too.
    """
    args = ['-o', '-i', '--exclude-standard'] + (['--'] + list(pathspecs) if pathspecs else [])
    return ls_files_stream(checkout, args, lambda entry: entry, limit, index=index)


def index_hidden_entry(entry):
    """(path, tag) of an `ls-files -v` entry flagged skip-worktree or assume-unchanged, else None.

    'S' is skip-worktree; a lowercase tag is assume-unchanged (h, s, ...): both hide edits from git status.
    """
    tag, separator, path = entry.partition(' ')
    if separator and len(tag) == 1 and (tag == 'S' or tag.islower()):
        return path, tag
    return None


def index_flags(checkout, paths):
    """{path: tag} for the given paths flagged in the worktree's own index, or None when it cannot be listed.

    Only labels the fresh-index scan: an edit is found whatever the worktree index says.
    """
    wanted = set(paths)
    if not wanted:
        return {}

    def keep(entry):
        found = index_hidden_entry(entry)
        return found if found and found[0] in wanted else None

    items, _ = ls_files_stream(checkout, ['-v'], keep, limit=len(wanted))
    return None if items is None else dict(items)


def private_state_path(path, private):
    """True when path is xq's or an adapter's own state in the primary checkout (private: private_paths(root))."""
    rooted = '/' + path
    return any(rooted == entry or (entry.endswith('/') and rooted.startswith(entry)) for entry in private)


INTERPRETER_HOOKS = ('*.pth', 'sitecustomize.py', 'usercustomize.py')
# Installed dependency trees (the lead prepares them in the worktree). Their manifests, Makefiles and
# configs do not run during the lead's checks, so only interpreter start-up hooks inside them are reported,
# minus the two every virtualenv/setuptools install creates.
DEPENDENCY_DIRS = ('node_modules', 'bower_components', '.venv', 'venv', 'site-packages', '__pypackages__',
                   '.tox', '.nox', 'vendor')
STANDARD_INTERPRETER_HOOKS = frozenset(('_virtualenv.pth', 'distutils-precedence.pth'))


def ignored_scan_pathspecs():
    """(outside dependency trees, interpreter hooks inside them) for git ls-files -o -i."""
    outside = ['.'] + [':(exclude,glob,icase)**/{}/**'.format(name) for name in DEPENDENCY_DIRS]
    inside = [':(glob,icase)**/{}/**/{}'.format(name, hook) for name in DEPENDENCY_DIRS for hook in INTERPRETER_HOOKS]
    return outside, inside


def worktree_sensitive(root, task, scan, brief):
    """SENSITIVE uncommitted, untracked and ignored paths in a Codex worktree, each with its kind as a suffix.

    Ignored files outside dependency trees get the full rules plus interpreter hooks; inside them only
    non-standard interpreter hooks count. An ignored listing that fails or stops at the cap adds a hit, so
    the pass needs an explicit --ack-sensitive.
    """
    tid, checkout = task['id'], scan['checkout']
    hits = ['{} (uncommitted)'.format(path) for path in scan['modified'] if sensitive_path(path, tid, brief)]
    hits += ['{} (untracked)'.format(path) for path in scan['untracked'] if sensitive_path(path, tid, brief)]
    # Skip-worktree/assume-unchanged entries hide local edits from git status (an edited conftest.py runs anyway).
    hits += ['{} (index-hidden)'.format(path) for path in scan.get('hidden', ()) if sensitive_path(path, tid, brief)]
    if scan['truncated']:
        print('NOTE {}: status listing stopped after {} entries; inspect git -C {} status'.format(
            tid, WORKTREE_SCAN_LIMIT, shlex.quote(checkout)), file=sys.stderr)
    # A task forced into the primary checkout gets no exemption: it could have written xq's own state.
    primary = same_worktree(checkout, str(root)) and not task.get('primary_checkout')
    private = seams.private_paths(root) if primary else ()
    outside, inside = ignored_scan_pathspecs()
    with scratch_index(checkout) as index:
        listings = [(ignored_paths(checkout, pathspecs=pathspecs, index=index), dependency)
                    for pathspecs, dependency in ((outside, False), (inside, True))]
    for (ignored, truncated), dependency in listings:
        where = 'in dependency directories' if dependency else 'outside dependency directories'
        if ignored is None:
            print('WARN {}: cannot list ignored files {} in {}; inspect git -C {} status --ignored'.format(
                tid, where, display_name(checkout), shlex.quote(checkout)), file=sys.stderr)
            hits.append('<ignored files {} not listed> (ignored)'.format(where))
            continue
        for path in ignored:
            if primary and private_state_path(path, private):
                continue
            name = Path(path).name.casefold()
            hook = any(fnmatch.fnmatchcase(name, p) for p in INTERPRETER_HOOKS)
            if dependency:
                if hook and name not in STANDARD_INTERPRETER_HOOKS:
                    hits.append('{} (ignored)'.format(path))
            elif hook or sensitive_path(path, tid, brief):
                hits.append('{} (ignored)'.format(path))
        if truncated:
            print('NOTE {}: ignored-file scan {} stopped after {} entries; inspect git -C {} status --ignored'.format(
                tid, where, WORKTREE_SCAN_LIMIT, shlex.quote(checkout)), file=sys.stderr)
            hits.append('<ignored files {} beyond the first {} not checked> (ignored)'.format(
                where, WORKTREE_SCAN_LIMIT))
    return hits


def sparse_absent(checkout, path):
    try:
        os.lstat(os.path.join(str(checkout), path))
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def worktree_scan(root, task, context='finish'):
    """Status of an executor worktree against a scratch index of its HEAD (the worktree index is untrusted).

    Tracked paths the worktree index flags skip-worktree/assume-unchanged are labelled (index-hidden);
    a skip-worktree path absent from disk is a sparse-checkout exclusion, not a change.
    """
    checkout = existing_worktree(task, root, context)
    same_repository(root, checkout)
    config = worktree_filter_overrides(checkout)
    with scratch_index(checkout) as index:
        head, tracked, untracked, truncated, deleted = worktree_status(checkout, index=index, config=config)
    flags = index_flags(checkout, tracked)
    if flags is None:
        print('WARN {}: cannot list index flags in {}; inspect git -C {} ls-files -v'.format(
            task.get('id') or '-', display_name(checkout), shlex.quote(checkout)), file=sys.stderr)
        flags = {}
    sparse = [path for path in tracked
              if path in deleted and flags.get(path) == 'S' and sparse_absent(checkout, path)]
    hidden = [path for path in tracked if path in flags and path not in sparse]
    modified = [path for path in tracked if path not in flags]
    # A file staged in the worktree index but not in HEAD is uncommitted work, not an untracked leftover
    # (the worktree index is only trusted here to add dirt, never to hide it).
    wanted = set(untracked)
    staged, _ = ls_files_stream(checkout, ['--cached'], lambda entry: entry if entry in wanted else None,
                                limit=len(wanted)) if wanted else ([], False)
    if staged is None:
        print('WARN {}: cannot list staged files in {}; untracked paths are treated as uncommitted'.format(
            task.get('id') or '-', display_name(checkout)), file=sys.stderr)
        staged = untracked
    staged = set(staged)
    modified += [path for path in untracked if path in staged]
    untracked = [path for path in untracked if path not in staged]
    return dict(checkout=checkout, head=head, modified=modified, untracked=untracked, truncated=truncated,
                hidden=hidden, hidden_labels=['{} (index-hidden)'.format(path) for path in hidden],
                sparse=sparse)


def untracked_leftovers(root, task, scan):
    """True when the scan's only dirt is untracked paths past a commit that moves beyond the assignment base:
    leftovers (caches, build output) that are not part of the delivery."""
    base, head = task.get('base'), scan['head']
    return (not scan['modified'] and not scan.get('hidden') and bool(base) and head != base and
            ancestor(base, head, root))


def codex_delivery(root, task, context='finish'):
    """Return head (clean commit past base) or delivery_base (dirty or no-op), dirty paths and the scan."""
    scan = worktree_scan(root, task, context)
    checkout, head = scan['checkout'], scan['head']
    if not SHA_RE.fullmatch(head):
        fail('Codex worktree needs an initial commit')
    hidden = scan.get('hidden_labels', [])
    paths = scan['modified'] + hidden + scan['untracked']
    dirty = bool(paths) or scan['truncated']
    # Untracked leftovers (caches, build output) do not change a commit that already
    # moves past the assignment base; uncommitted new work at the base still does, and so does
    # any index-hidden (skip-worktree/assume-unchanged) path, whose edits git status never shows.
    base = task.get('base')
    if paths and untracked_leftovers(root, task, scan):
        print('WARN {} has untracked paths not in the delivery: untrusted executor-created paths: {}'.format(
            checkout, listed_paths(paths)), file=sys.stderr)
        dirty = False
    if not dirty and base and head == base:
        # Codex committed nothing: leave the delivery unpinned so the lead commits it and verifies --head.
        return dict(delivery_base=head, dirty_paths=[], scan=scan)
    return {'delivery_base' if dirty else 'head': head, 'dirty_paths': paths if dirty else [], 'scan': scan}


def codex_scope_paths(root, task, base, tip, scan=None):
    """Paths a Codex delivery changes: committed base..tip (the sensitive review range) less what the default
    branch already holds, plus the worktree scan's uncommitted, index-hidden and untracked paths; None when the
    base is unknown here."""
    if not base or not tip or not commit_known(root, base):
        return None
    main = known_main(root, task.get('main')) or stale_base_main(root)
    fork = runner.git('merge-base', tip, 'refs/heads/' + main, check=False, cwd=root).strip() if main else ''
    if SHA_RE.fullmatch(fork) and fork != base and ancestor(base, fork, root):
        base = fork  # main merged or rebased in mid-task: its changes are not the delivery's.
    paths = set(runner.git('diff', '--name-only', '-z', '--no-renames', base, tip, '--', cwd=root).split('\0'))
    for key in ('modified', 'hidden', 'untracked'):
        paths.update((scan or {}).get(key, ()))
    paths.discard('')
    return sorted(paths)


def codex_done_report(root, task, found, out=None):
    """WARN SENSITIVE lines for a Codex 'done' before the lead runs any check, plus the unpinned next step.

    With out (a list), the lines are collected instead of printed, so a retried poll prints them once.
    """
    collected = [] if out is None else out
    tid = task['id']
    brief = task_brief(root, task)
    tip = found.get('head') or found.get('delivery_base')
    base = sensitive_review_base(root, task, tip) if tip else task.get('review_base') or task.get('base')
    lines = []
    if base and tip and tip != base and not runner.run(['git', 'cat-file', '-e', base + '^{commit}'], cwd=root).returncode:
        cache = {}
        committed = sensitive_paths(root, base, tip, tid, brief=brief, cache=cache)
        lines += [sensitive_warning(path, cache[(base, tip)][1]) for path in committed]
    lines += [sensitive_warning(path) for path in worktree_sensitive(root, dict(task, id=tid), found['scan'], brief)]
    collected.append((lines, tid + ': '))
    collected.append((scope_findings(root, task, codex_scope_paths(root, task, base, tip, found['scan']))[1], ''))
    if 'head' not in found and found['dirty_paths']:
        checkout = found['scan']['checkout']
        collected.append((['WARN {}: delivery is uncommitted in {} ({}); review, commit the named files '
                           '(git -C {} status), then xq.py verify --id {} --by <reader> --verdict pass --head <sha> '
                           '--evidence ...'.format(tid, display_name(checkout), listed_paths(found['dirty_paths']),
                                                   shlex.quote(checkout), tid)], ''))
    if out is None:
        print_reports(collected)


def delivery_fields(root, task, report=False, out=None):
    found = codex_delivery(root, task)
    if report:
        codex_done_report(root, task, found, out)
    return {key: found[key] for key in ('head', 'delivery_base') if key in found}


def removed_worktree_head(root, t, ref):
    if ref.startswith('-') or runner.run(['git', 'rev-parse', '--verify', '--quiet', ref + '^{commit}'],
                                  cwd=root).returncode:
        fail('worktree "{}" no longer exists and --head {} is not a commit in the primary checkout; fetch or '
             'recreate the delivery commit, or {}'.format(display_name(t['worktree']),
                                                       display_name(ref[:120]),
                                                       VERIFY_REJECT_STEP.format(t.get('id') or '<id>')))
    return resolve_commit(root, ref)
