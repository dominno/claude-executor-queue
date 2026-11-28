"""CLI commands that set a repository up and record work: doctor, preflight, init, import, assign,
brief-lint and compose."""

import json
import os
from pathlib import Path
import re
import sys

from ...adapters import private_paths
from ...core import ledger, paths
from ...core.brief import compose_out_refusal, compose_prompt, file_sha256, lint_brief, read_brief_text
from ...core.capacity import preflight_status
from ...core.config import brief_lint_settings, config
from ...core.importer import import_mapping, import_plan, jsonl_lines
from ...core.ledger import append, append_many, exclude_private, ledger_mutation, ledger_path
from ...core.paths import DEFAULT_LEDGER, ledger_committed, ledger_relative, safe_path
from ...core.repo import brief_relative, checked_worktree, default_branch, origin_default
from ...core.review import inherited_review_base, stale_base_check, stale_base_finding
from ...core.state import check_id_collision, JOB_RE, valid_id
from ...core.util import display_name, EXECUTORS, fail, LedgerConflict, LOCAL_EXECUTOR_LIMITS, OPTION_RE, STATUS_DIR
from ...port import runner
from ...port.adapter import adapter_for, ADAPTERS


def preflight_worktree(root, a):
    """preflight --worktree: the stale-base admission for a worktree about to be launched; a reason when refused."""
    path = Path(a.worktree)
    if not path.is_absolute() or not path.is_dir():
        fail('--worktree "{}" must be an existing absolute directory'.format(display_name(a.worktree)[:200]))
    worktree = str(path.resolve())
    adapter = ADAPTERS[a.executor]
    if not adapter.worktree_delivery:
        fail('--worktree applies only to an executor whose delivery is a worktree commit')
    refusals, warnings = adapter.preflight_worktree(root, worktree)
    for warning in warnings:
        print('WARN ' + warning)
    if refusals:
        return refusals
    base = adapter.worktree_base(root, worktree)
    mode, message = stale_base_finding(root, worktree, base)
    if message and mode == 'refuse':
        return [message]
    if message:
        print('WARN ' + message)
    return []


def cmd_preflight(a):
    root = ledger.repo_root()
    reasons, details = preflight_status(root, list(ledger.state(root).values()), config(root))
    for detail in details:
        print(detail)
    refused = reasons[a.executor] + (preflight_worktree(root, a) if a.worktree else [])
    note = ADAPTERS[a.executor].runner_note(root)
    if note:
        print(note)
    print('{} preflight {}{}'.format(a.executor, 'refused' if refused else 'OK',
                                    ': ' + '; '.join(refused) if refused else ''))
    return 1 if refused else 0


@ledger_mutation
def cmd_init(a, root):
    """Track briefs/status by default; ignore only ledger/lock and agy output.

    --local excludes briefs/status via git info/exclude (disables cloud handoff).
    """
    for folder in ('.agents/briefs', STATUS_DIR, '.agents/review'):
        safe_path(root / folder)
        (root / folder).mkdir(parents=True, exist_ok=True)
    ledger = ledger_path(root)
    safe_path(ledger)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with paths.safe_open(ledger, os.O_RDWR | os.O_CREAT):
        pass
    exclude_private(root, a.local)
    if ledger_committed(config(root)):
        print('ledger {} is outside .agents/: not excluded from Git (commit it as project history)'.format(
            ledger.relative_to(root).as_posix()))
    print('created {}; {}'.format(root / '.agents', 'local-only; cloud handoff disabled' if a.local
                                 else 'briefs/status must be committed for cloud use'))
    return 0


