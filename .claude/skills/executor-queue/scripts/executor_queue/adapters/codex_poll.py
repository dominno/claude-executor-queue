"""Codex job polling (ADAPTERS): companion status queries and the poll of running Codex tasks."""

import json
import os
from pathlib import Path
import shlex
import sys
import time

from ..core import ledger, util
from ..core.ledger import executor_observation
from ..core.review import print_reports
from ..core.util import display_name, OPTION_RE, short_reason
from ..port import discovery, runner
from ..port.discovery import plugin_project_root
from ..port.runner import worktree_git
from .codex_worktree import delivery_fields, REMOVED_WORKTREE_STEP


def codex_json(*args):
    """Parse stdout as JSON whatever the exit code (codex doctor exits 1 on any failed check)."""
    result = runner.run(['codex', *args], timeout=60)
    try:
        return json.loads(result.stdout)
    except (ValueError, RecursionError):
        pass
    if result.returncode:
        raise ValueError('codex {} exited {}{}'.format(' '.join(args), result.returncode, (
            ': ' + short_reason(result.stderr, 80)) if short_reason(result.stderr, 80) else ''))
    raise ValueError('codex {} did not print JSON'.format(' '.join(args)))


def codex_model_check(check):
    """Codex auth can be fine while the configured default model is not offered to the account."""
    try:
        details = codex_json('doctor', '--json')['checks']['config.load']['details']
        if not isinstance(details, dict):
            raise TypeError('unexpected JSON shape')
        model = details.get('model')
        if model is None or model == '' or model == '<default>':
            print('WARN Codex default model: none configured (built-in default); pass --model <m> to the Codex '
                  'companion task (and record it with xq.py assign --model)')
            return
        provider = details.get('model provider')
        if provider not in (None, '', 'openai'):
            # Only OpenAI's catalog is listed by `codex debug models`; other providers serve their own models.
            print('WARN cannot verify Codex default model for provider {}'.format(
                display_name(short_reason(provider, 64))))
            return
        config_path = details.get('config.toml')
        listing = codex_json('debug', 'models')
        models = listing['models'] if isinstance(listing, dict) else listing
        slugs = [m['slug'] for m in models if isinstance(m, dict) and m.get('visibility') == 'list'
                 and isinstance(m.get('slug'), str)]
        if not isinstance(model, str) or not slugs:
            raise ValueError('unexpected JSON shape')
    except (ValueError, KeyError, TypeError, RecursionError) as exc:
        reason = short_reason(exc if isinstance(exc, ValueError) else 'unexpected JSON shape')
        print('WARN cannot verify Codex default model' + (': ' + reason if reason else ''))
        return
    # Values come from external JSON (the user's Codex config); never echo control bytes.
    if not OPTION_RE.fullmatch(model):
        print('WARN cannot verify Codex default model: model name {!r} has unexpected characters'
              .format(model[:64]))
        return
    slugs = [slug for slug in slugs if OPTION_RE.fullmatch(slug)]
    if model in slugs:
        check('Codex default model', True, model)
    else:
        shown = display_name(short_reason(config_path, 200)) if isinstance(config_path, str) and \
            short_reason(config_path, 200) else '~/.codex/config.toml'
        check('Codex default model', False, '{} is not offered for this account'.format(model),
              'pass --model <one of {}> or change model in {} (xq does not edit it)'.format(
                  ', '.join(slugs[:4]), shown))


def codex_poll_worktree(root_common, value):
    try:
        checkout = Path(value)
        if not checkout.is_absolute():
            return None
        checkout = checkout.resolve(strict=True)
        location = worktree_git(checkout, 'rev-parse', '--path-format=absolute', '--git-common-dir',
                                '--show-toplevel', check=False).splitlines()
        common, top = (Path(path).resolve() for path in location)
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    return str(checkout) if common == root_common and top == checkout else None


CODEX_JOB_STATES = {
    'running': 'running', 'queued': 'queued', 'completed': 'done', 'done': 'done',
    'failed': 'failed', 'error': 'failed', 'cancelled': 'cancelled', 'canceled': 'cancelled',
}


def codex_state_environments(recorded):
    """Companion state roots to try: recorded, current, then the TMPDIR fallback."""
    current = os.environ.get('CLAUDE_PLUGIN_DATA')
    options = []
    if recorded:
        options.append((recorded, dict(os.environ, CLAUDE_PLUGIN_DATA=recorded)))
    options.append((current or 'TMPDIR fallback', None))
    if current:
        fallback = {key: value for key, value in os.environ.items() if key != 'CLAUDE_PLUGIN_DATA'}
        options.append(('TMPDIR fallback', fallback))
    seen, result = set(), []
    for label, env in options:
        if label not in seen:
            seen.add(label)
            result.append((label, env))
    return result


