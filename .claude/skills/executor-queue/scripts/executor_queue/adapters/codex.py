"""The Codex adapter (ADAPTERS): companion jobs in local worktrees."""

import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile

from ..core.ledger import snapshot
from ..core.repo import ancestor, resolve_commit, same_worktree
from ..core.review import main_fork_review_base, sensitive_review_base
from ..core.util import display_name, fail, listed_paths, OPTION_RE, SHA_RE, short
from ..port import discovery, runner
from ..port.adapter import ExecutorAdapter
from ..port.discovery import plugin_project_root, shell_assignment
from ..port.runner import worktree_git, WORKTREE_GIT_CONFIG
from .codex_poll import codex_model_check, poll_codex
from .codex_worktree import (codex_delivery, codex_scope_paths, DEPENDENCY_DIRS, removed_worktree_head,
                             same_repository, untracked_leftovers, worktree_scan, worktree_sensitive)


# --- ADAPTERS: Codex ----------------------------------------------------------------------------------------------

CODEX_NON_TASK = ('--version', '--help', 'login', 'logout', 'status', 'completion', 'app-server')


def primary_checkout_message(root, tid):
    """Why Codex must not run in the primary checkout, with the command that creates a worktree instead."""
    suggestion = shlex.quote(str(root.parent / '{}-{}'.format(root.name, tid))) if tid else '<worktree>'
    return ('Codex must not run in the primary checkout {} (it could write the xq ledger and '
            '.agy-bridge results); create one: git -C {} worktree add -b xq/{} {} HEAD'.format(
                shlex.quote(str(root)), shlex.quote(str(root)), tid or '<id>', suggestion))


def shared_dependency_warnings(worktree):
    """WARN lines for top-level dependency trees that are symlinks (allowed: shared read-only environments)."""
    warnings = []
    for name in DEPENDENCY_DIRS:
        path = Path(worktree) / name
        if path.is_symlink():
            warnings.append('shared dependency {} is a symlink to {}; keep its target outside the worktree and '
                            'read-only, and ignore it without a trailing slash (references/codex.md)'.format(
                                name, display_name(os.readlink(str(path)))[:200]))
    return warnings


def codex_worktree_checks(root, a, known, worktree):
    """Refuse the primary checkout and a worktree another Codex task is running in; True for a forced primary."""
    primary = same_worktree(worktree, str(root))
    if primary:
        message = primary_checkout_message(root, a.id)
        if not a.force:
            fail(message)
        print('WARN ' + message + ' (forced: its xq/agy-bridge state is reviewed as untrusted)', file=sys.stderr)
    sharing = [(tid, task) for tid, task in sorted(known.items()) if tid != a.id and task.get('executor') == 'codex'
               and task.get('status') in ('running', 'queued') and same_worktree(task.get('worktree'), worktree)]
    running = [tid for tid, task in sharing if task.get('status') == 'running']
    if running and not a.queued and not a.force:
        other = running[0]
        fail('Codex task {0} is still running in worktree {1}; poll --id {0} / finish --id {0} --status failed '
             'first, or use a separate worktree (--force overrides)'.format(other, shlex.quote(worktree)))
    for tid, task in sharing:
        if task.get('status') == 'queued' or a.queued or a.force:
            print('WARN Codex task {} ({}) is also pinned to worktree {}; launch one task per worktree'.format(
                tid, task.get('status'), shlex.quote(worktree)), file=sys.stderr)
    return primary


def assignment_base(root, worktree, previous):
    """Commit the Codex worktree started from; deliveries must move past it."""
    if runner.run(['git', *WORKTREE_GIT_CONFIG, 'rev-parse', '--git-dir'], cwd=worktree).returncode:
        fail('--worktree {} is not a Git checkout of this repository'.format(shlex.quote(worktree)))
    same_repository(root, worktree)
    if (previous.get('status') == 'queued' and previous.get('executor') == 'codex' and
            previous.get('worktree') == worktree and previous.get('base')):
        return previous['base']  # The backlog entry pinned it before launch.
    base = worktree_git(worktree, 'rev-parse', '--verify', '--quiet', 'HEAD^{commit}', check=False).strip()
    if not SHA_RE.fullmatch(base):
        fail('Codex worktree {} needs an initial commit'.format(shlex.quote(worktree)))
    return base


