"""Task state rules: ids, branches, required task states, staleness and status records (CORE)."""

import datetime
import re

from . import ledger, seams
from .util import (EXECUTORS, fail, ID_RE, INTEGRATING_EXECUTORS, LOCAL_EXECUTOR_LIMITS, OBSERVATION_STATES,
                   OPTION_RE, SHA_RE, TASK_STATES)


def valid_id(tid):
    if not ID_RE.fullmatch(tid):
        fail('invalid task id: use 1–120 ASCII letters/digits/dot/underscore/hyphen; start with a letter/digit')


def valid_branch(name, root):
    if name.startswith('-') or seams.run(['git', 'check-ref-format', '--branch', name], cwd=root).returncode:
        fail('invalid branch ' + name)


def check_id_collision(tid, known):
    if any(k != tid and k.casefold() == tid.casefold() for k in known):
        fail('task id case collision: ' + tid)


def unknown_task(known, tid):
    similar = [k for k in known if k != tid and k.casefold() == tid.casefold()]
    fail('unknown task {}; see xq.py status{}'.format(
        tid, '; did you mean {}?'.format(similar[0]) if similar else ''))


def refuse_imported(tid, task, command):
    """Imported history is read-only: no command that observes or changes a task applies to it."""
    if task and task.get('imported'):
        fail('{} is imported history (read-only): {} does not apply; record new work under a fresh id'.format(
            tid, command))


def require_task(root, a, allowed, command, *, closed_running=False):
    valid_id(a.id)
    known = ledger.state(root)
    t = known.get(a.id)
    refuse_imported(a.id, t, command)
    if not a.force:
        if t is None:
            unknown_task(known, a.id)
        # A closed task still marked running may record its terminal evidence (frees the slot).
        if t.get('closed') and not (closed_running and t.get('status') == 'running'):
            fail(a.id + ' is closed; use --force only for explicit reconciliation')
        status = t.get('status')
        if status not in allowed:
            hints = {'running': 'xq.py poll --id {0} or finish --id {0} --status done|failed|blocked',
                     'queued': seams.adapter_for(t.get('executor')).queued_hint or
                     'promote with assign --id {0} --executor {1} --brief <brief> after launch'}
            hint = hints.get(status)
            if status in ('failed', 'blocked') and command == 'merge':
                hint = 'record verify --id {0} --verdict reject or close --id {0} --reason dropped|rejected'
            elif status in ('done', 'failed', 'blocked') and command in ('finish', 'ack'):
                hint = 'already finished; verify --id {0} ... or close --id {0} --reason ...'
            fail('{} is {} (executor {}); {} needs a {} task{}'.format(
                a.id, status, t.get('executor') or '-', command, '/'.join(allowed),
                '; next: ' + hint.format(a.id, t.get('executor') or '<executor>') if hint else ''))
    return t or {}


def parse_timestamp(value):
    """Parse zoned ISO timestamps consistently on Python 3.8+, including Z."""
    if not isinstance(value, str):
        raise ValueError('timestamp must be a string')
    value = value.replace('Z', '+00:00').replace(' ', 'T')
    # datetime stores microseconds; ISO permits comma and longer fractions.
    value = re.sub(r'[.,](\d+)', lambda m: '.' + m.group(1)[:6], value)
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y%m%dT%H%M%S', '%G-W%V-%uT%H:%M:%S'):
        for fraction in ('', '.%f'):
            try:
                return datetime.datetime.strptime(value, fmt + fraction + '%z')
            except ValueError:
                pass
    raise ValueError('invalid zoned ISO timestamp')


def stale(t, hours):
    if t.get('status') != 'running' or t.get('imported'):
        return False
    try:
        assigned = parse_timestamp(t.get('seen') or t.get('assigned', ''))
        if t.get('ack_until') and datetime.datetime.now(datetime.timezone.utc) < parse_timestamp(t['ack_until']):
            return False
        return (datetime.datetime.now(datetime.timezone.utc) - assigned).total_seconds() > hours * 3600
    except (ValueError, TypeError):
        return True


def is_open_task(task):
    return not task.get('closed') and not task.get('imported') and (
        task.get('status') in ('running', 'queued') or task.get('verdict') != 'pass' or
        (task.get('executor') in INTEGRATING_EXECUTORS and not task.get('merged')))