def codex_job_detail(job):
    """Liveness and change markers from a companion status payload (pid, phase, updatedAt)."""
    pid = job.get('pid')
    # kill() takes a C pid_t: anything outside 1..2**31-1 is malformed, not a process.
    detail = {'pid': pid if type(pid) is int and 0 < pid < 2 ** 31 else None}
    for key, field in (('phase', 'phase'), ('updated', 'updatedAt')):
        value = job.get(field)
        detail[key] = value[:200] if isinstance(value, str) and value else None
    return detail


def pid_alive(pid):
    """False only when the worker pid has certainly exited; unknown counts as alive."""
    if util.WINDOWS or not hasattr(os, 'kill'):
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, OverflowError, ValueError):
        return True
    return True


def query_codex_status(candidates, job_id, checkout, state_root=None):
    """Return (state, argv prefix, env, tried roots, detail); argv is None when no root knows the job."""
    outcome = 'unavailable'
    tried = []
    for label, env in codex_state_environments(state_root):
        tried.append(label)
        for companion in candidates:
            args = ['node', companion]
            result = runner.run(args + ['status', job_id, '--cwd', checkout, '--json'], env=env)
            if result.returncode:
                continue  # Typically "No job found" under this state root.
            try:
                job = json.loads(result.stdout)['job']
                if job.get('id', job_id) == job_id:
                    return CODEX_JOB_STATES[job['status']], args, env, tried, codex_job_detail(job)
            except (AttributeError, KeyError, RecursionError, TypeError, ValueError):
                pass
            outcome = 'unknown'
    return outcome, None, None, tried, {}


def codex_state_changed(task, status, detail):
    """A heartbeat is written only when the companion's observable state moves."""
    observation = task.get('observation') or {}
    return (observation.get('state') != status or task.get('codex_phase') != detail.get('phase') or
            task.get('codex_updated') != detail.get('updated'))


# One re-read before declaring a worker orphaned or a status unavailable: the companion writes its job file
# and reaps its worker non-atomically, so a single read can race a finishing job.
CODEX_RECHECK_SECONDS = 0.75