def codex_doctor_checks(root, a, check, settings):
    """doctor --need codex: Node, the Codex CLI and plugin, auth, the default model and the job state root."""
    node = shutil.which('node')
    check('Node', node, node or 'not on PATH', 'install Node.js and put node on PATH')
    codex = shutil.which('codex')
    check('Codex CLI', codex, codex or 'not on PATH', 'npm install -g @openai/codex; codex login [--device-auth]')
    cc = discovery.plugin_binary('codex')
    check('Codex plugin', cc, cc or 'not installed',
          '/plugin marketplace add openai/codex-plugin-cc; /plugin install codex@openai-codex', plugin=True)
    if cc:
        print(shell_assignment('CC', cc))
        if a.offline:
            print('SKIP Codex auth (--offline)')
            print('SKIP Codex default model (--offline; ask the user for a model or run doctor online)')
        else:
            r = runner.run(['node', cc, 'setup', '--json'])
            try:
                details = json.loads(r.stdout).get('auth', {})
                auth, method = details.get('loggedIn'), details.get('authMethod')
            except (ValueError, AttributeError):
                auth, method = False, None
            logged_in = r.returncode == 0 and auth is True
            check('Codex auth', logged_in, 'logged in' if logged_in else 'not logged in',
                  'codex login (or codex login --device-auth), then /codex:setup')
            if isinstance(method, str) and OPTION_RE.fullmatch(method):
                print('Codex auth method: ' + method)
            codex_model_check(check)
        state_root = os.environ.get('CLAUDE_PLUGIN_DATA')
        if state_root:
            print('Codex job state root: CLAUDE_PLUGIN_DATA=' + state_root)
        else:
            print('Codex job state root: {} (TMPDIR fallback)'.format(
                Path(tempfile.gettempdir()) / 'codex-companion'))
            print('WARN CLAUDE_PLUGIN_DATA is unset; Codex jobs launched from Claude Code may be invisible '
                  'to this shell (xq poll also tries the root recorded at assign)')


NO_COMMIT_BEYOND_BASE = 'no commit beyond recorded base {}; commit the delivery in {}, then {}'


def fail_unpinned_delivery(root, t, recorded, next_verify):
    """A worktree delivery with no pinned commit and no --head: name what the worktree holds and stop."""
    found = codex_delivery(root, t, 'verify')
    checkout, tip = found['scan']['checkout'], found.get('head') or found['delivery_base']
    base = t.get('base') or recorded
    if found['dirty_paths']:
        problem = 'has uncommitted/untracked paths (untrusted executor-created paths: {})'.format(
            listed_paths(found['dirty_paths']))
        step = 'review and commit the named files'
    elif tip == base:
        problem, step = 'has no commit beyond base {}'.format(short(base)), 'review and commit the delivery'
    else:
        problem, step = 'is clean at {}'.format(short(tip)), 'review it'
        next_verify = next_verify.replace('<sha>', tip)
    fail('the Codex delivery has no pinned commit: worktree {} {}; next: git -C {} status, {}, then {}'.format(
        display_name(checkout), problem, shlex.quote(checkout), step, next_verify))


