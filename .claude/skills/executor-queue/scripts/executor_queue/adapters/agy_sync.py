"""agy-bridge artifacts (ADAPTERS): result and refusal files, the bridge runner probe and agy-sync."""

import hashlib
import os
from pathlib import Path
import re
import shlex
import stat

from ..core import ledger, paths, util
from ..core.ledger import executor_observation, snapshot
from ..core.state import valid_id
from ..core.util import display_name, fail


AGY_STATE_DIR = '.agy-bridge'  # agy-bridge's queue, results and logs in the primary checkout


AGY_RESULT_MAX_BYTES = 8 * 1024 * 1024
AGY_RESULT_FORMAT = b'<!-- agy-bridge-result-v2 -->'
AGY_RESULT_CHECKSUM = b'\n<!-- agy-bridge-result-sha256: '
AGY_ROUND_HEADING = re.compile(r'^## Round ([0-9]+)$', re.M)
AGY_ATTEMPT_HEADING = re.compile(r'^### Attempt [0-9]+ \(round [0-9]+\)$', re.M)
# Anything that is not a checksummed agy-bridge v2 queue result is model text: never bridge metadata.
AGY_PLAIN = object()
AGY_PLAIN_HINT = ('plain delegate output; record its exit with xq.py agy-sync --delegate-exit {0}=<code> '
                  '--out <abs path> (write delegate output to .agy-bridge/delegate/{0}.md)')


def agy_artifact_exists(path):
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('unsafe symlink in bridge artifact path')
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError('bridge artifact must be a regular file')
        return path.name in os.listdir(str(path.parent))
    except FileNotFoundError:
        return False


