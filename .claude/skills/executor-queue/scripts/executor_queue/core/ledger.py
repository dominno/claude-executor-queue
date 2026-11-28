"""Ledger I/O: the JSONL file, its lock, appends, snapshots and replay into task state (CORE)."""

from contextlib import contextmanager
from copy import deepcopy
import datetime
from functools import wraps
import json
import os
from pathlib import Path
import sys
from threading import get_ident
import time

from . import paths, seams, util
from .config import config
from .paths import ledger_relative, safe_path
from .util import fail, LedgerConflict, STATUS_DIR


LOCK_WAIT_SECONDS = 120
_LOCKED = {}


def repo_root():
    if os.environ.get('XQ_ROOT'):
        return Path(os.environ['XQ_ROOT']).expanduser().resolve()
    # --git-common-dir points at the main checkout's .git in every linked worktree.
    common = Path(seams.git('rev-parse', '--git-common-dir').strip()).resolve()
    if common.name != '.git':
        fail('use XQ_ROOT for a bare repository or separate git directory')
    return common.parent


def ledger_path(root):
    """The one accessor for the ledger file; every command reads and appends through it."""
    return root / ledger_relative(config(root))


def lock_path(root):
    """The ledger lock lives next to the ledger (ledger.jsonl -> ledger.lock)."""
    return ledger_path(root).with_suffix('.lock')


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def exclude_private(root, local=False):
    exclude = Path(seams.git('rev-parse', '--git-path', 'info/exclude', cwd=root).strip())
    if not exclude.is_absolute():
        exclude = root / exclude
    safe_path(exclude)
    private = seams.private_paths(root)
    lines = list(private)
    if local:
        lines += ['/.agents/briefs/', '/' + STATUS_DIR + '/']
    tracked = seams.git('ls-files', '--', *(path.strip('/') for path in private), cwd=root).strip()
    if tracked:
        fail('private state is tracked; remove it from the index before continuing: ' + tracked)
    exclude.parent.mkdir(parents=True, exist_ok=True)
    try:
        with paths.safe_open(exclude, os.O_RDONLY, 'r', encoding='utf-8') as stream:
            existing = stream.read()
    except FileNotFoundError:
        existing = ''
    missing = [x for x in lines if x not in existing.split('\n')]
    if missing:
        with paths.safe_open(exclude, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 'a') as fh:
            fh.write(('\n' if existing and not existing.endswith('\n') else '') + '\n'.join(missing) + '\n')


def ledger_stamp(root):
    path = ledger_path(root)
    safe_path(path)
    if not path.exists():
        return None
    st = path.stat()
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def snapshot(root):
    with ledger_lock(root):
        tasks = state(root)
        return tasks, (ledger_stamp(root), deepcopy(tasks))


def append_checked(root, rows, stamp, *, dependencies=None):
    """Commit external query results if their ledger inputs still match."""
    rows = list(rows)
    if not rows:
        return
    previous_stamp, previous = stamp
    with ledger_lock(root):
        if ledger_stamp(root) != previous_stamp:
            current = state(root)
            touched = {row['id'].casefold() for row in rows}

            def affected(tasks):
                # Heartbeats and observations do not change the queried attempt.
                return {tid: {key: value for key, value in task.items()
                              if key not in ('seen', 'ack_until', 'observation') + seams.heartbeat_fields()}
                        for tid, task in tasks.items() if tid.casefold() in touched}

            if (affected(previous) != affected(current) or
                    (dependencies is not None and dependencies(previous) != dependencies(current))):
                raise LedgerConflict('xq: ledger changed during query; retry to reconcile fresh state')
        append_many(root, rows)


@contextmanager
def ledger_lock(root):
    """Lock the entire read/check/append transaction, not just a single write."""
    key = str(root.resolve())
    owner = (os.getpid(), get_ident())
    if _LOCKED.get(key) == owner:
        yield
        return
    path = lock_path(root)
    safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    exclude_private(root)
    with paths.safe_open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND) as fh:
        if util.WINDOWS:
            import msvcrt
            if fh.tell() == 0:
                fh.write(b'\0')
                fh.flush()
            fh.seek(0)
            deadline = time.monotonic() + LOCK_WAIT_SECONDS
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        fail('ledger busy; retry after the current writer finishes')
                    time.sleep(0.02)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX)
        _LOCKED[key] = owner
        try:
            yield
        finally:
            del _LOCKED[key]
            if util.WINDOWS:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fh, fcntl.LOCK_UN)


def ledger_mutation(command):
    """Resolve the repository once and keep command checks and writes together."""
    @wraps(command)
    def mutate(a):
        root = repo_root()
        with ledger_lock(root):
            return command(a, root)

    return mutate


def append_many(root, rows):
    rows = [dict(ts=now(), **row) if 'ts' not in row else row for row in rows]
    if not rows:
        return
    # A torn append cannot leave a valid prefix of individual events from a batch.
    value = rows[0] if len(rows) == 1 else {'event': 'batch', 'events': rows}
    encoded = (json.dumps(value, ensure_ascii=False) + '\n').encode('utf-8')
    with ledger_lock(root):
        path = ledger_path(root)
        with paths.safe_open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND) as fh:
            # Recover from an interrupted partial last line without joining two events.
            fh.seek(0, 2)
            if fh.tell():
                fh.seek(-1, 2)
                if fh.read(1) != b'\n':
                    fh.write(b'\n')
            fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())


def append(root, event):
    append_many(root, [event])


