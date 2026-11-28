"""The executor adapter interface (PORT): ExecutorAdapter and the ADAPTERS registry lookup."""

from ..core.review import main_fork_review_base


# --- PORT: executor adapter interface -----------------------------------------------------------------------------

class ExecutorAdapter:
    """What CORE and the CLI may ask of one executor; this base class is the behaviour of none.

    Adapters are registered by name in ADAPTERS (by the adapters package) and looked up with
    adapter_for(). Their methods call module-level functions by name at call time, so a test that
    monkeypatches <function> on its defining module still reaches the adapter's behaviour.

    Capabilities (class attributes):
      name                        executor name recorded in the ledger (None: no registered executor)
      plugin                      (installed file, marketplaces) for plugin_binary(), or None
      skill                       (skill name, launcher inside it) searched before the plugin, or None
      worktree_delivery           the delivery is a commit in a local worktree xq inspects and pins
      inherits_review_base        assign inherits the review base of earlier unpassed attempts
      batch_reports               tasks run in envelopes on pushed session branches (cloud-collect)
      observes_running_only       poll --wait has nothing to observe once the attempt is not running
      idle_alert_needs_keep_busy  the Stop hook flags this executor idle only when keep_busy names it
      polled                      plain `poll` (no --executor, no --id) refreshes this executor
      delivery_label              how a verify refusal names the recorded delivery
      queued_hint                 next step for a queued task ({0} = id), or None for the generic hint
      assign_refusal              why `assign` cannot record this executor, or None
      reserved_id_refusal         why an id once assigned to this executor cannot be reused ({} = id), or None
      poll_refusal                why `poll` cannot refresh this executor's tasks, or None
      heartbeat_fields            task fields a 'seen' heartbeat of this executor records (replaced by the next one)
      private_state               repository directories holding this executor's private state (never committed)

    Operations (static methods; the base versions do nothing or report nothing):
      runner_note(root)                                 NOTE printed before a launch, or None
      check_assign_artifacts(root, a, previous)         refuse reused artifacts; True: a run before promotion
      import_promoted_run(root, tid)                    import that run's outcome; returns the exit code
      check_assign_launch(a)                            refuse an assignment this executor cannot run
      launch_suffix(a)                                  text added to assign worktree refusals
      assign_worktree(root, a, known, previous, wt)     (assignment base, primary checkout forced)
      assign_event_fields(root, a)                      extra fields for the assign event
      live_process(name, tokens)                        True when a live process is one of this executor's runs
      poll(root, task, stamp, quiet, notes)             refresh one recorded task (poll --id)
      poll_all(root, explicit)                          refresh every recorded task of this executor
      check_wait(tid, task)                             refuse poll --wait for a task with nothing to wait for
      verify_delivery(root, t, a, checkout, next_verify)
                                                        (delivery, pin) for a pass: the recorded delivery
                                                        (or one pinned now) and whether the pass records
                                                        its verified head as the delivery
      check_verified_head(root, t, a, head, checkout, next_verify)
                                                        refuse a pass head this executor cannot deliver
      sensitive_review_base(root, task, head, main)     start of the reviewed range
      reviewed_hits(root, task, tid, head, hits)        committed sensitive hits that still need review
      local_sensitive(root, task, tid, brief)           sensitive uncommitted/untracked/ignored local paths
      scope_paths(root, task, head)                     paths the delivery changes for the declared-Files check:
                                                        git diff --name-only over the sensitive review range less
                                                        changes already on main, plus uncommitted local paths and
                                                        untracked ones that are not leftovers past a clean commit;
                                                        None: no delivered range
      worktree_base(root, worktree)                     the worktree's current base commit (what a fresh assignment
                                                        records; the stale-base check tests it), refusing a
                                                        foreign checkout; None: no worktree deliveries
      preflight_worktree(root, worktree)                (refusals, warnings) for preflight --worktree before the
                                                        stale-base check (Codex: the primary checkout is refused)
      check_merge_head(root, task, head)                refuse a merge whose verified head is not the delivery
      split_status_commits(root, extra)                 (commits to review, status-only commits) beyond head
      doctor_checks(root, a, check, settings)           doctor lines for --need <name>
      pre_close_import(root, tid)                       import a finish close depends on (inside close's lock)
      close_guard(root, a, reason)                      refuse or warn before close
    """
    name = None
    plugin = None
    skill = None
    worktree_delivery = False
    inherits_review_base = False
    batch_reports = False
    observes_running_only = False
    idle_alert_needs_keep_busy = False
    polled = False
    delivery_label = None
    queued_hint = None
    assign_refusal = None
    reserved_id_refusal = None
    poll_refusal = None
    heartbeat_fields = ()
    private_state = ()

    @staticmethod
    def runner_note(root):
        return None

    @staticmethod
    def check_assign_artifacts(root, a, previous):
        return False

    @staticmethod
    def import_promoted_run(root, tid):
        return 0

    @staticmethod
    def check_assign_launch(a):
        pass

    @staticmethod
    def launch_suffix(a):
        return ''

    @staticmethod
    def assign_worktree(root, a, known, previous, worktree):
        return None, False

    @staticmethod
    def assign_event_fields(root, a):
        return {}

    @staticmethod
    def live_process(name, tokens):
        return False

    @staticmethod
    def poll(root, task, stamp, *, quiet=False, notes=None):
        # Historical wording: only Codex registrations are polled through a companion.
        if not quiet:
            print('{}: no running Codex registration'.format(task['id']))
        return 0

    @staticmethod
    def poll_all(root, explicit):
        return 0

    @staticmethod
    def check_wait(tid, task):
        pass

    @staticmethod
    def verify_delivery(root, t, a, checkout, next_verify):
        return t.get('head'), False

    @staticmethod
    def check_verified_head(root, t, a, head, checkout, next_verify):
        pass

    @staticmethod
    def sensitive_review_base(root, task, head, main=None):
        # A recorded base (Cloud bases are pinned by cloud-envelope from the pushed main) is authoritative.
        return task.get('review_base') or task.get('base') or main_fork_review_base(root, task, head, main)

    @staticmethod
    def reviewed_hits(root, task, tid, head, hits):
        return hits

    @staticmethod
    def local_sensitive(root, task, tid, brief):
        return []

    @staticmethod
    def scope_paths(root, task, head):
        return None

    @staticmethod
    def worktree_base(root, worktree):
        return None

    @staticmethod
    def preflight_worktree(root, worktree):
        return [], []

    @staticmethod
    def check_merge_head(root, task, head):
        pass

    @staticmethod
    def split_status_commits(root, extra):
        return extra, []

    @staticmethod
    def doctor_checks(root, a, check, settings):
        pass

    @staticmethod
    def pre_close_import(root, tid):
        pass

    @staticmethod
    def close_guard(root, a, reason):
        pass


# Executor name -> adapter, filled in by the adapters package. EXECUTORS (argparse choices) lists the same names in
# the same order.
ADAPTERS = {}


def adapter_for(name):
    """The registered adapter for an executor name read from the ledger; ExecutorAdapter for anything else."""
    return ADAPTERS.get(name, ExecutorAdapter) if isinstance(name, str) else ExecutorAdapter


def heartbeat_fields():
    """Every registered adapter's heartbeat fields, in registry order."""
    return tuple(key for adapter in ADAPTERS.values() for key in adapter.heartbeat_fields)