@ledger_mutation
def cmd_import(a, root):
    """Append an external JSONL ledger's lines as read-only 'import' history (idempotent by line sha256)."""
    mapping = import_mapping(a.map, config(root))
    source = Path(a.source).expanduser().resolve()
    new, problems, skipped = import_plan(jsonl_lines(source), str(source), mapping, ledger.events(root))
    for number, reason in problems:
        print('xq: {} line {}: {}; skipped'.format(source, number, reason), file=sys.stderr)
    if new and not a.dry_run:
        append_many(root, new)
    print('{} {} line(s) from {} ({} already imported, {} malformed)'.format(
        'would import' if a.dry_run else 'imported', len(new), source, skipped, len(problems)))
    return 1 if problems else 0


def check_assign_id(a, rows, known):
    """Refuse an id that collides, was reserved by an earlier assignment, or is still running; returns the
    task recorded under it (empty when new)."""
    check_id_collision(a.id, known)
    if known.get(a.id, {}).get('imported'):
        fail('{} is imported history (read-only); assign new work under a fresh id'.format(a.id))
    follows = getattr(a, 'follows', None)
    if follows and known.get(follows, {}).get('imported'):
        fail('--follows {}: imported history has no recorded worktree or base; assign without --follows'.format(
            follows))
    reserved = next((adapter_for(row.get('executor')).reserved_id_refusal for row in rows
                     if row.get('event') == 'assign' and adapter_for(row.get('executor')).reserved_id_refusal and
                     row['id'].casefold() == a.id.casefold()), None)
    if reserved:
        fail(reserved.format(a.id))
    previous = known.get(a.id, {})
    if previous.get('status') == 'running':
        label = 'executor {}{}'.format(previous.get('executor') or '-', ', job ' + previous['job'] if isinstance(
            previous.get('job'), str) and JOB_RE.fullmatch(previous['job']) else '')
        if previous.get('closed'):
            fail('{0} is closed but still running ({1}); once the executor has stopped: xq.py finish --id {0} '
                 '--status failed, then assign again'.format(a.id, label))
        fail('{0} is already running ({1}); next: xq.py poll --id {0} or finish --id {0} --status '
             'done|failed|blocked; for a new attempt use a fresh id with --follows {0}'.format(a.id, label))
    return previous


def brief_argument(root, value, what='brief'):
    """A --brief/--prompt path: relative to the primary checkout or absolute; must be a file."""
    brief = Path(value)
    if not brief.is_absolute():
        brief = root / brief
    if not brief.is_file():
        fail(what + ' not found: ' + str(brief))
    return brief


def print_lint(errors, warnings, stream=None, prefix=''):
    for line in ['ERROR ' + e for e in errors] + ['WARN ' + w for w in warnings]:
        print(prefix + line, file=stream or sys.stdout)


def assign_lint(root, brief, force):
    """brief_lint at assign: off is silent, warn prints to stderr, enforce refuses errors unless --force."""
    settings = brief_lint_settings(config(root), at_assign=True)
    if settings['mode'] == 'off':
        return
    errors, warnings = lint_brief(read_brief_text(brief), settings)
    if settings['mode'] == 'enforce' and errors and not force:
        print_lint(errors, warnings, sys.stderr, 'brief-lint: ')
        fail('brief-lint refused {} ({} error(s); brief_lint mode is enforce): fix the brief, or pass --force to '
             'bypass (recorded as force in the ledger)'.format(brief, len(errors)))
    print_lint([], ['brief ' + e for e in errors] + warnings, sys.stderr, 'brief-lint: ')


def assign_brief(a, root):
    """The absolute brief path; also checks --model/--effort."""
    brief = brief_argument(root, a.brief)
    for option in ('model', 'effort'):
        value = getattr(a, option, None)
        if value is not None and not OPTION_RE.fullmatch(value):
            fail('--{} must be 1-64 characters of letters, digits, dot, underscore, colon or hyphen'.format(option))
    return brief


