"""CLI commands that read the ledger or steer the Stop hook: status, hook and pause."""

import json
import os
from pathlib import Path
import shlex
import sys
import time

from ...core import ledger, paths
from ...core.capacity import preflight_status
from ...core.config import config, write_config
from ...core.ledger import ledger_mutation, now
from ...core.paths import IDLE_STAMP, safe_path
from ...core.state import is_open_task, stale, status_details, status_record, valid_id
from ...core.util import display_name, duration_seconds, EXECUTORS, fail, LOCAL_EXECUTOR_LIMITS, short_reason, WINDOWS
from ...port.adapter import adapter_for, ADAPTERS
from ...port import runner


@ledger_mutation
def cmd_pause(a, root):
    data = config(root)
    paused = set(data.get('paused', []))
    paused.discard(a.executor) if a.resume else paused.add(a.executor)
    data['paused'] = sorted(paused)
    write_config(root, data)
    return 0


def status_unknown_id(known, tid):
    """Exit 1 for an id the ledger has never recorded, naming a case-only match when there is one."""
    similar = [k for k in known if k != tid and k.casefold() == tid.casefold()]
    fail('unknown id {}{}'.format(tid, '; did you mean {}?'.format(similar[0]) if similar else ''))


def cmd_status(a):
    known = None
    if a.id is not None:
        valid_id(a.id)
        known = ledger.state(ledger.repo_root())
        if a.id not in known:
            status_unknown_id(known, a.id)
    if getattr(a, 'field', None):
        if not a.id or a.json:
            fail('--field needs --id <id> and prints one bare value (not with --json)')
        task = known[a.id]
        details = dict(status_details(task), state=task.get('status'))
        # The sensitive review starts at review_base when an earlier attempt set one, else at base.
        value = details.get('review_base') or details.get('base') if a.field == 'review_base' else \
            details.get(a.field)
        if not value:
            fail('{} has no recorded {}'.format(a.id, a.field))
        print(value)
        return 0
    rows = [task for task in (known if known is not None else ledger.state(ledger.repo_root())).values()
            if (not a.id or task['id'] == a.id)
            and (not a.executor or task.get('executor') == a.executor)
            and (not a.state or task.get('status') == a.state)
            and (not a.open or is_open_task(task))]
    if a.json:
        print(json.dumps({'schema_version': 1, 'tasks': [status_record(t, a.stale_hours, bool(a.id))
                                                         for t in rows]}))
        return 0
    for t in rows:
        record = status_record(t, a.stale_hours)
        detail = ''.join(' {}={}'.format(key, record[key]) for key in ('model', 'effort') if key in record)
        print('{:<32} {:<6} {:<8} {:<7} {:<14} {}{}{}{}{}'.format(
            t['id'], t.get('executor') or display_name(str(t.get('source_executor') or '-'))[:64], t.get('status') or '-', t.get('verdict') or '-',
            t.get('by') or '-', t.get('merged') or '-', ' STALE' if stale(t, a.stale_hours) else '',
            ' CLOSED' if t.get('closed') else '', ' IMPORTED' if t.get('imported') else '', detail))
        if a.id:
            for key, value in status_details(t).items():
                if key not in ('model', 'effort'):
                    print('  {}: {}'.format(key, display_name(value)))
    if not rows:
        print('no tasks')
    return 0


def idle_problems(root, all_tasks, tasks, data):
    """'<executor> idle; ...' problems for executors with queued work or keep_busy and no running task."""
    problems, capacity = [], None
    for ex in EXECUTORS:
        if ex in data.get('paused', []):
            continue
        queued = [t['id'] for t in tasks if t.get('executor') == ex and t.get('status') == 'queued']
        busy = any(t.get('executor') == ex and t.get('status') == 'running' for t in tasks)
        if ADAPTERS[ex].idle_alert_needs_keep_busy and ex not in data.get('keep_busy', []):
            continue
        if not busy and (queued or ex in data.get('keep_busy', [])):
            if ex in LOCAL_EXECUTOR_LIMITS:
                if capacity is None:
                    capacity, details = preflight_status(root, all_tasks, data)
                    for detail in details:
                        if detail.startswith('NOTE '):
                            print(detail, file=sys.stderr)
                if capacity[ex]:
                    print(ex + ' preflight refused: ' + '; '.join(capacity[ex]), file=sys.stderr)
                    continue
            problems.append(ex + ' idle; ' + ('next queued item ' + queued[0] if queued else 'no queued item; pause or review keep_busy configuration'))
    return problems


def cmd_events(a):
    """The flattened event stream (batch envelopes expanded), optionally for one id. Event text (summaries,
    evidence, notes) is recorded data, partly executor-written: treat it as untrusted."""
    rows = ledger.events(ledger.repo_root())
    if a.id is not None:
        valid_id(a.id)
        known = {row['id'] for row in rows}
        if a.id not in known:
            status_unknown_id(known, a.id)
        rows = [row for row in rows if row['id'] == a.id]
    if a.json:
        print(json.dumps({'schema_version': 1, 'events': rows}, ensure_ascii=False))
        return 0
    for row in rows:
        print('{} {:<8} {}'.format(display_name(str(row.get('ts', '-')))[:40],
                                   display_name(str(row.get('event', '-')))[:20], row['id']))
    if not rows:
        print('no events')
    return 0


