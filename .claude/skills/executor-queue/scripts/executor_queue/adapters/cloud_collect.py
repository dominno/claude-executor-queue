"""Cloud collection (ADAPTERS): fetch pushed session branches and collect their status reports."""

import json
from pathlib import Path

from ..core import ledger, repo
from ..core.ledger import executor_observation
from ..core.repo import ancestor, require_main
from ..core.review import sensitive_paths
from ..core.util import display_name, fail, ID_RE, STATUS_DIR
from ..port import runner
from .cloud_report import (clean_report, cloud_batch_key, cloud_commit, recorded_cloud_delivery, report_hash,
                           REPORT_MAX_BYTES, validate_report)


def cloud_status_entries(root, ref):
    entries = {}
    output = runner.git('ls-tree', '-r', '-z', ref, '--', STATUS_DIR, cwd=root)
    for entry in filter(None, output.split('\0')):
        metadata, path = entry.split('\t', 1)
        if path.endswith('.json'):
            mode, kind, blob = metadata.split()
            entries[path] = (mode, kind, blob)
    return entries


def cloud_status_only(root, commit, cloud_ids):
    """True when a commit changes only .agents/cloud-status/<cloud task>.json files."""
    paths = runner.git('diff-tree', '--no-commit-id', '--name-only', '-r', '-m', '--root', '-z', commit,
                cwd=root).split('\0')
    paths = [path for path in paths if path]
    prefix = STATUS_DIR + '/'
    return bool(paths) and all(path.startswith(prefix) and path.endswith('.json') and
                               path[len(prefix):-len('.json')] in cloud_ids for path in paths)


def collected_sibling_statuses(root, t, tid, head):
    """STATUS_DIR/<sibling>.json paths whose blob at head is the report cloud-collect recorded for a sibling."""
    key = cloud_batch_key(dict(t, id=tid))
    if key[0] == 'task':
        return set()
    exempt = set()
    for sid, sibling in ledger.state(root).items():
        if (sid == tid or sibling.get('executor') != 'cloud' or not ID_RE.fullmatch(sid) or
                cloud_batch_key(dict(sibling, id=sid)) != key or not sibling.get('report_sha256') or
                (t.get('branch') and sibling.get('branch') != t.get('branch'))):
            continue
        path = STATUS_DIR + '/' + sid + '.json'
        entry = runner.git('ls-tree', '-z', head, '--', path, check=False, cwd=root).rstrip('\0')
        meta, _, name = entry.partition('\t')
        fields = meta.split()
        if name != path or len(fields) != 3 or fields[0] not in ('100644', '100755') or fields[1] != 'blob':
            continue
        try:
            if int(runner.git('cat-file', '-s', fields[2], check=False, cwd=root).strip()) > REPORT_MAX_BYTES:
                continue
            data = json.loads(runner.git('cat-file', 'blob', fields[2], check=False, cwd=root))
        except (ValueError, RecursionError):
            continue
        if report_hash(data) == sibling['report_sha256']:
            exempt.add(path)
    return exempt


def session_status_entries(root, ref, branch, cloud, base_entries):
    entries = cloud_status_entries(root, ref)
    bases = {task['base'] for tid, task in cloud.items() if task.get('base') and
             (task.get('branch') == branch or (not task.get('branch') and STATUS_DIR + '/' + tid + '.json' in entries))}
    branch_history = {}
    for base in sorted(bases):
        if not ancestor(base, ref, root):
            continue
        if base not in base_entries:
            base_entries[base] = cloud_status_entries(root, base)
        if any(base_entries[base].get(name) == entry for name, entry in entries.items()):
            branch_history[base], _ = repo.history_paths(root, base, ref, STATUS_DIR)
    for name, entry in entries.items():
        tid = Path(name).stem
        task = cloud.get(tid)
        # A merged session may leave its reports in the base of later
        # sessions. Check owned reports and skip only untouched carryovers.
        if (task is None or task.get('branch') != branch) and any(
                name not in changed and base_entries[base].get(name) == entry
                for base, changed in branch_history.items()):
            continue
        yield name, tid, entry


