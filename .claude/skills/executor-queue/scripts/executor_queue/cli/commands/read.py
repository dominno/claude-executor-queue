"""CLI commands that read the ledger or steer the Stop hook: status, hook and pause."""

import json
from pathlib import Path
import shlex
import sys

from ...core import ledger
from ...core.capacity import preflight_status
from ...core.config import config, write_config
from ...core.ledger import ledger_mutation
from ...core.state import is_open_task, stale, status_details, status_record, unknown_task, valid_id
from ...core.util import display_name, EXECUTORS, fail, LOCAL_EXECUTOR_LIMITS
from ...port.adapter import adapter_for, ADAPTERS


@ledger_mutation
def cmd_pause(a, root):
    data = config(root)
    paused = set(data.get('paused', []))
    paused.discard(a.executor) if a.resume else paused.add(a.executor)
    data['paused'] = sorted(paused)
    write_config(root, data)
    return 0


def cmd_status(a):
    if a.id is not None:
        valid_id(a.id)
    if getattr(a, 'field', None):
        if not a.id or a.json:
            fail('--field needs --id <id> and prints one bare value (not with --json)')
        task = ledger.state(ledger.repo_root()).get(a.id)
        if task is None:
            unknown_task(ledger.state(ledger.repo_root()), a.id)
        details = status_details(task)
        # The sensitive review starts at review_base when an earlier attempt set one, else at base.
        value = details.get('review_base') or details.get('base') if a.field == 'review_base' else \
            details.get(a.field)
        if not value:
            fail('{} has no recorded {}'.format(a.id, a.field))
        print(value)
        return 0
    rows = [task for task in ledger.state(ledger.repo_root()).values()
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
    capacity = None
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
    old = [t['id'] for t in tasks if t.get('executor') not in data.get('paused', []) and stale(t, a.stale_hours)]
    if old:
        problems.append('stale running: ' + ', '.join(old[:8]) + '; poll/reconcile before retrying')
    if problems:
        print('Executors (shared queue): ' + ' | '.join(problems), file=sys.stderr)
        print(hook_next_steps(unverified, old, tasks), file=sys.stderr)
    return 2 if problems and not active else 0


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
