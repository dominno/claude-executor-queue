"""CLI commands that consult executors: poll (with --wait), agy-sync, cloud-envelope and cloud-collect."""

import shlex
import sys
import time

from ...adapters import agy_sync
from ...adapters.agy_sync import AGY_NO_RUNNER
from ...adapters.cloud import record_cloud_reports, record_envelope, reprint_envelope
from ...core import ledger
from ...core.ledger import append, executor_observation, snapshot
from ...core.state import refuse_imported, unknown_task, valid_id
from ...core.util import fail, LedgerConflict, LOCAL_EXECUTOR_LIMITS, OBSERVATION_STATES
from ...port.adapter import adapter_for, ADAPTERS


# Module-level so tests can inject a clock for poll --wait.
sleep = time.sleep
monotonic = time.monotonic


def cmd_agy_sync(a):
    return agy_sync.sync_agy(ledger.repo_root(), delegate_exit=getattr(a, 'delegate_exit', None), out=getattr(a, 'out', None))


def poll_task(root, task_id, executor_filter=None, *, quiet=False, notes=None):
    """Refresh one recorded task through its executor's adapter (ExecutorAdapter.poll)."""
    known, stamp = snapshot(root)
    task = known.get(task_id)
    if not task:
        unknown_task(known, task_id)
    refuse_imported(task_id, task, 'poll')
    executor = task.get('executor')
    adapter = adapter_for(executor)
    if adapter.poll_refusal:
        fail(adapter.poll_refusal)
    if executor_filter and executor_filter != executor:
        fail('{} uses {}. Select that executor or omit --executor.'.format(task_id, executor))
    return adapter.poll(root, task, stamp, quiet=quiet, notes=notes)


WAIT_TERMINAL = ('done', 'failed', 'blocked')
WAIT_TIMEOUT_EXIT = 3
# Below the 10-minute Bash tool limit; repeat on exit 3.
WAIT_DEFAULT_TIMEOUT = 9 * 60
WAIT_DEFAULT_INTERVAL = 30
WAIT_MAX_CONFLICTS = 3
WAIT_MAX_UNAVAILABLE = 3


def wait_for_task(root, a):
    """Bounded poll --wait: one stderr line per state change; rows only on change."""
    task = ledger.state(root).get(a.id)
    if task is None:
        unknown_task(ledger.state(root), a.id)
    refuse_imported(a.id, task, 'poll --wait')
    adapter_for(task.get('executor')).check_wait(a.id, task)
    deadline = monotonic() + a.timeout
    last = None
    conflicts = idle_runner = unavailable = 0
    while True:
        notes = {}
        try:
            result = poll_task(root, a.id, a.executor, quiet=True, notes=notes)
            conflicts = 0
        except LedgerConflict:
            # Another writer (a concurrent poll, agy-sync or finish) changed this task mid-query.
            conflicts += 1
            if ledger.state(root).get(a.id, {}).get('status') not in WAIT_TERMINAL:
                if conflicts >= WAIT_MAX_CONFLICTS:
                    raise
                continue
            result = 0
        task = ledger.state(root).get(a.id, {})
        observed = (task.get('observation') or {}).get('state')
        current = (task.get('status'), observed)
        if current != last:
            print('{}: {}{}'.format(a.id, current[0] or '-', ' (observed {})'.format(observed)
                                    if observed and observed != current[0] else ''), file=sys.stderr)
            last = current
        transient = bool(result) and notes.get(a.id) == 'unavailable' and task.get('status') not in WAIT_TERMINAL
        unavailable = unavailable + 1 if transient else 0
        if result and not (transient and unavailable < WAIT_MAX_UNAVAILABLE):
            return 1
        if task.get('status') in WAIT_TERMINAL:
            return 0
        if adapter_for(task.get('executor')).observes_running_only and task.get('status') != 'running':
            return 0  # Nothing left to observe for this attempt.
        idle_runner = idle_runner + 1 if notes.get(a.id) == 'no-runner' else 0
        if idle_runner >= 2:
            print('PROBLEM {}: {}'.format(a.id, AGY_NO_RUNNER.format(shlex.quote(str(root)))), file=sys.stderr)
            return 1
        remaining = deadline - monotonic()
        if remaining <= 0:
            if observed in OBSERVATION_STATES and adapter_for(task.get('executor')).name in LOCAL_EXECUTOR_LIMITS:
                append(root, dict(event='observe', id=a.id,
                                  observation=executor_observation(task['executor'], observed)))
            print('TIMEOUT {}: still {} after {}; poll again later or reconcile'.format(
                a.id, task.get('status'), format_duration(a.timeout)), file=sys.stderr)
            return WAIT_TIMEOUT_EXIT
        sleep(min(a.interval, remaining))


def format_duration(seconds):
    for unit, size in (('h', 3600), ('m', 60)):
        if seconds >= size and seconds % size == 0:
            return '{}{}'.format(int(seconds // size), unit)
    return '{:g}s'.format(seconds)


def cmd_poll(a):
    if a.id is not None:
        valid_id(a.id)
    if getattr(a, 'wait', False) and a.id is None:
        fail('--wait requires --id: wait for one recorded task (poll --id <id> --wait)')
    if not getattr(a, 'wait', False) and (a.timeout is not None or a.interval is not None):
        fail('--timeout and --interval only apply with --wait (poll --id <id> --wait --timeout 9m)')
    if a.timeout is None:
        a.timeout = WAIT_DEFAULT_TIMEOUT
    if a.interval is None:
        a.interval = WAIT_DEFAULT_INTERVAL
    root = ledger.repo_root()
    if getattr(a, 'wait', False):
        return wait_for_task(root, a)
    if a.id is not None:
        return poll_task(root, a.id, a.executor)
    if a.executor:
        return ADAPTERS[a.executor].poll_all(root, explicit=True)
    # Evaluated in registry order (Codex, then agy), like the explicit per-executor polls.
    return max([adapter.poll_all(root, explicit=False) for adapter in ADAPTERS.values() if adapter.polled])


def check_envelope_ids(ids):
    for tid in ids:
        valid_id(tid)
    if len(set(ids)) != len(ids):
        fail('task ids must be distinct')
    if len({tid.casefold() for tid in ids}) != len(ids):
        fail('task id case collision')


def cmd_cloud_envelope(a):
    root = ledger.repo_root()
    ids = a.tasks
    check_envelope_ids(ids)
    if a.reprint:
        return reprint_envelope(root, a, ids)
    output = record_envelope(root, a, ids)
    print(output, flush=True)
    print('recorded {} assign events; paste as /goal'.format(len(ids)), file=sys.stderr)
    return 0


def cmd_cloud_collect(a):
    root = ledger.repo_root()
    tasks, stamp = snapshot(root)
    accepted, relevant, bad, fetch_failed = record_cloud_reports(root, tasks, stamp, a)
    for messages in bad.values():
        for message in messages:
            print(message)
    accepted_status = {row['id']: row['status'] for row in accepted}
    counts = {}
    for tid in relevant:
        status = accepted_status.get(tid, tasks[tid].get('status', 'running'))
        counts[status] = counts.get(status, 0) + 1
    print('cloud progress: ' + (', '.join('{} {}'.format(k, v) for k, v in sorted(counts.items())) or 'no cloud tasks'))
    print('Next: inspect untrusted diffs before tests; verify each task in verify-<batch>, record independent verdicts; merge accepted tasks only with user authorization.')
    return 1 if bad or fetch_failed else 0