def select_cloud_batches(cloud, pending, rejected, *, seen, batch_branches, fetched, fetch_errors,
                         batch_filter, discover):
    """Choose the eligible finish rows using the snapshot and collected facts."""
    bad = {key: list(messages) for key, messages in rejected.items()}

    def group(tid):
        return cloud_batch_key(cloud[tid])

    def problem(tid, message):
        bad.setdefault(group(tid), []).append(message)

    for row in pending:
        tid = row['id']
        destinations = batch_branches.get(cloud[tid].get('batch_id'), ())
        if len(destinations) > 1:
            problem(tid, 'PROBLEM {}: one envelope must use a single session branch'.format(tid))
    batches = {group(tid) for tid, t in cloud.items() if tid in seen or t.get('branch') in fetched}
    if batch_filter and (discover or not batches) and (fetched or not fetch_errors):
        batches.update(group(tid) for tid in cloud)
    relevant = {tid: t for tid, t in cloud.items()
                if tid in seen or t.get('branch') in fetched or (discover and t.get('branch')) or
                (not t.get('branch') and group(tid) in batches)}
    for tid, t in relevant.items():
        if t.get('branch') in fetch_errors:
            problem(tid, 'PROBLEM {}: assigned session branch could not be fetched'.format(tid))
        elif tid not in seen and t.get('status') == 'running':
            problem(tid, 'MISSING ' + tid)
        elif tid not in seen and t.get('report') and t.get('branch') in fetched:
            problem(tid, 'CHANGED {}: recorded report is missing from {}'.format(tid, t['branch']))

    def check_distinct_heads(rows, within_batch):
        owners = {}
        for row in rows:
            tid = row['id']
            key = (group(tid), row['head']) if within_batch else row['head']
            peer = owners.setdefault(key, tid)
            if peer != tid:
                problem(peer, 'PROBLEM {}: done heads must be distinct from {}'.format(peer, tid))
                problem(tid, 'PROBLEM {}: done heads must be distinct from {}'.format(tid, peer))

    # An invalid batch cannot claim heads against unrelated eligible work.
    candidates = [row for row in pending if row['status'] == 'done' and '*' not in bad and group(row['id']) not in bad]
    check_distinct_heads(candidates, within_batch=True)
    check_distinct_heads([row for row in candidates if group(row['id']) not in bad], within_batch=False)
    accepted = [row for row in pending if '*' not in bad and group(row['id']) not in bad]
    return accepted, tuple(relevant), bad


def cloud_collect_branches(root, branch, discover):
    """Branches cloud-collect fetches: --branch, then every claude/* head on origin with --discover."""
    if not branch and not discover:
        fail('cloud-collect requires --branch or --discover')
    if branch and not branch.startswith('claude/'):
        fail('--branch must start with claude/')
    branches = [branch] if branch else []
    if discover:
        for line in runner.git('ls-remote', '--heads', 'origin', 'claude/*', cwd=root).splitlines():
            refname = line.split('\t')[-1]
            if not refname.startswith('refs/heads/claude/'):
                continue
            name = refname[len('refs/heads/'):]
            if name not in branches:
                branches.append(name)
    return branches


def cloud_recorded_heads(root, tasks):
    """(batch id -> assigned session branches, canonical recorded delivery head -> first task id)."""
    batch_branches, historical = {}, {}
    for tid, task in tasks.items():
        if task.get('executor') == 'cloud' and task.get('batch_id') and task.get('branch'):
            batch_branches.setdefault(task['batch_id'], set()).add(task['branch'])
        head = recorded_cloud_delivery(task)
        if head:
            canonical = head if len(head) == len(task.get('base') or '') else cloud_commit(root, head)
            if canonical:
                historical.setdefault(canonical, tid)
    return batch_branches, historical