def occupies_slot(task):
    executor = task.get('executor')
    return isinstance(executor, str) and executor in LOCAL_EXECUTOR_LIMITS and task.get('status') == 'running'


def status_observation(task):
    observation = task.get('observation')
    if not isinstance(observation, dict):
        return None
    source, status = observation.get('source'), observation.get('state')
    if source not in EXECUTORS or source != task.get('executor') or status not in OBSERVATION_STATES:
        return None
    try:
        observed = parse_timestamp(observation.get('observed_at')).astimezone(datetime.timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None
    return {'source': source, 'state': status, 'observed_at': observed.isoformat().replace('+00:00', 'Z')}


STATUS_DETAIL_SHAS = ('base', 'review_base', 'head', 'verified_head', 'replaced_head')
STATUS_DETAIL_KEYS = ('worktree', 'base', 'review_base', 'job', 'head', 'verified_head', 'replaced_head', 'brief',
                      'model', 'effort')
JOB_RE = re.compile(r'^[A-Za-z0-9._:-]{1,200}$')


def status_details(task):
    """Recorded launch metadata for one task (status --id): paths, SHAs and the job id."""
    details = {}
    for key in STATUS_DETAIL_KEYS:
        value = task.get(key)
        if not isinstance(value, str) or not value:
            continue
        if key in STATUS_DETAIL_SHAS:
            ok = SHA_RE.fullmatch(value)
        elif key == 'job':
            ok = JOB_RE.fullmatch(value)
        elif key in ('model', 'effort'):
            ok = OPTION_RE.fullmatch(value)
        else:
            ok = len(value) <= 4096 and all(ch.isprintable() for ch in value)
        if ok:
            details[key] = value
    return details


def zoned_timestamp(value):
    """A recorded timestamp as given when it parses as a zoned ISO timestamp, else None."""
    try:
        parse_timestamp(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return value


# status --json lifecycle fields: (record key, task key), timestamps from the recording event's ts.
LIFECYCLE_TIMES = (('assigned_at', 'assigned'), ('finished_at', 'finished'), ('verified_at', 'verified_at'),
                   ('merged_at', 'merged_at'))


def lifecycle_fields(task):
    """When the attempt was assigned, finished, verified and merged, who verified it and the merge commit (None when
    not recorded); a verify that does not count (no verdict) reports no verifier."""
    record = {key: zoned_timestamp(task.get(source)) for key, source in LIFECYCLE_TIMES}
    by = task.get('by')
    record['verified_by'] = by if task.get('verdict') and isinstance(by, str) and 0 < len(by) <= 200 and \
        by.isprintable() else None
    if not task.get('verdict'):
        record['verified_at'] = None
    merged = task.get('merged')
    record['merge_commit'] = merged if isinstance(merged, str) and SHA_RE.fullmatch(merged) else None
    if record['merge_commit'] is None:
        record['merged_at'] = None
    return record


def status_record(task, stale_hours, details=False):
    # Allowlist metadata so summaries, paths, commands and evidence stay in the ledger.
    tid, executor, status = task['id'], task.get('executor'), task.get('status')
    verdict, attempt = task.get('verdict'), task.get('attempt')
    record = {
        'id': tid if ID_RE.fullmatch(tid) else None,
        'executor': executor if executor in EXECUTORS else None,
        'attempt': attempt if type(attempt) is int and attempt > 0 else None,
        'state': status if status in TASK_STATES else 'unknown',
        'verdict': verdict if verdict in ('pass', 'fixes', 'reject') else None,
        'merged': bool(task.get('merged')),
        'closed': bool(task.get('closed')),
        'stale': stale(task, stale_hours),
        'occupies_slot': occupies_slot(task),
        'observation': status_observation(task),
    }
    record.update(lifecycle_fields(task))
    if task.get('imported'):
        # Additive: history imported by xq.py import (never an executor slot, never verified).
        record['imported'] = True
        executor = task.get('source_executor')
        record['source_executor'] = executor if isinstance(executor, str) and OPTION_RE.fullmatch(executor) else None
    # Additive: present only for attempts recorded with assign --model/--effort.
    for key in ('model', 'effort'):
        value = task.get(key)
        if isinstance(value, str) and OPTION_RE.fullmatch(value):
            record[key] = value
    if details:
        record.update(status_details(task))
    return record
