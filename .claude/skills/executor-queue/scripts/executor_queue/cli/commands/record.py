"""CLI commands that record verdicts and outcomes: finish, verify, merge, ack and close."""

import datetime
import re
import shlex
import sys

from ...adapters.codex_worktree import delivery_fields, existing_worktree
from ...core import ledger
from ...core.config import config
from ...core.ledger import append, ledger_mutation
from ...core.repo import (ancestor, brief_relative, commit_parents, commit_subjects, merged_extra_commits,
                          origin_default, require_main, resolve_commit)
from ...core.review import (print_sensitive, scope_review, sensitive_paths, sensitive_review, sensitive_review_base,
                            sensitive_warning)
from ...core.state import require_task, valid_id
from ...core.util import EXECUTORS, fail, INTEGRATING_EXECUTORS, listed_paths, short
from ...port import runner
from ...port.adapter import adapter_for
from ...port.runner import worktree_git


@ledger_mutation
def cmd_finish(a, root):
    task = require_task(root, a, ('running', 'queued'), 'finish', closed_running=True)
    event = dict(event='finish', id=a.id, status=a.status, summary=(a.summary or '')[:2000], force=a.force)
    worktree_done = adapter_for(task.get('executor')).worktree_delivery and a.status == 'done'
    if worktree_done and task.get('status') == 'queued' and not a.force:
        fail('a Codex task that never launched cannot be done; close --id {} --reason dropped'.format(a.id))
    if a.head and (not worktree_done or not a.force):
        fail('finish --head requires --force and a successful Codex task')
    if a.head:
        event['head'] = resolve_commit(root, a.head)
    elif worktree_done and not task.get('closed'):
        event.update(delivery_fields(root, task, report=True))
    if task.get('closed'):
        event['preserve_closed'] = True
    append(root, event)
    return 0


ACK_SENSITIVE_UNUSED = ('--ack-sensitive applies only to a Codex or cloud pass with a recorded base; {} has no '
                        'sensitive-path review. Drop the flag.')


def check_verify_request(a, t):
    """Refuse a verdict the task or the flags do not allow; returns (reader, evidence)."""
    if t.get('merged') and not a.force:
        fail(a.id + ' is already merged; use --force only for explicit reconciliation')
    reader, evidence = a.by.strip(), a.evidence.strip()
    if not reader or not evidence:
        fail('--by and --evidence must be nonempty')
    if reader.casefold() in EXECUTORS and not a.force:
        fail('--by must name a different reader, not an executor name')
    status = t.get('status')
    if a.verdict == 'pass' and status != 'done' and not a.force:
        fail('{} is {}; a pass verdict needs a done task; record reject/fixes or use --force for reconciliation'
             .format(a.id, status))
    if a.ack_sensitive and (a.verdict != 'pass' or t.get('executor') not in INTEGRATING_EXECUTORS):
        fail(ACK_SENSITIVE_UNUSED.format(a.id))
    if a.accept_scope is not None and (a.verdict != 'pass' or not a.accept_scope.strip()):
        fail('--accept-scope "<reason>" applies only to a pass verdict and needs a nonempty reason')
    return reader, evidence


def replaced_delivery(root, t, a, adapter, head, delivery):
    """The recorded delivery an explicit --head replaces, or None; refuses an unrelated head without --force."""
    if not (a.head and t.get('executor') in INTEGRATING_EXECUTORS and delivery):
        return None
    recorded_head = resolve_commit(root, delivery)
    if head == recorded_head:
        return None
    # A head that descends from the recorded delivery (lead fixes, late executor commits) is
    # re-reviewed from the review base like any delivery; anything else needs --force.
    if not a.force and not ancestor(recorded_head, head, root):
        fail('verification head must match the {} delivery head or descend from it (got {}, recorded '
             'delivery {}); use --force for explicit reconciliation'.format(
                 adapter.delivery_label, short(head), short(recorded_head)))
    return recorded_head


