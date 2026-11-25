"""The Cloud adapter (ADAPTERS): pushed session branches with status reports."""

from pathlib import Path
import re

from ..core import ledger, repo
from ..core.ledger import snapshot
from ..core.repo import brief_path, origin_default, require_main, resolve_commit
from ..core.state import check_id_collision, valid_branch
from ..core.util import fail, STATUS_DIR
from ..port import runner
from ..port.adapter import adapter_for, ExecutorAdapter
from .cloud_collect import (cloud_collection_dependencies, cloud_status_only, collect_cloud_reports,
                            collected_sibling_statuses)
from .cloud_report import cloud_batch_key, envelope_text


def reprint_envelope(root, a, ids):
    """Print the envelope recorded for earlier cloud assignments again; nothing is appended."""
    with ledger.ledger_lock(root):
        recorded = {row['id']: row for row in ledger.events(root)
                    if row.get('event') == 'assign' and adapter_for(row.get('executor')).batch_reports}
    if any(tid not in recorded for tid in ids):
        fail('--reprint requires previously assigned cloud tasks')
    records = [recorded[tid] for tid in ids]
    if any(not t.get('base') or not t.get('brief') for t in records):
        fail('--reprint requires a recorded envelope with a pinned base and brief')
    first = records[0]
    if any(cloud_batch_key(t) != cloud_batch_key(first) for t in records):
        fail('--reprint tasks must belong to the same envelope')
    main = first.get('main') or require_main(root, a.main)
    if first.get('batch_id'):
        # Keep the order and destination from the original handoff, even
        # when the caller selects a single task or a report bound a branch.
        ids = [tid for tid, row in recorded.items() if row.get('batch_id') == first['batch_id']]
    print(envelope_text(ids, first['brief'], first['base'], main, first.get('branch')))
    return 0


def pushed_envelope_base(root, path, brief, ids, main):
    """Fetch main and check the pushed brief matches the local bytes and names every task; returns the base."""
    base = repo.fetch_branch(main, root)
    remote_blob = runner.run(['git', 'rev-parse', '--verify', base + ':' + brief], cwd=root)
    if remote_blob.returncode:
        fail(brief + ' is not pushed; commit and push it first with user approval')
    if remote_blob.stdout.strip() != runner.git('hash-object', str(path), cwd=root).strip():
        fail('brief differs from pushed bytes; commit and push the exact brief first')
    text = runner.git('show', base + ':' + brief, cwd=root)
    for tid in ids:
        if not re.search(r'^## ' + re.escape(tid) + r'\s*$', text, re.M):
            fail('pushed brief lacks ## ' + tid)
    return base


def check_envelope_request(root, a, ids):
    """Validate a new envelope's brief, branch and ids; returns (main, brief path, brief, ledger snapshot)."""
    if not a.brief:
        fail('cloud-envelope requires --brief unless --reprint was supplied')
    main = require_main(root, a.main)
    if a.branch:
        valid_branch(a.branch, root)
        if a.branch == main:
            fail('--branch must differ from --main')
        if not a.branch.startswith('claude/'):
            fail('--branch must start with claude/')
    path, brief = brief_path(root, a.brief)
    for tid in ids:
        for target in (brief, STATUS_DIR + '/' + tid + '.json'):
            if runner.run(['git', 'check-ignore', '-q', target], cwd=root).returncode == 0:
                fail(target + ' is ignored; briefs/cloud-status must be tracked for cloud use')
    known, stamp = snapshot(root)
    for tid in ids:
        check_id_collision(tid, known)
    for tid in ids:
        if tid in known:
            fail('{} already {}; use a new id'.format(tid, known[tid].get('status', 'recorded')))
    return main, path, brief, stamp