def read_agy_bytes(path):
    """(raw bytes up to AGY_RESULT_MAX_BYTES, truncated) for a regular bridge artifact, None when absent."""
    if not agy_artifact_exists(path):
        return None
    flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
    with paths.safe_open(path, flags, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('bridge artifact must be a regular file')
        raw = stream.read(AGY_RESULT_MAX_BYTES + 1)
    return raw[:AGY_RESULT_MAX_BYTES], len(raw) > AGY_RESULT_MAX_BYTES


def agy_bridge_header(raw, truncated, name):
    """Header of a checksummed agy-bridge v2 queue result, or AGY_PLAIN for any other (model-written) text.

    The bridge (queue.py) writes '# <job>', its warning, the v2 marker, job fields, '### Attempt N (round N)'
    blocks, '## Round 1' and a trailing '<!-- agy-bridge-result-sha256: <hex> -->' over everything before it.
    A file that claims the v2 format but fails these checks is a PROBLEM (interrupted or edited), not plain.
    """
    prefix = re.match(rb'# ([^\n]*)\n\n[^\n]*\n\n' + re.escape(AGY_RESULT_FORMAT) + rb'\n', raw)
    if not prefix:
        return AGY_PLAIN
    if prefix.group(1) != name.encode('utf-8'):
        raise ValueError('bridge result names a different job ({!r})'.format(prefix.group(1).decode('utf-8', 'replace')))
    if truncated:
        raise ValueError('bridge result exceeds {} bytes; cannot verify its checksum'.format(AGY_RESULT_MAX_BYTES))
    body, separator, checksum = raw.rpartition(AGY_RESULT_CHECKSUM)
    if not separator or checksum != hashlib.sha256(body).hexdigest().encode('ascii') + b' -->\n':
        raise ValueError('bridge result checksum is missing or wrong (interrupted or edited write)')
    text = body.decode('utf-8', errors='replace')
    heading = AGY_ROUND_HEADING.search(text)
    if not heading or heading.group(1) != '1':
        raise ValueError('bridge result has no "## Round 1" answer heading')
    header = text[:heading.start()]
    if not AGY_ATTEMPT_HEADING.search(header):
        raise ValueError('bridge result has no "### Attempt N (round N)" block')
    return header


def read_agy_header(path, name):
    """Verified bridge header, None when the artifact is absent, or AGY_PLAIN for model text."""
    item = read_agy_bytes(path)
    return None if item is None else agy_bridge_header(item[0], item[1], name)


AGY_BRIDGE_PREFIX = re.compile(rb'# [^\n]*\n\n[^\n]*\n\n' + re.escape(AGY_RESULT_FORMAT) + rb'\n')


def agy_bridge_prefixed(item):
    """True when an artifact starts with the exact agy-bridge result prefix (v2 marker on its own line).

    A delegate answer may quote the marker or show `exit_code:` lines (reviews of bridge code); only a
    file the bridge itself wrote starts with this prefix.
    """
    return item is not None and AGY_BRIDGE_PREFIX.match(item[0]) is not None


def delegate_output_exists(path):
    try:
        return agy_artifact_exists(path)
    except (OSError, ValueError):
        return False


AGY_NO_RUNNER = 'pending; no active bridge runner: run "$AGYB" queue run --repo {}'


def agy_runner_active(root):
    """Probe the bridge runner lock like agy-bridge QueueLocation.running(): only a runner holds it exclusively.

    True: a runner holds it; False: no watcher.pid or the shared lock was granted; None: cannot tell.
    """
    if util.WINDOWS:
        return None
    import fcntl
    queue = root / '.agy-bridge/queue'
    try:
        if queue.is_symlink() or queue.parent.is_symlink():
            return None
        fd = os.open(str(queue / 'watcher.pid'), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return False
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return None
    finally:
        os.close(fd)


AGY_REFUSAL_HEADING = '# Queue refusal'
AGY_REFUSAL_MARKER = re.compile(r'<!-- agy-bridge-refusal-v1 sent: (no|maybe) -->')


def agy_refusal(path):
    """(sent, reason) when a failed-archive result is a bridge refusal ('no' = nothing reached agy), else None.

    The bridge writes '# Queue refusal', '<!-- agy-bridge-refusal-v1 sent: no|maybe -->' and the reason;
    older bridges wrote only the heading (always before sending). Either line may come first.
    """
    item = read_agy_bytes(path)
    if item is None:
        return None
    lines = item[0][:4096].decode('utf-8', errors='replace').split('\n')
    head = lines[:2]
    marker = next((AGY_REFUSAL_MARKER.fullmatch(line) for line in head if AGY_REFUSAL_MARKER.fullmatch(line)), None)
    if lines[0] == AGY_REFUSAL_HEADING:
        sent = marker.group(1) if marker else 'no'
    elif marker and AGY_REFUSAL_MARKER.fullmatch(lines[0]):
        sent = marker.group(1)
    else:
        return None
    rest = [line.strip() for line in lines[1:] if line.strip() and line.strip() != AGY_REFUSAL_HEADING
            and not AGY_REFUSAL_MARKER.fullmatch(line.strip())]
    return sent, display_name(rest[0] if rest else 'no reason given')[:200]


def import_agy_refusal(root, tid):
    """A running agy task whose bridge job was cancelled/refused before sending: record that finish now.

    Runs inside close's ledger lock, so `queue cancel` then `close --reason dropped` needs no poll in between.
    """
    task = ledger.state(root).get(tid) or {}
    if task.get('executor') != 'agy' or task.get('status') != 'running' or task.get('closed'):
        return
    folder = root / AGY_STATE_DIR
    try:
        cancelled = (agy_artifact_exists(folder / 'queue/failed' / (tid + '.job')) and
                     (agy_refusal(folder / 'results' / (tid + '.md')) or ('',))[0] == 'no')
    except (OSError, ValueError):
        cancelled = False
    if not cancelled:
        return
    try:
        code = sync_agy(root, task_id=tid)
    except SystemExit:
        code = 1
    if code or (ledger.state(root).get(tid) or {}).get('status') == 'running':
        fail('run xq.py poll --id {} first (the bridge cancelled it)'.format(tid))


def agy_delegate_exit_row(root, known, explicit, out):
    """(task id, finish row) for --delegate-exit <id>=<code> with its plain --out result."""
    match = re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9._-]{0,119})=([0-9]{1,10})', explicit)
    if not match:
        fail('--delegate-exit requires <id>=<nonnegative exit code>')
    tid, code = match.group(1), int(match.group(2))
    t = known.get(tid, {})
    if t.get('executor') != 'agy' or t.get('status') != 'running' or t.get('closed'):
        fail('--delegate-exit requires a running agy task')
    result = Path(out)
    if not result.is_absolute():
        fail('--out must be an absolute path')
    archives = root / '.agy-bridge/queue'
    try:
        if any(agy_artifact_exists(archives / name / (tid + '.job')) for name in ('', 'done', 'failed')):
            fail('delegate has a queue archive; use ordinary agy-sync to preserve bridge checks')
        result_item = read_agy_bytes(result)
        if code == 0 and result_item is None:
            fail('successful delegate requires an existing --out result')
        bridge_result = root / '.agy-bridge/results' / (tid + '.md')
        items = [result_item]
        if bridge_result != result:
            items.append(read_agy_bytes(bridge_result))
        # A file the bridge wrote must go through ordinary agy-sync; the exit code below comes only
        # from --delegate-exit, never from text in the answer.
        if any(agy_bridge_prefixed(item) for item in items):
            fail('result has bridge metadata; use ordinary agy-sync to preserve bridge checks')
    except (OSError, ValueError) as exc:
        fail('cannot read bridge artifacts: ' + str(exc))
    status = 'done' if code == 0 else 'failed'
    row = dict(event='finish', id=tid, status=status,
               summary='Unverified plain delegate exit {}: {}'.format(code, result),
               delegate_exit=code, observation=executor_observation('agy', status))
    if result_item is not None:
        row['result'] = str(result)  # A failed delegate without output delivered nothing to verify.
    else:
        row['summary'] = 'plain delegate exit {}; no output at {}'.format(code, result)
    return tid, row