@ledger_mutation
def cmd_verify(a, root):
    t = require_task(root, a, ('done', 'failed', 'blocked'), 'verify')
    reader, evidence = check_verify_request(a, t)
    event = dict(event='verify', id=a.id, by=reader, verdict=a.verdict, evidence=evidence[:8000], force=a.force)
    if a.verdict != 'pass':
        # A rejection retires work; it never inspects (or requires) the worktree.
        head = resolve_commit(root, a.head) if a.head else t.get('head')
        if head:
            event['verified_head'] = head
        append(root, event)
        return 0
    adapter = adapter_for(t.get('executor'))
    checkout = t.get('worktree') or str(root)
    next_verify = 'xq.py verify --id {} --by {} --verdict pass --head <sha> --evidence ...'.format(
        a.id, shlex.quote(reader))
    delivery, pin_delivery = adapter.verify_delivery(root, t, a, checkout, next_verify)
    if not a.head and not delivery:
        head = worktree_git(existing_worktree(t, root, 'verify'), 'rev-parse', 'HEAD').strip()
    else:
        head = a.head or delivery
    head = resolve_commit(root, head)
    replacing = replaced_delivery(root, t, a, adapter, head, delivery)
    adapter.check_verified_head(root, t, a, head, checkout, next_verify)
    if t.get('executor') in INTEGRATING_EXECUTORS:
        review_base = sensitive_review_base(root, t, head)
        if review_base:
            sensitive_review(root, t, a, head, event, review_base, replacing=replacing)
        elif a.ack_sensitive:
            fail(ACK_SENSITIVE_UNUSED.format(a.id))
    scope_review(root, t, a, head, event, adapter.scope_paths(root, t, head))
    event['verified_head'] = head
    # A Codex pass pins the delivery it approved when none was recorded (forced or not), and a Codex/cloud
    # pass on a different head (a descendant, or any head with --force) replaces the recorded delivery,
    # so a later merge can match it.
    if pin_delivery:
        event['head'] = head
    elif replacing:
        event['head'] = head
        event['replaced_head'] = t.get('replaced_head') or replacing
        print('NOTE {}pass replaces recorded delivery {} with {}'.format(
            'forced ' if a.force else '', short(replacing), short(head)))
    append(root, event)
    return 0


@ledger_mutation
def cmd_merge(a, root):
    t = require_task(root, a, ('done',), 'merge')
    if not a.force and t.get('merged'):
        fail(a.id + ' is already merged; use --force to reconcile')
    if not a.force and t.get('verdict') != 'pass':
        fail('{0} verdict is {1} (by {2}); merge needs pass: xq.py verify --id {0} --verdict pass --head <sha> ..., '
             'or start a fix attempt'.format(a.id, t.get('verdict') or 'none', t.get('by') or '-'))
    sha = resolve_commit(root, a.commit)
    runner.git('cat-file', '-e', sha + '^{commit}', cwd=root)
    head = t.get('verified_head')
    if not a.force:
        if not head:
            fail('{} has no verified head; record xq.py verify --id {} --verdict pass --head <sha> ...'.format(
                a.id, a.id))
        if not ancestor(head, sha, root):
            main_hint = a.main or config(root).get('main') or origin_default(root) or '<main>'
            fail('commit {} does not contain verified head {}; merge it into {} first, then xq.py merge --id {} '
                 '--commit <merge sha>'.format(short(sha), short(head), main_hint, a.id))
        adapter_for(t.get('executor')).check_merge_head(root, t, head)
        main = require_main(root, a.main)
        if not ancestor(sha, 'refs/heads/' + main, root):
            fail('merge commit {} is not reachable from the default branch {}; merge into {} first, or pass '
                 '--main <branch> if another branch is the integration target'.format(short(sha), main, main))
    extra = merged_extra_commits(root, sha, head) if head else []
    extra, status_commits = adapter_for(t.get('executor')).split_status_commits(root, extra)
    if status_commits:
        print('WARN {} cloud status commit(s) after verified head {}: {}; inspect all intervening commits'.format(
            len(status_commits), short(head), '; '.join('{} {}'.format(short(c), subject)
                                                         for c, subject in status_commits[:5])))
    for parent, commits in extra:
        listing = '; '.join('{} {}'.format(short(c), subject) for c, subject in commits[:5])
        more = ' (+{} more)'.format(len(commits) - 5) if len(commits) > 5 else ''
        hits = [path for path in sensitive_paths(root, head, parent, a.id, brief=brief_relative(root, t.get('brief')))]
        sensitive = '; SENSITIVE: ' + listed_paths(hits, 10) if hits else ''
        message = ('merge {} brings {} commit(s) from {} that are not part of verified head {}: {}{}{}'.format(
            short(sha), len(commits), short(parent), short(head), listing, more, sensitive))
        if not a.force:
            fail('{}; review those commits, then verify again: xq.py verify --id {} --by <reader> --verdict pass '
                 '--evidence ... --head {}{}, then rerun merge (or merge --force)'.format(
                     message, a.id, parent, ' --ack-sensitive (after inspecting the SENSITIVE paths)'
                     if hits and t.get('executor') in INTEGRATING_EXECUTORS else ''))
        print('WARN ' + message, file=sys.stderr)
    print_sensitive([sensitive_warning(path) for path in t.get('sensitive') or []])
    if t.get('sensitive_more'):
        print('WARN SENSITIVE (+{} more; see the verify event)'.format(t['sensitive_more']), file=sys.stderr)
    if head and not extra and len(commit_parents(root, sha)) < 2:
        after = commit_subjects(root, sha, '^' + head)
        if after:
            print('WARN {} commit(s) after verified head {}: {}; inspect all intervening commits'.format(
                len(after), short(head), '; '.join('{} {}'.format(short(c), s) for c, s in after[:5])))
    review_base = t.get('review_base') or t.get('base') or head
    messages = runner.git('log', '--format=%B', (review_base + '..' + sha) if review_base else sha, cwd=root)
    for tid, peer in ledger.state(root).items():
        if (tid != a.id and peer.get('verdict') != 'pass' and peer.get('reason') != 'superseded-by:' + a.id and
                re.search(r'(?<![\w.-])' + re.escape(tid) + r'(?![\w.-])', messages)):
            print('WARN commit names non-pass task ' + tid)
    append(root, dict(event='merge', id=a.id, commit=sha, force=a.force))
    return 0