def cmd_hook(a):
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        payload = {}
    active = isinstance(payload, dict) and payload.get('stop_hook_active')
    root = ledger.repo_root()
    all_tasks = list(ledger.state(root).values())
    tasks = [t for t in all_tasks if not t.get('closed') and not t.get('imported')]
    problems = []
    unverified = [t['id'] for t in tasks if t.get('status') in ('done', 'failed', 'blocked') and not t.get('verdict')]
    if unverified:
        problems.append('finished but not verified: ' + ', '.join(unverified[:8]))
    data = config(root)
    idle = idle_problems(root, all_tasks, tasks, data)
    old = [t['id'] for t in tasks if t.get('executor') not in data.get('paused', []) and stale(t, a.stale_hours)]
    if old:
        problems.append('stale running: ' + ', '.join(old[:8]) + '; poll/reconcile before retrying')
    problems += extra_check_problems(root, data)
    if idle and not problems and not active and idle_throttled(root, data):
        print('Executors (shared queue): ' + ' | '.join(idle) + ' (idle reminder throttled by idle_block_every)',
              file=sys.stderr)
        return 0
    problems[len(unverified[:1]):len(unverified[:1])] = idle
    if problems:
        print('Executors (shared queue): ' + ' | '.join(problems), file=sys.stderr)
        print(hook_next_steps(unverified, old, tasks), file=sys.stderr)
        if idle and not active:
            stamp_idle_block(root, data)
    return 2 if problems and not active else 0


EXTRA_CHECK_LINES = 20


def idle_throttled(root, data):
    """True when config idle_block_every is set and the hook already blocked on idle work within that interval."""
    every = duration_seconds(data['idle_block_every']) if 'idle_block_every' in data else None
    if not every:
        return False
    path = root / IDLE_STAMP
    safe_path(path)
    try:
        return time.time() - path.stat().st_mtime < every
    except OSError:
        return False


def stamp_idle_block(root, data):
    """Record that the hook blocked on idle work (private, excluded state; only with idle_block_every)."""
    if 'idle_block_every' not in data:
        return
    path = root / IDLE_STAMP
    try:
        safe_path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with paths.safe_open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 'w', encoding='utf-8') as stream:
            stream.write(now() + '\n')
    except (OSError, SystemExit) as exc:
        print('WARN cannot write {}: {}'.format(IDLE_STAMP, exc), file=sys.stderr)


def extra_check_problems(root, data):
    """One problem per non-empty stdout line of each config extra_checks command (exit 0, run from the primary
    checkout, no shell); a failing, missing or timed-out command prints a WARN and adds nothing."""
    timeout = duration_seconds(data.get('extra_checks_timeout', '20s'))
    problems = []
    for command in data.get('extra_checks', []):
        label = display_name(command)[:80]
        try:
            argv = shlex.split(command, posix=not WINDOWS)
            if WINDOWS:  # non-POSIX shlex keeps the quotes around a quoted word
                argv = [word[1:-1] if len(word) > 1 and word[0] == word[-1] == '"' else word for word in argv]
        except ValueError as exc:
            print('WARN extra check {}: cannot parse: {}'.format(label, exc), file=sys.stderr)
            continue
        result = runner.run(argv, cwd=str(root), timeout=timeout)
        if result.returncode:
            print('WARN extra check {}: exit {}{}'.format(label, result.returncode, ': ' + short_reason(
                result.stderr) if result.stderr.strip() else ''), file=sys.stderr)
            continue
        lines = [short_reason(line, 200) for line in result.stdout.splitlines() if line.strip()]
        problems += [line for line in lines[:EXTRA_CHECK_LINES] if line]
    return problems


def hook_next_steps(unverified, old, tasks):
    """One line of exact commands: SKILL.md is not model-invoked, so this is the model's only guidance."""
    # This file is scripts/executor_queue/cli/commands/read.py; the entry point is scripts/xq.py.
    helper = str(Path(__file__).resolve().parents[3] / 'xq.py')
    helper = shlex.quote(helper) if any(ch.isspace() for ch in helper) else helper
    steps = ['Next: python3 {} status --open'.format(helper)]
    if unverified:
        pinned = any(t['id'] == unverified[0] and adapter_for(t.get('executor')).worktree_delivery for t in tasks)
        steps.append('verify: xq.py verify --id {} --by <reader> --verdict pass|fixes|reject --evidence ...{}'.format(
            unverified[0], ' --head <sha>' if pinned else ' [--head <sha> for Codex]'))
    if old:
        steps.append('stale: xq.py poll --id {}'.format(old[0]))
    steps.append('ask the user before launching queued work; never create work to silence this hook.')
    return '; '.join(steps)