class CloudCollection:
    """The facts one cloud-collect gathers from fetched session branches (no ledger lock held)."""

    def __init__(self, root, tasks, cloud, *, discover, main, emit):
        self.root, self.tasks, self.cloud = root, tasks, cloud
        self.discover, self.main, self.emit = discover, main, emit
        self.default_main = require_main(root, main) if cloud else None
        self.seen, self.pending, self.fingerprints = set(), [], {}
        self.bad, self.diffs, self.warned = {}, {}, set()
        self.fetched, self.fetch_errors, self.base_entries = set(), set(), {}
        self.batch_branches, self.historical = cloud_recorded_heads(root, tasks)

    def problem(self, tid, message):
        self.bad.setdefault(cloud_batch_key(self.cloud[tid]), []).append(message)

    def warn_changes(self, t, tid, head, ref):
        # Review task implementation separately, and preserve the old safety
        # check for authority changes hidden in status/later commits as well.
        paths = sensitive_paths(self.root, t['base'], head, tid, t.get('brief'), self.diffs)
        paths += sensitive_paths(self.root, head, ref, tid, t.get('brief'), self.diffs)
        key = cloud_batch_key(dict(t, id=tid))
        # Same-batch sibling status files on this ref are validated by this collect as their own reports.
        siblings = {STATUS_DIR + '/' + sid + '.json' for sid, task in self.cloud.items()
                    if sid != tid and key[0] != 'task' and cloud_batch_key(dict(task, id=sid)) == key}
        for path in paths:
            if path in siblings:
                continue
            if path not in self.warned:
                self.emit('WARN SENSITIVE ' + display_name(path) +
                          ' (untrusted changes; inspect before tests/merge)')
                self.warned.add(path)

    def collect_branch(self, branch):
        try:
            ref = repo.fetch_branch(branch, self.root)
        except SystemExit:
            if not self.discover:
                raise
            self.fetch_errors.add(branch)
            # Transport diagnostics may include private origin URLs.
            self.emit('PROBLEM {}: could not fetch discovered branch'.format(display_name(branch)))
            return
        self.fetched.add(branch)
        for name, tid, entry in session_status_entries(self.root, ref, branch, self.cloud, self.base_entries):
            self.collect_entry(name, tid, entry, ref, branch)

    def owned_task(self, name, tid, branch):
        """The selected task a status file belongs to, or None after recording why it is skipped."""
        t = self.cloud.get(tid)
        if tid in self.tasks and tid not in self.cloud and self.tasks[tid].get('executor') == 'cloud':
            return None  # Ignore closed and unselected tasks before validating remote data.
        if tid not in self.cloud and self.discover:
            self.emit('SKIP foreign ' + display_name(tid) + ': no assignment in selected ledger/batch')
            return None
        if name != STATUS_DIR + '/' + tid + '.json' or not ID_RE.fullmatch(tid):
            self.bad.setdefault('*', []).append('PROBLEM {}: invalid status path'.format(display_name(name)))
            return None
        if t is None:
            self.bad.setdefault('*', []).append('PROBLEM {}: status file but no assign in the ledger'.format(tid))
            return None
        self.seen.add(tid)
        if t.get('status') != 'running' and not t.get('report'):
            self.emit('SKIP {}: manually finished; reconcile before import'.format(tid))
            return None
        if t.get('branch') and t['branch'] != branch:
            self.problem(tid, 'PROBLEM {}: report on {} but bound to {}'.format(tid, branch, t['branch']))
            return None
        return t

    def read_report(self, tid, entry):
        """(report JSON, its hash) from a status blob, or None after recording the problem."""
        mode, kind, blob = entry
        if kind != 'blob' or mode not in ('100644', '100755'):
            self.problem(tid, 'PROBLEM {}: status path must be a regular file'.format(tid))
            return None
        size = int(runner.git('cat-file', '-s', blob, cwd=self.root).strip())
        if size > REPORT_MAX_BYTES:
            self.problem(tid, 'PROBLEM {}: report file size limit exceeded (65536 bytes)'.format(tid))
            return None
        try:
            data = json.loads(runner.git('show', blob, cwd=self.root))
            return data, report_hash(data)
        except (ValueError, RecursionError):
            self.problem(tid, 'PROBLEM {}: invalid JSON'.format(tid))
            return None

    def collect_entry(self, name, tid, entry, ref, branch):
        t = self.owned_task(name, tid, branch)
        if t is None:
            return
        report = self.read_report(tid, entry)
        if report is None:
            return
        if t.get('status') != 'running':
            self.recheck_recorded(t, tid, report, ref, branch)
        else:
            self.accept_new(t, tid, report, ref, branch)

    def recheck_recorded(self, t, tid, report, ref, branch):
        """A report already imported: it must be unchanged and its delivery head still on the branch."""
        data, report_sha256 = report
        if t.get('report_sha256', report_hash(t['report'])) != report_sha256:
            status = data.get('status') if isinstance(data, dict) else None
            if status not in ('done', 'failed', 'blocked'):
                status = '?'
            self.problem(tid, 'CHANGED {}: {} -> {} (report differs; reconcile explicitly)'.format(
                tid, t.get('status'), status))
            return
        head = cloud_commit(self.root, t.get('replaced_head') or t.get('head'))
        if not head or not ancestor(head, ref, self.root):
            self.problem(tid, 'CHANGED {}: recorded delivery head is no longer on {}'.format(tid, branch))
        else:
            self.warn_changes(t, tid, head, ref)

    def accept_new(self, t, tid, report, ref, branch):
        """Validate a running task's report and queue its finish row."""
        data, report_sha256 = report
        main = self.main or t.get('main') or self.default_main
        reasons = validate_report(data, tid, dict(t, main=main), ref, self.root, branch)
        if reasons:
            self.problem(tid, 'PROBLEM {}: {}'.format(tid, '; '.join(reasons)))
            return
        data = clean_report(data)
        try:
            json.dumps(data, ensure_ascii=False).encode('utf-8')
        except UnicodeEncodeError:
            self.problem(tid, 'PROBLEM {}: report fields contain invalid Unicode'.format(tid))
            return
        head = cloud_commit(self.root, data['head'])
        if not head:
            self.problem(tid, 'PROBLEM {}: cannot resolve delivery head'.format(tid))
            return
        if data['status'] == 'done':
            prior = self.historical.get(head)
            if prior is not None and prior != tid:
                self.problem(tid, 'PROBLEM {}: done head is already recorded for {}'.format(tid, prior))
                return
        self.warn_changes(t, tid, head, ref)
        if tid in self.fingerprints:
            if not t.get('branch') or self.fingerprints[tid] != report_sha256:
                self.problem(tid, 'CHANGED {}: conflicting reports across session branches; '
                                  'unbound reports require one branch'.format(tid))
            return
        self.fingerprints[tid] = report_sha256
        if t.get('batch_id'):
            self.batch_branches.setdefault(t['batch_id'], set()).add(branch)
        self.pending.append(dict(event='finish', id=tid, status=data['status'], summary=data['summary'],
                                 head=head, tests=data['tests'], report=data, report_sha256=report_sha256,
                                 branch=branch, observation=executor_observation('cloud', data['status'])))


