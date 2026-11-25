"""The agy adapter (ADAPTERS): jobs queued with agy-bridge in the primary checkout."""

import json
import os
import shlex
import stat
import sys

from ..core import util
from ..core.util import display_name, fail
from ..port import discovery, runner
from ..port.adapter import ExecutorAdapter
from ..port.discovery import binary_source, shell_assignment
from ..port.runner import PROCESS_NAMES
from . import agy_sync
from .agy_sync import agy_runner_active, AGY_STATE_DIR, import_agy_refusal


# --- ADAPTERS: agy ------------------------------------------------------------------------------------------------


AGY_RUNNER_ACTIVE_NOTE = ('NOTE a bridge runner/watcher is active: queue add will send the job immediately; run '
                          'preflight and promote before queue add')


def exact_file(path):
    return path.is_file() and path.name in os.listdir(str(path.parent))


def agy_ran_before_promote(root, a, previous, existing):
    """A queued agy task whose only artifacts are its own bridge archive and result (a watcher ran it)."""
    if (a.queued or previous.get('status') != 'queued' or previous.get('executor') != 'agy' or
            previous.get('closed')):
        return False
    queue, results = root / '.agy-bridge/queue', root / '.agy-bridge/results'
    archives = {queue / 'done' / (a.id + '.job'), queue / 'failed' / (a.id + '.job')}
    allowed = archives | {results / (a.id + '.md')}
    return (any(path in archives for path in existing) and all(path in allowed for path in existing) and
            not (archives <= set(existing)) and all(exact_file(path) and not path.is_symlink() for path in existing))


_BRIDGE_PROBES = {}


def bridge_supports(bridge, args, needle=None):
    """True/False when the bridge's help exits 0 (and mentions needle); None when it cannot run. Cached."""
    key = (bridge, tuple(args), needle)
    if key not in _BRIDGE_PROBES:
        try:
            r = runner.run(discovery.bridge_command(bridge) + list(args))
        except OSError:
            r = None
        if r is None or (r.returncode and not r.stdout and 'No such file' in (r.stderr or '')):
            _BRIDGE_PROBES[key] = None
        else:
            _BRIDGE_PROBES[key] = r.returncode == 0 and (needle is None or needle in r.stdout + r.stderr)
    return _BRIDGE_PROBES[key]


# agy-bridge reads at most 8 MiB (MAX_RESULT_BYTES) of a result; the checksum covers the whole file.


def agy_assign_artifacts(root, a, previous):
    """Refuse reused agy names; True when the only artifacts are this queued task's own archived run."""
    folder = root / AGY_STATE_DIR
    artifacts = [folder / name / (a.id + suffix) for name, suffix in
                 (('results', '.md'), ('delegate', '.md'), ('queue', '.job'), ('queue/done', '.job'),
                  ('queue/failed', '.job'))]
    if previous.get('status') == 'queued' and previous.get('executor') == 'agy' and not a.queued:
        job = folder / 'queue' / (a.id + '.job')
        if exact_file(job):
            try:
                name = json.loads(job.read_text(encoding='utf-8')).get('name')
            except (ValueError, AttributeError):
                name = None
            if name != a.id:
                shown = display_name(name[:120]) if isinstance(name, str) else '<missing or invalid>'
                fail('.agy-bridge/queue/{}.job has name "{}"; the job name must equal the task id'.format(
                    a.id, shown))
            artifacts = [p for p in artifacts if p != job]
    existing = [p for p in artifacts if p.exists() or p.is_symlink()]
    if existing and agy_ran_before_promote(root, a, previous, existing):
        return True
    if existing:
        fail('agy names must be unique; existing artifact(s) for {0}: {1}; use a fresh id (e.g. {0}-r2), then '
             'xq.py close --id {0} --reason superseded-by:{0}-r2'.format(
                 a.id, ', '.join(p.relative_to(root).as_posix() for p in existing)))
    return False


def agy_close_guard(root, a, reason):
    """Refuse to close a task whose bridge job is still pending (the next queue run would send it)."""
    job = root / AGY_STATE_DIR / 'queue' / (a.id + '.job')
    try:
        pending = stat.S_ISREG(job.lstat().st_mode) and not job.parent.is_symlink()
    except OSError:
        pending = False
    if not pending:
        return
    bridge = discovery.plugin_binary('agy') or 'agy-bridge'
    message = ('{0} has a pending agy-bridge job (.agy-bridge/queue/{0}.job) that the next queue run would '
               'send'.format(a.id))
    if not a.force:
        if bridge_supports(bridge, ('queue', 'cancel', '--help')) is False:
            fail('{}; this agy-bridge has no queue cancel: stop the runner/watcher, remove .agy-bridge/queue/'
                 '{}.job by hand, then close --id {} --reason {} --force (or /plugin update agy@agy-bridge)'
                 .format(message, a.id, a.id, reason))
        fail('{}; cancel it first: "{}" queue cancel {} -C {} --reason dropped, then xq.py poll --id {} '
             '(or close --force)'.format(message, bridge, a.id, shlex.quote(str(root)), a.id))
    print('WARN {}; it is still pending after this close'.format(message), file=sys.stderr)


AGY_INSTALL_HINT = ('npx skills add dominno/claude-agy-bridge --skill agy-bridge -a claude-code -a codex '
                    '(or /plugin marketplace add https://github.com/dominno/claude-agy-bridge; '
                    '/plugin install agy@agy-bridge)')