def pinned_worktree_delivery(root, t, a, recorded, checkout, next_verify):
    """The clean worktree commit (or --head of a removed worktree) a pass pins as the delivery."""
    if not recorded:
        fail('Codex delivery has no recorded commit; reconcile with finish --force --head')
    if not a.head:
        fail_unpinned_delivery(root, t, recorded, next_verify)
    if t.get('worktree') and not os.path.isdir(t['worktree']):
        # The worktree was removed after the delivery commit: --head names it, nothing is left dirty.
        delivery = removed_worktree_head(root, t, a.head)
        found = {'dirty_paths': []}
    else:
        found = codex_delivery(root, t, 'verify')
        delivery = found.get('head') or found['delivery_base']
    base = t.get('base') or recorded
    # Unrelated history is reported first: committing leftovers would not fix it.
    if delivery != base and not ancestor(base, delivery, root):
        fail('HEAD {} is not a descendant of base {}'.format(delivery, base))
    if found['dirty_paths']:
        fail('worktree {} has uncommitted/untracked paths (untrusted executor-created paths: {}); commit or '
             'remove them (git -C {} status), then rerun {}'.format(
                 checkout, listed_paths(found['dirty_paths']), shlex.quote(checkout), next_verify))
    if delivery == base:
        fail(NO_COMMIT_BEYOND_BASE.format(base, checkout, next_verify))
    return delivery


def check_beyond_base(root, t, head, checkout, next_verify):
    """Every Codex pass path (recorded clean head, pinned delivery, explicit --head)
    must deliver at least one commit on top of the assignment base."""
    if head == t['base']:
        fail(NO_COMMIT_BEYOND_BASE.format(t['base'], checkout, next_verify))
    if not ancestor(t['base'], head, root):
        fail('HEAD {} is not a descendant of base {}; use --force only for explicit reconciliation'
             .format(head, t['base']))


def codex_verify_delivery(root, t, a, checkout, next_verify):
    """(delivery, pin) for a pass: the recorded commit, or the worktree commit pinned now; pin says the
    pass records its verified head as the delivery (forced or not) because none was recorded."""
    done = t.get('status') == 'done'
    delivery, recorded = t.get('head'), t.get('delivery_base')
    if done and delivery and delivery == t.get('base'):
        # Older ledgers pinned a no-op finish at the assignment base; treat it as unpinned.
        delivery, recorded = None, recorded or delivery
    if not a.force and done and not delivery:
        return pinned_worktree_delivery(root, t, a, recorded, checkout, next_verify), True
    return delivery, not delivery


def codex_check_verified_head(root, t, a, head, checkout, next_verify):
    """A non-forced pass of a done task must deliver commits beyond the assignment base."""
    if not a.force and t.get('status') == 'done' and t.get('base'):
        check_beyond_base(root, t, head, checkout, next_verify)