def agy_sync_selection(known, task_id, explicit, out):
    """The (id, task) pairs one sync looks at, after the argument checks."""
    if bool(explicit) != bool(out):
        fail('--delegate-exit <id>=<code> requires an absolute --out path')
    if task_id is None:
        return known.items()
    valid_id(task_id)
    if explicit:
        fail('cannot select a task and reconcile a delegate exit in the same query')
    if known.get(task_id, {}).get('executor') != 'agy':
        fail('no registered agy task ' + task_id)
    return [(task_id, known[task_id])]


def agy_pending_observation(root, tid, observe, runner_active, notes):
    """Observe a job still in the bridge queue; runner_active() probes the bridge runner once per sync."""
    active = runner_active()
    if active is True:
        observe(tid, 'unknown', 'pending; bridge runner active')
    elif active is False:
        observe(tid, 'unknown', AGY_NO_RUNNER.format(shlex.quote(str(root))))
        if notes is not None:
            notes[tid] = 'no-runner'
    else:
        observe(tid, 'unknown', 'pending bridge artifact exists')


def agy_failed_finish(tid, result, closure):
    """Finish row for a job in the bridge's failed archive."""
    row = dict(event='finish', id=tid, status='failed', observation=executor_observation('agy', 'failed'), **closure)
    refusal = agy_refusal(result)
    if refusal and refusal[0] == 'no':
        # Cancelled or refused before anything reached agy: nothing was delivered to verify.
        row.update(summary='agy job cancelled/refused before sending: ' + refusal[1],
                   not_sent=True, refusal=str(result))
    elif agy_artifact_exists(result):
        row.update(summary='agy failed archive; inspect result locally', result=str(result))
    else:
        row.update(summary='agy failed archive; no result, inspect locally')
    return row


def agy_unfinished_observation(tid, t, header, done, folder, observe):
    """Observe a result that cannot finish the task (no result or plain model text); True on a PROBLEM."""
    if header is None:
        if done:
            print('PROBLEM {}: done archive has no result'.format(tid))
            observe(tid, 'unavailable')
            return True
        if t.get('status') == 'running' and delegate_output_exists(folder / 'delegate' / (tid + '.md')):
            observe(tid, 'unknown', AGY_PLAIN_HINT.format(tid), always=True)
        else:
            observe(tid, 'unknown', 'no bridge artifact found')
        return False
    # Model text can contain an `exit_code: 0` line; only an explicit exit finishes plain output.
    if done:
        print('PROBLEM {}: done archive result is not a checksummed agy-bridge v2 result; '
              'inspect it manually'.format(tid))
        observe(tid, 'unavailable')
        return True
    observe(tid, 'unknown', AGY_PLAIN_HINT.format(tid), always=True)
    return False