def assign_event(a, brief, worktree, base, primary, review_base):
    event = dict(event='assign', id=a.id, executor=a.executor, brief=str(brief.resolve()),
                 note=a.note or '', job=a.job, worktree=worktree, queued=a.queued, force=a.force)
    for option in ('model', 'effort'):
        if getattr(a, option, None) is not None:
            event[option] = getattr(a, option)
    if base:
        event['base'] = base
    if primary:
        event['primary_checkout'] = True
    if getattr(a, 'follows', None):
        event['follows'] = a.follows
    if review_base and review_base != event.get('base'):
        event['review_base'] = review_base
    return event


@ledger_mutation
def cmd_assign(a, root):
    adapter = ADAPTERS[a.executor]
    if adapter.assign_refusal:
        fail(adapter.assign_refusal)
    valid_id(a.id)
    rows = ledger.events(root)
    known = ledger.state(root, rows=rows)
    previous = check_assign_id(a, rows, known)
    ran_before_promote = adapter.check_assign_artifacts(root, a, previous)
    # An explicitly retired (closed) attempt may be reassigned without a verdict.
    if (previous.get('status') in ('done', 'failed', 'blocked') and not previous.get('closed') and
            not previous.get('verdict') and not a.force):
        fail(a.id + ' is finished but unverified; verify it or use --force to start a new attempt')
    adapter.check_assign_launch(a)
    if a.job and not a.worktree:
        fail('--job requires --worktree')
    if a.job and a.queued:
        fail('--queued cannot be combined with --job')
    launched = adapter.launch_suffix(a)
    worktree, base, primary = None, None, False
    if a.worktree:
        try:
            worktree = checked_worktree(root, a)
            base, primary = adapter.assign_worktree(root, a, known, previous, worktree)
            # The worktree's current HEAD, not a base a queued entry pinned: a rebase since then clears it.
            stale_base_check(root, worktree, adapter.worktree_base(root, worktree) or base, a.force)
        except LedgerConflict:
            raise
        except SystemExit as exc:
            raise SystemExit(str(exc) + launched) from None
    brief = assign_brief(a, root)
    prompt = brief_argument(root, a.prompt, 'prompt') if getattr(a, 'prompt', None) else None
    assign_lint(root, brief, a.force)
    review_base = inherited_review_base(a, root, known, previous, worktree, base)
    event = assign_event(a, brief, worktree, base, primary, review_base)
    event.update(adapter.assign_event_fields(root, a))
    if prompt:
        event.update(prompt=str(prompt.resolve()), prompt_sha256=file_sha256(prompt))
    append(root, event)
    if ran_before_promote:
        return adapter.import_promoted_run(root, a.id)
    note = adapter.runner_note(root) if a.queued else None
    if note:
        print(note)
    return 0


def cmd_brief_lint(a):
    root = ledger.repo_root()
    brief = brief_argument(root, a.brief)
    settings = brief_lint_settings(config(root))
    errors, warnings = lint_brief(read_brief_text(brief), settings)
    failed = bool(errors or (a.enforce and warnings))
    if a.json:
        print(json.dumps(dict(version=1, brief=str(brief), ok=not failed, errors=errors, warnings=warnings)))
    else:
        print_lint(errors, warnings)
        print('brief-lint {}: {} error(s), {} warning(s){}'.format(
            'failed' if failed else 'OK', len(errors), len(warnings), ' (--enforce: warnings fail)' if a.enforce else ''))
    return 1 if failed else 0


def cmd_compose(a):
    root = ledger.repo_root()
    brief = brief_argument(root, a.brief)
    name = brief_relative(root, brief) or str(brief)
    text = compose_prompt(root, read_brief_text(brief), name, config(root).get('context_files', []),
                          a.context or ())
    out = Path(a.out) if a.out else root / '.agents/review' / (brief.stem + '.prompt.md')
    out = out if out.is_absolute() else root / out
    refusal = compose_out_refusal(root, out)
    if refusal:
        fail(refusal)
    safe_path(out.parent)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with paths.safe_open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 'w', encoding='utf-8') as stream:
            stream.write(text)
    except OSError as exc:
        fail('cannot write composed prompt {}: {}'.format(out, exc))
    print(out)
    return 0