class CodexPoll:
    """One poll of Codex companion jobs: the rows to append, reports to print, and whether a PROBLEM was seen."""

    def __init__(self, root, *, explicit, quiet, notes):
        self.root, self.explicit, self.quiet, self.notes = root, explicit, quiet, notes
        self.root_common = Path(runner.git('rev-parse', '--path-format=absolute', '--git-common-dir',
                                    cwd=root).strip()).resolve()
        self.caller_project = plugin_project_root()
        self.companions, self.rows, self.reports = {}, [], []
        self.problem = False

    def companion(self, project):
        if project not in self.companions:
            self.companions[project] = discovery.plugin_binary('codex', project=project)
        return self.companions[project]

    @staticmethod
    def observe(tid, status):
        return dict(event='observe', id=tid, observation=executor_observation('codex', status))

    def report_problem(self, event, message):
        print('PROBLEM {}: {}'.format(event['id'], message))
        self.problem = True
        if self.explicit:
            self.rows.append(event)

    def candidates(self, task):
        """Companion scripts that may know this job, the registering project's first."""
        registered_project = task.get('codex_project')
        projects = [Path(registered_project)] if registered_project else [self.root]
        if not registered_project and self.caller_project and self.caller_project != self.root:
            projects.insert(0, self.caller_project)
        return list(dict.fromkeys(path for path in map(self.companion, projects) if path))

    def first_read(self, task):
        """[task, checkout, candidates, status read, suspect pid, needs re-read], or None when it cannot be read."""
        tid, closed = task['id'], task.get('closed')
        job = task.get('job')
        checkout = codex_poll_worktree(self.root_common, task.get('worktree')) if job else None
        if not checkout:
            if self.explicit or not closed:
                recorded = task.get('worktree')
                if job and isinstance(recorded, str) and os.path.isabs(recorded) and not os.path.isdir(recorded):
                    message = 'worktree "{}" no longer exists; {}'.format(
                        display_name(recorded), REMOVED_WORKTREE_STEP.format(tid))
                else:
                    message = 'missing job or invalid recorded worktree. Reconcile manually.'
                self.report_problem(self.observe(tid, 'unavailable'), message)
            return None
        candidates = self.candidates(task)
        if not candidates:
            if self.explicit or not closed:
                self.report_problem(self.observe(tid, 'unavailable'), 'missing Codex companion. Reconcile manually.')
            return None
        first = query_codex_status(candidates, job, checkout, task.get('codex_state_root'))
        status, args, _, _, detail = first
        suspect_pid = None
        if status in ('running', 'queued') and detail.get('pid') is not None and not pid_alive(detail['pid']):
            suspect_pid = detail['pid']
        return [task, checkout, candidates, first, suspect_pid, args is None or suspect_pid is not None]

    @staticmethod
    def reread(reads):
        """Re-read every entry that needs it after one shared pause."""
        # One shared pause before every re-read, not one per task.
        if any(entry[5] for entry in reads):
            time.sleep(CODEX_RECHECK_SECONDS)
        for entry in reads:
            task, checkout, candidates, first, suspect_pid, recheck = entry
            if not recheck:
                continue
            again = query_codex_status(candidates, task.get('job'), checkout, task.get('codex_state_root'))
            tried = list(dict.fromkeys(first[3] + again[3]))
            # A failed re-read keeps the first read's state (a running job with a suspect pid stays suspect);
            # 'unavailable' is reported only when both reads fail.
            chosen = again if again[1] is not None or first[1] is None else first
            entry[3] = chosen[:3] + (tried,) + chosen[4:]

    def apply(self, task, checkout, read, suspect_pid):
        status, args, env, tried, detail = read
        tid = task['id']
        event = self.observe(tid, status)
        if args is None:
            if self.notes is not None:
                self.notes[tid] = 'unavailable'
            self.report_problem(event, 'invalid or unavailable job status (companion state roots tried: {}), '
                                       'reconcile before retrying'.format(', '.join(tried)))
        elif status in ('running', 'queued'):
            self.apply_live(task, checkout, event, status, detail, suspect_pid)
        else:
            self.apply_terminal(task, checkout, event, status, args, env)

    def apply_live(self, task, checkout, event, status, detail, suspect_pid):
        tid, closed, job = task['id'], task.get('closed'), task.get('job')
        previous = (task.get('observation') or {}).get('state')
        pid = detail.get('pid')
        if pid is not None and pid == suspect_pid and not pid_alive(pid):
            # The companion still says running, but nothing will ever finish this job.
            # kill(pid, 0) only sees this process's PID namespace: a sandboxed or containerized
            # poll can miss a healthy worker, so the instruction asks to confirm first.
            print('PROBLEM {}: companion reports {} but worker pid {} appears to have exited (not visible to '
                  'this poll; if xq runs in a sandbox or container, re-poll outside it first); cancel with '
                  'node "$CC" cancel {} --cwd {}, then poll again, or record finish --id {} --status failed'.format(
                      tid, status, pid, shlex.quote(job), shlex.quote(checkout), tid), file=sys.stderr)
            self.problem = True
            if previous != 'orphaned':
                self.rows.append(self.observe(tid, 'orphaned'))
            return
        if not self.quiet:
            print('{}: {}'.format(tid, status))
        if not closed:
            # Unchanged polls do not refresh the stale clock: a frozen job ages normally, even when
            # its pid is alive (the companion bumps updatedAt only on phase changes), so a long
            # single-phase job needs `ack --id X --hours N` when the stale hook fires.
            if codex_state_changed(task, status, detail):
                heartbeat = dict(event, event='seen')
                for key in ('phase', 'updated'):
                    if detail.get(key) is not None:
                        heartbeat['codex_' + key] = detail[key]
                self.rows.append(heartbeat)
        elif self.explicit and previous != status:
            self.rows.append(event)

    def apply_terminal(self, task, checkout, event, status, args, env):
        tid, job = task['id'], task.get('job')
        terminal = 'done' if status == 'done' else 'failed'
        if task.get('closed'):
            self.rows.append(dict(event, event='finish', status=terminal, preserve_closed=True,
                                  summary='Closed Codex job reported ' + status))
            return
        result = runner.run(args + ['result', job, '--cwd', checkout], env=env)
        if result.returncode:
            self.report_problem(event, 'terminal result unavailable. Reconcile manually.')
            return
        finish = dict(event, event='finish', status=terminal, summary=result.stdout.strip()[:2000])
        if terminal == 'done':
            try:
                finish.update(delivery_fields(self.root, task, report=True, out=self.reports))
            except SystemExit as exc:
                self.report_problem(event, str(exc))
                return
        self.rows.append(finish)
        if self.explicit:
            print('{}: {}'.format(tid, status))


def poll_codex(root, tasks, stamp, *, explicit, quiet=False, notes=None):
    if not tasks:
        return 0
    poll = CodexPoll(root, explicit=explicit, quiet=quiet, notes=notes)
    reads = [entry for entry in map(poll.first_read, tasks) if entry is not None]
    poll.reread(reads)
    for task, checkout, _, read, suspect_pid, _ in reads:
        poll.apply(task, checkout, read, suspect_pid)
    ledger.append_checked(root, poll.rows, stamp)
    # Printed after the append: a LedgerConflict retry under poll --wait recomputes them.
    print_reports(poll.reports)
    return 1 if poll.problem else 0
