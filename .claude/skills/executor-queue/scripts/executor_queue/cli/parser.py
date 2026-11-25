"""The argparse parser: every subcommand with its options, help text and handler (set as fn)."""

import argparse
import math
import re

from .. import VERSION
from ..core.util import EXECUTORS, LOCAL_EXECUTOR_LIMITS, TASK_STATES
from .commands.executors import cmd_agy_sync, cmd_cloud_collect, cmd_cloud_envelope, cmd_poll
from .commands.read import cmd_hook, cmd_pause, cmd_status
from .commands.record import cmd_ack, cmd_close, cmd_finish, cmd_merge, cmd_verify
from .commands.setup import cmd_assign, cmd_brief_lint, cmd_compose, cmd_import, cmd_init, cmd_preflight, doctor


def positive_hours(value):
    try:
        hours = float(value)
    except ValueError:
        hours = 0
    if not math.isfinite(hours) or hours <= 0:
        raise argparse.ArgumentTypeError('must be a finite positive number of hours')
    return hours


def acknowledgement_hours(value):
    hours = positive_hours(value)
    if hours > 8760:
        raise argparse.ArgumentTypeError('must be at most 8760 hours')
    return hours


STATUS_FIELDS = ('review_base', 'base', 'head', 'verified_head', 'worktree', 'job')


def parse_duration(value):
    match = re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)([smh]?)', value.strip())
    if not match:
        raise argparse.ArgumentTypeError('use a duration such as 30s, 10m or 1h')
    seconds = float(match.group(1)) * {'': 1, 's': 1, 'm': 60, 'h': 3600}[match.group(2)]
    if not math.isfinite(seconds):
        raise argparse.ArgumentTypeError('use a duration such as 30s, 10m or 1h')
    return seconds


def wait_timeout(value):
    seconds = parse_duration(value)
    if not 0 < seconds <= 24 * 3600:
        raise argparse.ArgumentTypeError('must be greater than 0 and at most 24h')
    return seconds


def wait_interval(value):
    seconds = parse_duration(value)
    if not 5 <= seconds <= 24 * 3600:
        raise argparse.ArgumentTypeError('must be at least 5s and at most 24h')
    return seconds


# The top-level --help description.
DESCRIPTION = """Offline executor ledger (stdlib, Python 3.8+).

Use the project's own ledger/queue when it already has one. xq records work, never
starts executors, sends files, commits, pushes, merges, or invents tasks. poll and
doctor query installed tools; cloud commands fetch refs from the configured origin.
"""

EXIT_CODES = '''exit codes:
  0  success (status/poll/collect found nothing needing attention)
  1  refused or failed: an "xq: ..." message, preflight refused, doctor MISSING,
     or poll/agy-sync/cloud-collect reported a PROBLEM
  2  usage error, or hook found work needing attention (Stop hook blocks)
  3  poll --wait timed out before the task reached a terminal state'''