def doctor_needs(a):
    """The executors doctor checks: --need (validated) or all."""
    requested = [name.strip() for name in a.need.split(',')] if a.need is not None else list(EXECUTORS)
    unknown = [name for name in requested if name not in EXECUTORS]
    if unknown:
        fail('--need accepts {}; unknown: {}'.format(','.join(EXECUTORS), ', '.join(display_name(n)[:40]
                                                                                for n in unknown)))
    return set(requested)


def doctor_capacity(root, needed, settings):
    """Print the preflight lines of the requested local executors."""
    local = [ex for ex in LOCAL_EXECUTOR_LIMITS if ex in needed]
    if not local:
        return
    capacity, details = preflight_status(root, list(ledger.state(root).values()), settings)
    others = tuple(ex + suffix for ex in LOCAL_EXECUTOR_LIMITS if ex not in needed
                   for suffix in (' running', ' counted:', ' live:'))
    for detail in details:
        if not others or not detail.startswith(others):
            print(detail)
    for ex in local:
        if capacity[ex]:
            print(ex + ' preflight refused: ' + '; '.join(capacity[ex]))


def doctor_repository(root, check):
    """Git version, HEAD, default branch and ignored private paths."""
    version = runner.run(['git', '--version'], cwd=root)
    match = re.search(r'git version (\d+)\.(\d+)([^\s]*)', version.stdout)
    print('git version: ' + (match.group(0)[len('git version '):] if match else 'unknown'))
    if match and tuple(map(int, match.group(1, 2))) < (2, 31):
        print('WARN git < 2.31: primary-checkout recipes require Git >= 2.31')
    has_head = runner.run(['git', 'rev-parse', '--verify', 'HEAD'], cwd=root).returncode == 0
    check('git HEAD', has_head, 'present' if has_head else 'no commit', 'commit an initial HEAD')
    try:
        branch = default_branch(root)
    except SystemExit as exc:
        check('default branch', False, str(exc))
    else:
        label = 'default branch' if origin_default(root) else 'default branch (local HEAD fallback, origin/HEAD unset)'
        print(label + ': ' + branch)
    configured = ledger_relative(config(root))
    print('ledger: {}{}'.format(configured, ' (default)' if configured == DEFAULT_LEDGER else ' (config "ledger")'))
    for path in private_paths(root):
        probe = path.lstrip('/') + ('probe' if path.endswith('/') else '')
        ignored = runner.run(['git', 'check-ignore', '-q', probe], cwd=root).returncode == 0
        tracked = runner.git('ls-files', '--', path.strip('/'), cwd=root).strip()
        check(path, ignored and not tracked,
              'ignored and untracked' if ignored and not tracked else ('tracked' if tracked else 'not ignored'),
              'run xq init; remove tracked private state from the index')


def doctor(a):
    root = ledger.repo_root()
    needed = doctor_needs(a)
    ok = True
    plugin_missing = False

    def check(name, good, detail, fix=None, plugin=False):
        nonlocal ok, plugin_missing
        ok = ok and bool(good)
        plugin_missing = plugin_missing or (plugin and not good)
        if good:
            print('OK {}: {}'.format(name, detail))
        else:
            print('MISSING {}: {}{}'.format(name, detail, ' -> next: ' + fix if fix else ''))

    settings = config(root)
    doctor_capacity(root, needed, settings)
    doctor_repository(root, check)
    for name, adapter in ADAPTERS.items():
        if name in needed:
            adapter.doctor_checks(root, a, check, settings)
        else:
            print('SKIP {} (not requested)'.format(name))
    if plugin_missing:
        print('Plugin marketplace fallback: git clone <plugin-source> <clone>; /plugin marketplace add <clone>, install the plugin, then rerun doctor.')
    return 0 if ok else 1