def agy_header_status(header):
    """'done' or 'failed' from a checksummed bridge header, or None when it records no exit code."""
    # Only the bridge header supplies exit metadata.
    metadata = re.split(r'^### Attempt\b', header, maxsplit=1, flags=re.M)[0]
    codes = re.findall(r'^exit_code:[ \t]*([0-9]+)[ \t]*$', metadata, re.M)
    # A recovered quota retry is not a failed delivery. Permission, truncation,
    # and citation failures in any attempt still require explicit review.
    unsafe_attempt = re.search(r'^exit_code:\s*(?:15|16|17)\s*$', header, re.M)
    omitted = re.search(r'^FILES NOT SENT:\s*\S', header, re.M)
    if not codes:
        return None
    rejected = omitted or unsafe_attempt or any(code.strip('0') for code in codes)
    return 'failed' if rejected else 'done'


def agy_sync_task(root, tid, t, observe, runner_active, notes):
    """Import one agy task's bridge artifacts: (finish row or None, problem)."""
    closure = {'preserve_closed': True} if t.get('closed') else {}
    valid_id(tid)
    folder = root / AGY_STATE_DIR
    result = folder / 'results' / (tid + '.md')
    queue = folder / 'queue'
    job_file = tid + '.job'
    try:
        if agy_artifact_exists(queue / job_file):
            agy_pending_observation(root, tid, observe, runner_active, notes)
            return None, False
        failed = agy_artifact_exists(queue / 'failed' / job_file)
        done = agy_artifact_exists(queue / 'done' / job_file)
        if failed:
            return agy_failed_finish(tid, result, closure), False
        header = read_agy_header(result, tid)
    except (OSError, ValueError) as exc:
        print('PROBLEM {}: cannot read bridge artifacts: {}'.format(tid, exc))
        observe(tid, 'unavailable')
        return None, True
    if header is None or header is AGY_PLAIN:
        return None, agy_unfinished_observation(tid, t, header, done, folder, observe)
    status = agy_header_status(header)
    if status is None:
        print('PROBLEM {}: missing bridge exit header; inspect result manually'.format(tid))
        observe(tid, 'unavailable')
        return None, True
    return dict(event='finish', id=tid, status=status,
                summary='Unverified agy result: ' + str(result), result=str(result),
                observation=executor_observation('agy', status), **closure), False


def sync_agy(root, *, task_id=None, delegate_exit=None, out=None, observe_incomplete=False, changes_only=False,
             notes=None, finish_extra=None):
    """Import bridge artifacts; notes (dict) receives 'no-runner' for pending jobs nobody will run.

    finish_extra (dict) is merged into a finish row recorded for task_id.
    """
    rows = []
    runner = []  # probed lazily, once per sync
    problem = False
    known, stamp = snapshot(root)
    explicit = delegate_exit
    tasks = agy_sync_selection(known, task_id, explicit, out)

    def observe(tid, status, detail=None, always=False):
        unchanged = (known.get(tid, {}).get('observation') or {}).get('state') == status
        if changes_only and unchanged:
            return
        if task_id is not None or observe_incomplete or always:
            if detail and not changes_only:
                print('{}: {}'.format(tid, detail))
            if not (always and unchanged and task_id is None and not observe_incomplete):
                rows.append(dict(event='observe', id=tid, observation=executor_observation('agy', status)))

    def runner_active():
        if not runner:
            runner.append(agy_runner_active(root))
        return runner[0]

    delegate_id = None
    if explicit:
        delegate_id, row = agy_delegate_exit_row(root, known, explicit, out)
        rows.append(row)
    for tid, t in tasks:
        if tid == delegate_id:
            continue
        if t.get('executor') != 'agy' or t.get('status') not in ('running', 'queued'):
            continue
        if t.get('closed') and t.get('status') != 'running':
            continue
        row, failed = agy_sync_task(root, tid, t, observe, runner_active, notes)
        problem = problem or failed
        if row is not None:
            if finish_extra and tid == task_id:
                row.update(finish_extra)
            rows.append(row)
    ledger.append_checked(root, rows, stamp)
    return 1 if problem else 0