def add_setup_commands(command, task_id):
    """doctor, preflight, init and assign."""
    s = command('doctor', 'check git, ignores, plugins, executor auth and the Codex default model', doctor)
    s.add_argument('--need', help='comma-separated executors to check: codex,agy,cloud (default: all)')
    s.add_argument('--offline', action='store_true', help='run local checks without executor authentication probes')
    s = command('preflight', 'check load, disk and running executor counts before a launch', cmd_preflight)
    s.add_argument('--executor', choices=tuple(LOCAL_EXECUTOR_LIMITS), required=True, help='executor about to be launched')
    s.add_argument('--worktree', help='absolute worktree about to be launched (Codex): also check its base is not '
                                      'behind the default branch (config stale_base)')
    s = command('init', 'create .agents/ and exclude private ledger state', cmd_init)
    s.add_argument('--local', action='store_true', help='also exclude briefs/status (disables cloud handoff)')
    s = command('import', 'import an external append-only JSONL ledger as read-only history', cmd_import)
    s.add_argument('--from', dest='source', required=True, help='JSONL ledger to import (one event per line)')
    s.add_argument('--map', help='JSON mapping xq fields to source keys, e.g. {"id": "task_id", "status": "state", '
                   '"status_values": {"ok": "done"}}; default: config "import_map"')
    s.add_argument('--dry-run', action='store_true', help='report what would be imported; write nothing')
    s = command('assign', 'record a launched (or --queued backlog) task', cmd_assign)
    s.add_argument('--id', required=True, help=task_id)
    s.add_argument('--executor', choices=EXECUTORS, required=True, help='executor running the task')
    s.add_argument('--brief', required=True, help='brief path, relative to the primary checkout or absolute')
    s.add_argument('--note', help='free-text note')
    s.add_argument('--follows', help='earlier attempt (same executor and worktree) this one fixes; the sensitive-path '
                                     'review then also covers its commits')
    s.add_argument('--job', help='Codex companion job id (requires --worktree)')
    s.add_argument('--worktree', help='absolute Codex worktree; its HEAD is recorded as the assignment base')
    s.add_argument('--model', help='model passed to the executor (recorded, not enforced)')
    s.add_argument('--effort', help='reasoning effort passed to the executor (recorded, not enforced)')
    s.add_argument('--queued', action='store_true', help='record backlog only; promote with a later assign')
    s.add_argument('--force', action='store_true', help='start a new attempt over unverified finished work; also '
                                                         'bypasses an enforced brief_lint or stale_base refusal')
    s.add_argument('--prompt', help='composed prompt file actually launched (from compose); records prompt_sha256')
    s = command('brief-lint', 'check a brief: one Result:, final ## Acceptance with Files:/Checks:/Notes:, '
                'configured restricted words and header fields', cmd_brief_lint)
    s.add_argument('--brief', required=True, help='brief path, relative to the primary checkout or absolute')
    s.add_argument('--enforce', action='store_true', help='exit 1 on warnings too')
    s.add_argument('--json', action='store_true', help='emit versioned JSON')
    s = command('compose', 'write the launch prompt: configured context_files, then the brief', cmd_compose)
    s.add_argument('--brief', required=True, help='brief path, relative to the primary checkout or absolute')
    s.add_argument('--out', help='output path (default .agents/review/<brief stem>.prompt.md); never xq state or '
                   'config, and an existing file only when it is a *.prompt.md')


def add_record_commands(command, task_id, force):
    """finish, verify, merge, ack and close: verdicts and outcomes the lead records."""
    s = command('finish', 'record the actual result of a running task', cmd_finish)
    s.add_argument('--id', required=True, help=task_id)
    s.add_argument('--force', action='store_true', help=force)
    s.add_argument('--status', required=True, choices=('done', 'failed', 'blocked'), help='actual outcome')
    s.add_argument('--summary', help='actual result summary (max 2000 characters)')
    s.add_argument('--head', help='reviewed Codex commit for forced reconciliation')
    s = command('verify', 'record a reader verdict on finished work', cmd_verify)
    s.add_argument('--id', required=True, help=task_id)
    s.add_argument('--force', action='store_true', help=force)
    s.add_argument('--by', required=True, help='reader who checked the work (not an executor name)')
    s.add_argument('--verdict', required=True, choices=('pass', 'fixes', 'reject'),
                   help='pass needs a done task; fixes/reject never touch the worktree')
    s.add_argument('--head', help='Codex: the reviewed, committed worktree commit (required for pass)')
    s.add_argument('--evidence', required=True, help='checks actually run and what they showed')
    s.add_argument('--ack-sensitive', action='store_true',
                   help='Codex pass: confirm you inspected the listed SENSITIVE paths (agent, hook, CI, manifest, '
                        'symlink changes); recorded in the ledger')
    s.add_argument('--accept-scope', metavar='REASON',
                   help='pass: accept changed paths outside the brief\'s declared Files (needed when scope_check is '
                        'enforce); recorded as scope_accepted with the out_of_scope list')
    s = command('merge', 'record an integration already performed in Git', cmd_merge)
    s.add_argument('--id', required=True, help=task_id)
    s.add_argument('--force', action='store_true', help=force)
    s.add_argument('--commit', required=True, help='resulting merge commit on the default branch')
    s.add_argument('--main', help='default branch when origin/HEAD is unset and config has no "main"')
    s = command('ack', 'acknowledge long-running work so the hook stops flagging it', cmd_ack)
    s.add_argument('--id', required=True, help=task_id)
    s.add_argument('--hours', type=acknowledgement_hours, default=4, help='acknowledge for this many hours')
    s.set_defaults(force=False)
    s = command('close', 'retire a ledger entry', cmd_close)
    s.add_argument('--id', required=True, help=task_id)
    s.add_argument('--reason', required=True, help='dropped | rejected | superseded-by:<id> (assign <id> first)')
    s.add_argument('--force', action='store_true', help=force)