def agy_doctor_checks(root, a, check, settings):
    """doctor --need agy: the agy-bridge plugin, its doctor and features, and the .agy-bridge ignore."""
    if util.WINDOWS:
        check('agy platform', False, 'unsupported on Windows',
              'agy-bridge supports macOS and Linux; run the bridge in a supported environment')
    else:
        agyb = discovery.plugin_binary('agy')
        check('agy-bridge', agyb, '{} ({})'.format(agyb, binary_source('agy', agyb)) if agyb else 'not installed',
              AGY_INSTALL_HINT, plugin=True)
        if agyb:
            print(shell_assignment('AGYB', agyb))
            command = discovery.bridge_command(agyb)
            doctor_args = ['doctor', '--repo', str(root)]
            if a.offline:
                doctor_args.append('--offline')
            probe = runner.run(command + doctor_args, timeout=60)
            scope = ('local Python and CLI checks only (authentication and model access skipped with --offline)'
                     if a.offline else 'Python, CLI version and model access in the bridge environment')
            check('agy-bridge doctor', probe.returncode == 0,
                  scope if probe.returncode == 0 else '{}: bridge doctor exited {}'.format(scope, probe.returncode),
                  'see /agy:doctor and the bridge output below')
            if probe.stdout.strip():
                print(probe.stdout.rstrip())
            for feature in ('queue', 'verdict'):
                supported = runner.run(command + [feature, '--help']).returncode == 0
                check('agy-bridge ' + feature, supported, 'supported' if supported else 'unsupported',
                      '/plugin update agy@agy-bridge or use only /agy:delegate; version labels alone do not '
                      'establish support', plugin=True)
            # Recipe steps that need newer bridge features than queue/verdict themselves.
            cancel = runner.run(command + ['queue', 'cancel', '--help']).returncode == 0
            check('agy-bridge queue cancel', cancel, 'supported' if cancel else
                  'unsupported (recipe step: drop a pending job)',
                  '/plugin update agy@agy-bridge (the cached plugin predates this feature)', plugin=True)
            watch = runner.run(command + ['queue', 'watch', '--help'])
            detach = watch.returncode == 0 and '--detach' in watch.stdout + watch.stderr
            check('agy-bridge queue watch --detach', detach, 'supported' if detach else
                  'unsupported (recipe step: detached watcher)',
                  '/plugin update agy@agy-bridge (the cached plugin predates this feature)', plugin=True)
    ignored = runner.run(['git', 'check-ignore', '-q', '.agy-bridge/probe'], cwd=root).returncode == 0
    tracked = bool(runner.git('ls-files', '--', AGY_STATE_DIR, check=False, cwd=root).strip())
    check('.agy-bridge ignored', ignored and not tracked,
          'ignored and untracked' if ignored and not tracked else ('tracked' if tracked else 'not ignored'),
          'run xq init; remove tracked output before any push')


class AgyAdapter(ExecutorAdapter):
    """agy-bridge queue/delegate runs; outcomes are imported from local .agy-bridge artifacts."""
    name = 'agy'
    plugin = ('bin/agy-bridge', ('agy-bridge', 'agy-bridge-local'))
    skill = ('agy-bridge', 'scripts/agy-bridge')
    idle_alert_needs_keep_busy = True
    polled = True
    queued_hint = 'promote with assign --id {0} --executor agy --brief <b> before queue run/delegate'
    private_state = (AGY_STATE_DIR,)

    @staticmethod
    def runner_note(root):
        return AGY_RUNNER_ACTIVE_NOTE if agy_runner_active(root) is True else None

    @staticmethod
    def check_assign_artifacts(root, a, previous):
        return agy_assign_artifacts(root, a, previous)

    @staticmethod
    def import_promoted_run(root, tid):
        # The bridge already archived this job: import its outcome now instead of demanding a fresh id.
        result = agy_sync.sync_agy(root, task_id=tid, finish_extra={'ran_before_promote': True})
        print('WARN the bridge already ran {} before promotion (a watcher was active); preflight was bypassed; '
              'result imported - verify it next'.format(tid), file=sys.stderr)
        return result

    @staticmethod
    def live_process(name, tokens):
        if name == 'agy':
            return not any(t in ('--version', '--help', 'models', 'login', 'logout') for t in tokens[1:2])
        if name and name not in PROCESS_NAMES:  # A Python interpreter running the bridge.
            return (any(t in ('agy-bridge', 'agy-bridge.py') for t in tokens) and
                    ('delegate' in tokens or any(tokens[i:i+2] == ['queue', 'run'] for i in range(len(tokens)))))
        return False

    @staticmethod
    def poll(root, task, stamp, *, quiet=False, notes=None):
        # Waiting re-polls: record agy observations only when they change.
        return agy_sync.sync_agy(root, task_id=task['id'], notes=notes, **({'changes_only': True} if quiet else {}))

    @staticmethod
    def poll_all(root, explicit):
        return agy_sync.sync_agy(root, observe_incomplete=True) if explicit else agy_sync.sync_agy(root)

    @staticmethod
    def doctor_checks(root, a, check, settings):
        agy_doctor_checks(root, a, check, settings)

    @staticmethod
    def pre_close_import(root, tid):
        import_agy_refusal(root, tid)

    @staticmethod
    def close_guard(root, a, reason):
        agy_close_guard(root, a, reason)