class CodexAdapter(ExecutorAdapter):
    """Codex companion jobs in a dedicated worktree; the delivery is a commit xq inspects and pins there."""
    name = 'codex'
    plugin = ('scripts/codex-companion.mjs', ('openai-codex',))
    worktree_delivery = True
    inherits_review_base = True
    observes_running_only = True
    polled = True
    delivery_label = 'recorded Codex'
    queued_hint = 'after launch: assign --id {0} --executor codex --brief <b> --job <jobId> --worktree <wt>'
    heartbeat_fields = ('codex_phase', 'codex_updated')

    @staticmethod
    def verify_delivery(root, t, a, checkout, next_verify):
        return codex_verify_delivery(root, t, a, checkout, next_verify)

    @staticmethod
    def check_verified_head(root, t, a, head, checkout, next_verify):
        codex_check_verified_head(root, t, a, head, checkout, next_verify)

    @staticmethod
    def check_assign_launch(a):
        if not a.queued and not a.worktree and not a.force:
            fail('Codex runs need a worktree: git worktree add <wt> -b xq/{0} <branch>; then xq.py assign --id {0} '
                 '--executor codex --brief {1} --job {2} --worktree <wt>'.format(
                     a.id, shlex.quote(a.brief), shlex.quote(a.job) if a.job else '<jobId>'))

    @staticmethod
    def launch_suffix(a):
        if a.queued:
            return ''
        return '; the Codex job {} is already running; rerun this assign once the path is fixed'.format(
            display_name(a.job)[:200] if a.job else '<jobId>')

    @staticmethod
    def assign_worktree(root, a, known, previous, worktree):
        collision = next((task for task in known.values() if a.job and
                          task.get('executor') == 'codex' and task.get('status') in ('running', 'queued') and
                          task.get('job') == a.job and same_worktree(task.get('worktree'), worktree)), None)
        if collision:
            fail('Codex job and worktree already registered to active task {} ({}{})'.format(
                collision['id'], collision.get('status'), ', closed' if collision.get('closed') else ''))
        primary = codex_worktree_checks(root, a, known, worktree)
        return assignment_base(root, worktree, previous), primary

    @staticmethod
    def assign_event_fields(root, a):
        if not a.job:
            return {}
        fields = {'codex_project': str(plugin_project_root() or root)}
        state_root = os.environ.get('CLAUDE_PLUGIN_DATA')
        if state_root and os.path.isdir(state_root):
            fields['codex_state_root'] = str(Path(state_root).resolve())
        return fields

    @staticmethod
    def live_process(name, tokens):
        if name == 'codex':
            return not any(t in CODEX_NON_TASK for t in tokens[1:2])
        if name in ('node', 'nodejs'):
            companion = any(t == 'codex-companion.mjs' and tokens[i + 1:i + 2] in (['task'], ['task-worker'])
                            for i, t in enumerate(tokens))
            shim = tokens[1:2] == ['codex'] and not any(t in CODEX_NON_TASK for t in tokens[2:3])
            return companion or shim or 'codex.js' in tokens
        return False

    @staticmethod
    def poll(root, task, stamp, *, quiet=False, notes=None):
        if task.get('status') != 'running':
            return ExecutorAdapter.poll(root, task, stamp, quiet=quiet, notes=notes)
        return poll_codex(root, [task], stamp, explicit=True, quiet=quiet, notes=notes)

    @staticmethod
    def poll_all(root, explicit):
        known, stamp = snapshot(root)
        running = [task for task in known.values()
                   if task.get('executor') == 'codex' and task.get('status') == 'running']
        return poll_codex(root, running, stamp, explicit=explicit)

    @staticmethod
    def check_wait(tid, task):
        if task.get('status') == 'queued':
            fail('{0} is queued (not launched); nothing to wait for. After launch: assign --id {0} --executor codex '
                 '--brief <b> --job <jobId> --worktree <wt>'.format(tid))

    @staticmethod
    def sensitive_review_base(root, task, head, main=None):
        return main_fork_review_base(root, task, head, main)

    @staticmethod
    def local_sensitive(root, task, tid, brief):
        if task.get('worktree') and os.path.isdir(task['worktree']):
            # Untracked and ignored files (conftest.py, *.pth, hooks) run during the lead's checks too.
            return worktree_sensitive(root, dict(task, id=tid), worktree_scan(root, task, 'verify'), brief)
        return []

    @staticmethod
    def scope_paths(root, task, head):
        local = task.get('worktree') and os.path.isdir(task['worktree'])
        scan = worktree_scan(root, task, 'verify') if local else None
        if scan and (scan['head'] != head or untracked_leftovers(root, task, scan)):
            # Untracked leftovers past a clean commit (the lead's check output too) are not the delivery.
            scan = dict(scan, untracked=[])
        return codex_scope_paths(root, task, sensitive_review_base(root, task, head), head, scan)

    @staticmethod
    def worktree_base(root, worktree):
        return assignment_base(root, worktree, {})

    @staticmethod
    def preflight_worktree(root, worktree):
        if same_worktree(worktree, str(root)):
            return [primary_checkout_message(root, None)], []
        return [], shared_dependency_warnings(worktree)

    @staticmethod
    def check_merge_head(root, task, head):
        if not task.get('head') or resolve_commit(root, head) != resolve_commit(root, task['head']):
            fail('verified head must match the recorded Codex delivery head; '
                 'reconcile with finish --force --head or use merge --force')

    @staticmethod
    def doctor_checks(root, a, check, settings):
        codex_doctor_checks(root, a, check, settings)