def add_read_commands(command):
    """status, hook and pause: reading the ledger and steering the Stop hook."""
    s = command('status', 'read recorded task status without contacting executors', cmd_status)
    s.description = ('Read this repository’s ledger. Provider observations are recorded evidence. '
                     'Text columns show ID, executor, state, verdict, reader, merge commit and flags.')
    s.add_argument('--id', help='show one exact task ID with its recorded worktree, base, review_base, job, head, '
                                'verified_head and brief')
    s.add_argument('--executor', choices=EXECUTORS, help='show tasks for this executor')
    s.add_argument('--state', choices=TASK_STATES, help='filter the recorded ledger state')
    s.add_argument('--open', action='store_true', help='show tasks that still need attention')
    s.add_argument('--stale-hours', type=positive_hours, default=4, help='mark running tasks stale after this many hours')
    s.add_argument('--json', action='store_true', help='emit versioned JSON with task metadata')
    s.add_argument('--field', choices=STATUS_FIELDS,
                   help='with --id: print one recorded value bare, for shell use (review_base falls back to base, '
                        'the start of the reviewed range)')
    s = command('hook', 'Stop hook: flag unverified, stale or idle work (exit 2)', cmd_hook)
    s.add_argument('--stale-hours', type=positive_hours, default=4, help='flag running tasks older than this')
    s = command('pause', 'pause idle/stale enforcement for an executor', cmd_pause)
    s.add_argument('--executor', choices=EXECUTORS, required=True, help='executor to pause or resume')
    s.add_argument('--resume', action='store_true', help='resume instead of pausing')


def add_executor_commands(command):
    """poll, agy-sync and the cloud commands, which consult executors."""
    s = command('poll', 'refresh recorded Codex jobs and local agy artifacts', cmd_poll)
    s.add_argument('--id', help='refresh just this recorded task. Use cloud-collect for Cloud.')
    s.add_argument('--executor', choices=tuple(LOCAL_EXECUTOR_LIMITS), help='refresh only this executor')
    s.add_argument('--wait', action='store_true',
                   help='with --id: re-poll until the task is done/failed/blocked, a PROBLEM, or --timeout')
    s.add_argument('--timeout', type=wait_timeout, default=None,
                   help='--wait limit such as 30s, 9m or 1h (default 9m, below the 10-minute Bash tool limit; '
                        'max 24h; exit 3 on timeout, then repeat)')
    s.add_argument('--interval', type=wait_interval, default=None,
                   help='--wait re-poll interval (default 30s, min 5s)')
    s = command('agy-sync', 'import locally available agy artifacts', cmd_agy_sync)
    s.add_argument('--delegate-exit', help='<id>=<exit code> of a plain delegate without a queue archive')
    s.add_argument('--out', help='absolute result path of that delegate')
    s = command('cloud-envelope', 'pin a pushed brief and base, record cloud tasks, print the /goal envelope',
                cmd_cloud_envelope)
    s.add_argument('--brief', help='tracked, pushed batch brief with ## <id> sections')
    s.add_argument('--tasks', required=True, nargs='+', help='task ids in execution order')
    s.add_argument('--branch', help='bind the batch to this claude/ session branch')
    s.add_argument('--main', help='branch holding the pushed brief (default: config main or origin/HEAD)')
    s.add_argument('--reprint', action='store_true', help='print a recorded envelope again without reassigning')
    s = command('cloud-collect', 'fetch and import published cloud reports', cmd_cloud_collect)
    s.add_argument('--branch', help='claude/ session branch to fetch')
    s.add_argument('--discover', action='store_true', help="discover and collect origin's claude/* branches")
    s.add_argument('--batch', help='only collect this batch')
    s.add_argument('--main', help='default branch when origin/HEAD is unset and config has no "main"')


def build_parser():
    p = argparse.ArgumentParser(prog='xq', description=DESCRIPTION, epilog=EXIT_CODES,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--version', action='version', version='%(prog)s ' + VERSION)
    sub = p.add_subparsers(dest='cmd', required=True, metavar='command')
    task_id = 'task id (1-120 ASCII letters/digits/._-)'
    force = 'explicit reconciliation: skip order/delivery checks (recorded in the ledger)'

    def command(name, text, fn, **options):
        parser = sub.add_parser(name, help=text, description=text, epilog=EXIT_CODES,
                                formatter_class=argparse.RawDescriptionHelpFormatter, **options)
        parser.set_defaults(fn=fn)
        return parser

    add_setup_commands(command, task_id)
    add_record_commands(command, task_id, force)
    add_read_commands(command)
    add_executor_commands(command)
    return p