def events(root):
    rows = []
    try:
        with paths.safe_open(ledger_path(root), os.O_RDONLY, 'r', encoding='utf-8', errors='replace') as stream:
            for number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                    if isinstance(value, dict) and value.get('event') == 'batch' and 'id' not in value:
                        records = value.get('events')
                        if not isinstance(records, list) or len(records) < 2:
                            raise ValueError('expected a batch with at least two events')
                    else:
                        records = [value]
                    if any(not isinstance(record, dict) or not isinstance(record.get('id'), str) or not record['id']
                           for record in records):
                        raise ValueError('expected object with id')
                    rows.extend(records)
                except (ValueError, RecursionError):
                    print('xq: corrupt ledger line {} ignored'.format(number), file=sys.stderr)
    except FileNotFoundError:
        return []
    except OSError as exc:
        fail('cannot read ledger: ' + str(exc))
    return rows


def executor_observation(source, status):
    return {'source': source, 'state': status, 'observed_at': now()}


def apply_observation(task, observation):
    fields = ('source', 'state', 'observed_at')
    if not isinstance(observation, dict) or not all(isinstance(observation.get(key), str) and observation[key]
                                                       for key in fields):
        return
    previous = task.get('observation', {})
    if observation['observed_at'] >= previous.get('observed_at', ''):
        task['observation'] = {key: observation[key] for key in fields}


# A finish starts a fresh delivery: it clears the previous verdict, merge and delivery evidence.
FINISH_CLEARS = ('verdict', 'by', 'evidence', 'merged', 'commit', 'verified_head', 'report', 'report_sha256',
                 'verified_at', 'merged_at', 'delegate_exit', 'result', 'head', 'delivery_base', 'tests',
                 'observation', 'replaced_head', 'not_sent', 'refusal', 'ran_before_promote')


def replay_events(rows):
    tasks = {}
    for e in rows:
        tid = e['id']
        kind = e.get('event')
        if kind == 'assign':
            attempt = tasks.get(tid, {}).get('attempt', 0) + 1
            # New attempt carries no previous verdict, finish, job, base or merge.
            tasks[tid] = dict(e, attempt=attempt, assigned=e.get('ts', ''),
                              status='queued' if e.get('queued') else 'running')
            tasks[tid].pop('observation', None)
            if seams.adapter_for(e.get('executor')).batch_reports:
                tasks[tid].setdefault('batch_branch', e.get('branch'))
        elif kind == 'observe':
            if tid in tasks:
                apply_observation(tasks[tid], e.get('observation'))
        elif kind == 'import':
            apply_import(tasks, e)
        elif kind in ('finish', 'verify', 'merge', 'seen', 'close'):
            t = tasks.setdefault(tid, {'id': tid})
            if kind == 'verify' and t.get('status') in ('running', 'queued'):
                continue  # Forced early audit events never approve a delivery.
            if kind == 'finish':
                for key in FINISH_CLEARS:
                    t.pop(key, None)
                if not e.get('preserve_closed'):
                    # Historical forced finishes reopened the task.
                    t.pop('closed', None)
                    t.pop('reason', None)
            if kind == 'seen' and 'ack_until' not in e:
                # A heartbeat replaces the previously observed executor progress markers.
                for key in seams.heartbeat_fields():
                    t.pop(key, None)
            if kind == 'verify':
                # Each verdict carries its own sensitive-path review.
                for key in ('sensitive', 'sensitive_more', 'ack_sensitive', 'out_of_scope', 'scope_accepted'):
                    t.pop(key, None)
            if kind == 'verify' and t.get('merged') and (
                    e.get('verdict') != 'pass' or e.get('verified_head') != t.get('verified_head')):
                t.pop('merged', None)
                t.pop('commit', None)
                t.pop('merged_at', None)
            t.update({k: v for k, v in e.items() if k not in ('event', 'ts', 'id', 'preserve_closed', 'observation')})
            if kind == 'finish':
                t['finished'] = e.get('ts', '')
                apply_observation(t, e.get('observation'))
            elif kind == 'seen':
                t['seen'] = e.get('ts', '')
                if 'ack_until' not in e:
                    apply_observation(t, e.get('observation'))
            elif kind == 'close':
                t['closed'] = e.get('ts', '')
            elif kind == 'merge':
                t['merged'] = e.get('commit')
                t['merged_at'] = e.get('ts', '')
            elif kind == 'verify':
                t['verified_at'] = e.get('ts', '')
    return tasks


IMPORT_TASK_FIELDS = ('source_executor', 'brief', 'imported', 'model', 'effort')
IMPORT_ATTEMPT_FIELDS = ('verdict', 'by', 'merged', 'finished', 'verified_at', 'merged_at', 'closed')


def apply_import(tasks, e):
    """Replay one imported history line. It never rewrites a task xq recorded itself; 'closed' closes the task
    and keeps its last imported status. An imported assign starts a new attempt (clears verdict, merge and finish);
    verdict/by and commit (the merge commit) are recorded with the line's ts as verified_at/merged_at."""
    if e['id'] in tasks and not tasks[e['id']].get('imported'):
        return
    t = tasks.setdefault(e['id'], {'id': e['id']})
    kind, ts = e.get('import_event'), e.get('ts', '')
    if kind == 'assign':
        for key in IMPORT_ATTEMPT_FIELDS:
            t.pop(key, None)
        t['assigned'] = ts
    t.update({key: e[key] for key in IMPORT_TASK_FIELDS if key in e})
    if kind == 'finish':
        t['finished'] = ts
    if 'verdict' in e:
        t.update(verdict=e['verdict'], verified_at=ts)
        t.pop('by', None)
    if 'by' in e:
        t['by'] = e['by']
    if 'commit' in e:
        t.update(merged=e['commit'], merged_at=ts)
    if e.get('status') == 'closed' or kind == 'close':
        t['closed'] = ts
    elif e.get('status'):
        t['status'] = e['status']


def state(root, *, rows=None):
    return replay_events(events(root) if rows is None else rows)