def collect_cloud_reports(root, tasks, *, branch, discover, batch, main, emit):
    """Acquire published reports without holding the ledger lock."""
    cloud = {k: t for k, t in tasks.items()
             if t.get('executor') == 'cloud' and not t.get('closed') and (not batch or t.get('batch') == batch)}
    branches = cloud_collect_branches(root, branch, discover)
    collection = CloudCollection(root, tasks, cloud, discover=discover, main=main, emit=emit)
    for name in branches:
        collection.collect_branch(name)
    for tid, t in cloud.items():
        if branch and t.get('branch') and t['branch'] not in branches and tid not in collection.seen:
            emit('UNCOLLECTED {}: bound to {} (not selected)'.format(tid, t['branch']))
    accepted, relevant, bad = select_cloud_batches(
        cloud, collection.pending, collection.bad, seen=collection.seen, batch_branches=collection.batch_branches,
        fetched=collection.fetched, fetch_errors=collection.fetch_errors, batch_filter=batch, discover=discover)
    return accepted, relevant, bad, branches, bool(collection.fetch_errors)


def cloud_collection_dependencies(states, groups, heads, branches, discover):
    relevant_tasks = {}
    for tid, task in states.items():
        if task.get('executor') != 'cloud':
            continue
        branch = task.get('branch')
        in_group = cloud_batch_key(task) in groups and (discover or not branch or branch in branches)
        delivery = recorded_cloud_delivery(task)
        conflicts = delivery and any(head.startswith(delivery) for head in heads)
        if in_group or conflicts:
            relevant_tasks[tid] = (task.get('status'), branch, task.get('closed'), task.get('head'),
                                   task.get('report_sha256'))
    return relevant_tasks