@ledger_mutation
def cmd_ack(a, root):
    require_task(root, a, ('running',), 'ack')
    try:
        until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=a.hours)
    except OverflowError:
        fail('--hours is too large for an acknowledgement deadline')
    append(root, dict(event='seen', id=a.id, ack_until=until.strftime('%Y-%m-%dT%H:%M:%SZ')))
    return 0


@ledger_mutation
def cmd_close(a, root):
    valid_id(a.id)
    adapter_for((ledger.state(root).get(a.id) or {}).get('executor')).pre_close_import(root, a.id)
    t = require_task(root, a, ('queued', 'done', 'failed', 'blocked'), 'close')
    reason = a.reason
    if reason not in ('dropped', 'rejected'):
        if not reason.startswith('superseded-by:'):
            fail('reason must be dropped, rejected or superseded-by:<id>')
        target = reason.split(':', 1)[1]
        valid_id(target)
        if target.casefold() == a.id.casefold():
            fail('superseded-by target cannot be {} itself'.format(a.id))
        if target not in ledger.state(root):
            fail('superseding task "{0}" is not in the ledger; record it first (xq.py assign --id {0} '
                 '--executor {1} --brief <brief> [--queued]), then rerun close'.format(
                     target, t.get('executor') or '<executor>'))
    adapter_for(t.get('executor')).close_guard(root, a, reason)
    # Nothing was delivered: no commit, no recorded base for one, no result file.
    undelivered = (t.get('status') in ('failed', 'blocked') and
                   (t.get('not_sent') or not any(t.get(key) for key in ('head', 'delivery_base', 'result'))))
    if t.get('status') != 'queued' and not t.get('verdict') and not undelivered and not a.force:
        if reason.startswith('superseded-by:'):
            fail('close requires a verdict for work that ran: xq.py verify --id {} --by <reader> --verdict '
                 'fixes|reject --evidence "<what failed>", then rerun close (or close --force for '
                 'reconciliation)'.format(a.id))
        fail('close requires a verdict for work that ran: xq.py verify --id {} --by <reader> --verdict reject '
             '--evidence "..." (or close --force for reconciliation)'.format(a.id))
    append(root, dict(event='close', id=a.id, reason=reason, force=a.force))
    return 0