def record_envelope(root, a, ids):
    """Pin the pushed brief and base, append one assign per task and return the envelope text."""
    main, path, brief, stamp = check_envelope_request(root, a, ids)
    base = pushed_envelope_base(root, path, brief, ids, main)
    output = envelope_text(ids, brief, base, main, a.branch)
    # Task IDs are unique across the ledger, so the first ID uniquely names
    # this handoff even when multiple envelopes use the same brief.
    ledger.append_checked(root, [dict(event='assign', id=tid, executor=CloudAdapter.name, brief=brief, branch=a.branch,
                            base=base, main=main, batch=Path(brief).stem, batch_id=ids[0]) for tid in ids], stamp)
    return output


def record_cloud_reports(root, tasks, stamp, a):
    """Collect published reports and append the accepted rows; returns (accepted, relevant, bad, fetch_failed)."""
    accepted, relevant, bad, branches, fetch_failed = collect_cloud_reports(
        root, tasks, branch=a.branch, discover=a.discover, batch=a.batch, main=a.main, emit=print)
    groups = {cloud_batch_key(tasks[row['id']]) for row in accepted}
    heads = {row['head'] for row in accepted if row['status'] == 'done'}

    def dependencies(states):
        return cloud_collection_dependencies(states, groups, heads, branches, a.discover)

    ledger.append_checked(root, accepted, stamp, dependencies=dependencies)
    return accepted, relevant, bad, fetch_failed


def cloud_doctor_checks(root, a, check, settings):
    """doctor --need cloud: origin, the cloud base branch and trackable briefs/cloud-status."""
    has_origin = runner.run(['git', 'remote', 'get-url', 'origin'], cwd=root).returncode == 0
    check('origin', has_origin, 'configured' if has_origin else 'not configured',
          'cloud needs a configured origin and a pushed brief')
    main = settings.get('main') or origin_default(root)
    valid_main = bool(main) and runner.run(['git', 'check-ref-format', '--branch', main], cwd=root).returncode == 0
    check('cloud base branch', valid_main, main if valid_main else 'unset or invalid',
          'set .agents/config.json main or select --main for each cloud command')
    for path in ('.agents/briefs/probe.md', STATUS_DIR + '/probe.json'):
        trackable = runner.run(['git', 'check-ignore', '-q', path], cwd=root).returncode == 1
        check(path, trackable, 'not ignored' if trackable else 'ignored',
              'briefs and cloud-status must not be ignored')


class CloudAdapter(ExecutorAdapter):
    """Cloud sessions: envelopes pinned to a pushed base, results collected from pushed status reports."""
    name = 'cloud'
    batch_reports = True
    delivery_label = 'collected cloud'
    assign_refusal = 'assign --executor cloud is unsupported; use cloud-envelope to pin a pushed brief and base'
    reserved_id_refusal = '{} was assigned to Cloud; use a fresh task id'
    poll_refusal = 'Cloud tasks use cloud-collect'

    @staticmethod
    def reviewed_hits(root, task, tid, head, hits):
        # A batch commits one status per task, so later tasks' ranges contain earlier siblings' status
        # files; those were validated by cloud-collect and stay exempt only while unchanged since then.
        return [path for path in hits if path not in collected_sibling_statuses(root, task, tid, head)]

    @staticmethod
    def check_merge_head(root, task, head):
        if task.get('head') and resolve_commit(root, head) != resolve_commit(root, task['head']):
            fail('verified head must match the collected cloud delivery head; verify it again '
                 'or use --force for explicit reconciliation')

    @staticmethod
    def split_status_commits(root, extra):
        if not extra:
            return extra, []
        # A cloud batch commits its reports after each implementation; cloud-collect validated those.
        cloud_ids = {tid for tid, task in ledger.state(root).items() if task.get('executor') == 'cloud'}
        kept, status_commits = [], []
        for parent, commits in extra:
            reviewed = [c for c in commits if not cloud_status_only(root, c[0], cloud_ids)]
            status_commits += [c for c in commits if c not in reviewed]
            if reviewed:
                kept.append((parent, reviewed))
        return kept, status_commits

    @staticmethod
    def doctor_checks(root, a, check, settings):
        cloud_doctor_checks(root, a, check, settings)
