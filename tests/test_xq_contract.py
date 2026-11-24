"""Offline contract tests for the user-level xq skill (never modifies that skill).

Run: python3 -m pytest tests/test_xq_contract.py -q
Git commits/pushes below are fixture data, confined to tmp_path and a bare file
remote. No project refs, executor queues, user Git config, or network are used.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'skills/executor-queue/scripts/xq.py'


@pytest.fixture
def xq():
    if not SCRIPT.is_file():
        pytest.skip(f'user-level skill is not installed at {SCRIPT}')
    spec = importlib.util.spec_from_file_location('xq_skill_under_test', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def queue(xq, tmp_path, monkeypatch):
    # Ignore inherited Git worktree/index/config settings and all global hooks.
    for key in tuple(os.environ):
        if key.startswith('GIT_'):
            monkeypatch.delenv(key)
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(home))
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    monkeypatch.setenv('GIT_ALLOW_PROTOCOL', 'file')
    monkeypatch.setenv('GIT_TERMINAL_PROMPT', '0')
    repo = tmp_path / 'repo'
    repo.mkdir()
    monkeypatch.chdir(repo)
    monkeypatch.setattr(xq, 'resource_readings', lambda root: dict(
        load=0, cpus=2, repo_free_gb=10, temp_free_gb=10))
    monkeypatch.setattr(xq, 'live_process_counts', lambda: dict(codex=0, agy=0))

    def git(*args):
        return subprocess.run(
            ['git', '-c', f'core.hooksPath={os.devnull}', '-c', 'commit.gpgsign=false',
             '-c', 'tag.gpgsign=false', *args],
            check=True, capture_output=True, text=True, timeout=15,
        ).stdout.strip()

    git('init', '-b', 'main')
    git('config', 'user.email', 'xq-test@example.invalid')
    git('config', 'user.name', 'XQ Fixture')
    git('config', 'core.hooksPath', os.devnull)
    brief = 'briefs/batch-one.md'
    (repo / brief).parent.mkdir()
    # Critic item 14: every requested cloud id needs an exact pushed heading.
    (repo / brief).write_text(
        '## c001\nFirst task.\n## c002\nSecond task.\n'
        '## a-bad\nInvalid report task.\n## z-good\nValid report task.\n',
        encoding='utf-8',
    )
    git('add', brief)
    git('commit', '-m', 'fixture base')
    remote = tmp_path / 'origin.git'
    git('init', '--bare', str(remote))
    git('remote', 'add', 'origin', str(remote))
    git('push', 'origin', 'main')
    git('remote', 'set-head', 'origin', 'main')
    base = git('rev-parse', 'HEAD')

    def cli(*args):
        monkeypatch.setattr(sys, 'argv', [str(SCRIPT), *args])
        return xq.main()

    def envelope(*ids, path=brief, branch='claude/test'):
        return cli('cloud-envelope', '--brief', path, '--tasks', *ids, '--branch', branch)

    def status(task='c001'):
        return dict(task=task, status='done', summary='Fixture result',
                    tests=[{'cmd': 'pytest', 'result': 'pass'}], head=base,
                    base=base, utc='2026-11-08T21:00:00Z')

    def publish(files, branch='claude/test'):
        git('checkout', '-B', branch, 'main')
        for tid, data in files.items():
            if isinstance(data, dict):
                data.setdefault('branch', branch)
                if data.get('head') == data.get('base') or data.get('head') == base:
                    marker = 'impl-' + tid
                    (repo / marker).write_text(tid)
                    git('add', marker)
                    git('commit', '-m', 'implement ' + tid)
                    data['head'] = git('rev-parse', 'HEAD')
        folder = repo / xq.STATUS_DIR
        folder.mkdir(parents=True, exist_ok=True)
        for task, data in files.items():
            (folder / f'{task}.json').write_text(
                data if isinstance(data, str) else json.dumps(data), encoding='utf-8')
        git('add', xq.STATUS_DIR)
        git('commit', '-m', 'fixture cloud status')
        git('push', 'origin', branch)
        git('checkout', 'main')

    return SimpleNamespace(xq=xq, repo=repo, git=git, base=base, brief=brief,
                           cli=cli, envelope=envelope, status=status, publish=publish)


def assign(q, task='local', executor='codex'):
    if executor == 'cloud':
        # Historical/synthetic cloud lifecycle fixture; new CLI assignments use envelopes.
        q.xq.append(q.repo, dict(event='assign', id=task, executor=executor, brief=q.brief, note='fixture'))
        return
    assert q.cli('assign', '--id', task, '--executor', executor,
                 '--brief', q.brief, '--note', 'fixture') == 0


def finish(q, task='local', status='done'):
    assert q.cli('finish', '--id', task, '--status', status, '--summary', 'Delivered') == 0


def verify(q, task='local', verdict='pass'):
    assert q.cli('verify', '--id', task, '--by', 'independent-reader',
                 '--verdict', verdict, '--evidence', 'Checked fixture diff') == 0


def collect(q, *args):
    return q.cli('cloud-collect', '--branch', 'claude/test', *args)


def test_init_and_empty_status(queue, capsys):
    q = queue
    assert q.cli('init') == 0
    assert (q.repo / '.agents/briefs').is_dir()
    assert q.xq.events(q.repo) == []
    capsys.readouterr()
    assert q.cli('status') == 0
    assert capsys.readouterr().out == 'no tasks\n'


@pytest.mark.parametrize('executor', ['codex', 'agy', 'cloud'])
def test_assign_finish_verify_merge_status(queue, capsys, executor):
    q = queue
    assign(q, executor=executor)
    assert q.xq.state(q.repo)['local']['status'] == 'running'
    finish(q)
    verify(q)
    assert q.cli('merge', '--id', 'local', '--commit', q.base) == 0
    events = q.xq.events(q.repo)
    assert [e['event'] for e in events] == ['assign', 'finish', 'verify', 'merge']
    assert all(e['ts'].endswith('Z') for e in events)
    assert events[0]['note'] == 'fixture'
    assert events[1]['summary'] == 'Delivered'
    assert events[2]['evidence'] == 'Checked fixture diff'
    task = q.xq.state(q.repo)['local']
    assert task['executor'] == executor
    assert task['status'] == 'done'
    assert task['by'] == 'independent-reader'
    assert task['verdict'] == 'pass'
    assert task['merged'] == q.base
    assert q.cli('status') == 0
    output = capsys.readouterr().out
    for value in ('local', executor, 'done', 'pass', 'independent-reader', q.base):
        assert value in output
    assert q.cli('status', '--open') == 0
    assert capsys.readouterr().out == 'no tasks\n'


def test_open_status_shows_running_and_unverified_done(queue, capsys):
    q = queue
    assign(q, 'running')
    assign(q, 'delivered')
    finish(q, 'delivered')
    assign(q, 'checked')
    finish(q, 'checked')
    verify(q, 'checked')
    assert q.cli('status', '--open') == 0
    ids = [line.split()[0] for line in capsys.readouterr().out.splitlines()]
    assert ids == ['running', 'delivered', 'checked']


def test_assign_refuses_running_duplicate_without_append(queue):
    assign(queue)
    before = queue.xq.events(queue.repo)
    with pytest.raises(SystemExit, match='already running'):
        assign(queue)
    assert queue.xq.events(queue.repo) == before


@pytest.mark.parametrize('status', ['done', 'failed', 'blocked'])
def test_hook_reports_unverified_finish(queue, capsys, status):
    assign(queue, executor='cloud')
    finish(queue, status=status)
    assert queue.cli('hook') == 2
    output = capsys.readouterr()
    assert output.out == ''
    assert 'finished but not verified: local' in output.err


@pytest.mark.parametrize('executor', ['codex', 'agy'])
def test_hook_reports_idle_queued_executor(queue, capsys, executor):
    assign(queue, executor=executor)
    finish(queue)
    verify(queue)
    # Critic item 1: past use alone is not idle work; an actual backlog is.
    assert queue.cli('hook') == 0
    assert queue.cli('assign', '--id', 'next', '--executor', executor,
                     '--brief', queue.brief, '--queued') == 0
    if executor == 'agy':
        (queue.repo / '.agents/config.json').write_text('{"keep_busy":["agy"]}')
    assert queue.cli('hook') == 2
    message = capsys.readouterr().err
    assert f'{executor} idle; next queued item next' in message
    assert 'not verified' not in message


@pytest.mark.parametrize('busy', [False, True])
def test_hook_quiet_for_empty_or_busy_queue(queue, capsys, busy):
    if busy:
        assign(queue, 'codex-task', 'codex')
        assign(queue, 'agy-task', 'agy')
    assert queue.cli('hook') == 0
    output = capsys.readouterr()
    assert output.out == output.err == ''


def test_cloud_envelope_pins_base_and_records_each_task(queue, capsys):
    q = queue
    assert q.envelope('c001', 'c002') == 0
    output = capsys.readouterr()
    assert f'Base commit {q.base}.' in output.out
    assert f'"base":"{q.base}"' in output.out
    assert q.brief in output.out
    assert 'tasks c001 → c002' in output.out
    assert 'claude/test' in output.out
    assert '.agents/cloud-status/<task>.json' in output.out
    # Critic items 11, 12 and 17: v2 expands these rules into separate clauses.
    for rule in ('never push main.', 'Do not open PRs or push other branches.',
                 'only if the user authorized this batch',
                 'git remote get-url --push --all origin', 'origin is configured as a mirror',
                 'git push --no-follow-tags --recurse-submodules=no origin "HEAD:refs/heads/$session_branch"',
                 'Before pushing, inspect all outgoing commits',
                 'Limit network access to authorized Git operations on that origin.'):
        assert rule in output.out
    assert 'git push origin HEAD' not in output.out
    assert 'recorded 2 assign events' in output.err
    rows = q.xq.events(q.repo)
    assert [e['id'] for e in rows] == ['c001', 'c002']
    for event in rows:
        assert event.items() >= dict(event='assign', executor='cloud', base=q.base,
                                     brief=q.brief, branch='claude/test', batch='batch-one').items()


@pytest.mark.parametrize('kind', ['missing', 'untracked', 'committed-not-pushed'])
def test_cloud_envelope_requires_pushed_brief(queue, kind):
    q = queue
    path = 'briefs/new.md'
    if kind != 'missing':
        (q.repo / path).write_text('## c001\nLocal only.\n')
    if kind == 'committed-not-pushed':
        q.git('add', path)
        q.git('commit', '-m', 'unpublished brief fixture')
    with pytest.raises(SystemExit, match='not found|commit and push it first'):
        q.envelope('c001', path=path)
    assert q.xq.events(q.repo) == []


@pytest.mark.parametrize('duplicate', ['within-batch', 'running', 'done'])
def test_cloud_envelope_refuses_duplicate_ids_atomically(queue, duplicate):
    q = queue
    ids = ('c001', 'c001')
    if duplicate != 'within-batch':
        q.envelope('c001')
        if duplicate == 'done':
            finish(q, 'c001')
        # A fresh id before the duplicate must not be partially assigned.
        ids = ('c002', 'c001')
    before = q.xq.events(q.repo)
    with pytest.raises(SystemExit, match='distinct|already running|already done'):
        q.envelope(*ids)
    assert q.xq.events(q.repo) == before


@pytest.mark.parametrize('status', ['done', 'failed', 'blocked'])
@pytest.mark.parametrize('prefix_length', [7, 12, 40])
def test_cloud_collect_valid_status_and_base_prefix(queue, capsys, status, prefix_length):
    q = queue
    q.envelope('c001')
    data = q.status()
    data.update(status=status, base=q.base[:prefix_length])
    q.publish({'c001': data})
    capsys.readouterr()
    assert collect(q) == 0
    output = capsys.readouterr().out
    assert f'cloud progress: {status} 1' in output
    assert 'PROBLEM' not in output
    result = q.xq.events(q.repo)[-1]
    assert result['event'] == 'finish'
    for key in ('status', 'summary', 'tests', 'head'):
        assert result[key] == data[key]
    assert not q.xq.state(q.repo)['c001'].get('verdict')
    before = q.xq.events(q.repo)
    assert collect(q) == 0
    assert q.xq.events(q.repo) == before  # Repeated collection is idempotent.


@pytest.mark.parametrize('base', ['wrong-sha', 'short-prefix', 'not-a-sha', ''])
def test_cloud_collect_rejects_wrong_or_invalid_base(queue, capsys, base):
    q = queue
    q.envelope('c001')
    data = q.status()
    if base == 'wrong-sha':
        base = ('0' if q.base[0] != '0' else '1') * 40
    elif base == 'short-prefix':
        base = q.base[:6]
    data['base'] = base
    q.publish({'c001': data})
    before = q.xq.events(q.repo)
    capsys.readouterr()
    assert collect(q) == 1
    # Critic item 15: reasons are explicit, without v1's extra separator space.
    assert 'PROBLEM c001: base mismatch' in capsys.readouterr().out
    assert q.xq.events(q.repo) == before


@pytest.mark.parametrize('field', ['task', 'status', 'summary', 'tests', 'head', 'base', 'utc'])
def test_cloud_collect_rejects_each_missing_field(queue, capsys, field):
    q = queue
    q.envelope('c001')
    data = q.status()
    del data[field]
    q.publish({'c001': data})
    before = q.xq.events(q.repo)
    capsys.readouterr()
    assert collect(q) == 1
    assert f'PROBLEM c001: missing {field}' in capsys.readouterr().out
    assert q.xq.events(q.repo) == before


@pytest.mark.parametrize('field,value', [('task', 'different-id'), ('status', 'running')])
def test_cloud_collect_rejects_mismatched_task_or_invalid_status(queue, capsys, field, value):
    q = queue
    q.envelope('c001')
    data = q.status()
    data[field] = value
    q.publish({'c001': data})
    before = q.xq.events(q.repo)
    capsys.readouterr()
    assert collect(q) == 1
    assert 'PROBLEM c001:' in capsys.readouterr().out
    assert q.xq.events(q.repo) == before


def test_cloud_collect_rejects_status_without_assignment(queue, capsys):
    q = queue
    q.publish({'orphan': q.status('orphan')})
    assert collect(q) == 1
    assert 'orphan: status file but no assign in the ledger' in capsys.readouterr().out
    assert q.xq.events(q.repo) == []


def test_cloud_collect_rejects_invalid_json_atomically(queue, capsys):
    q = queue
    q.envelope('a-bad', 'z-good')
    q.publish({'a-bad': '{invalid', 'z-good': q.status('z-good')})
    before = q.xq.events(q.repo)
    capsys.readouterr()
    assert collect(q) == 1
    output = capsys.readouterr().out
    assert 'PROBLEM a-bad: invalid JSON' in output
    # Critic item 15: validate every report before writing any finish event.
    assert 'cloud progress: running 2' in output
    assert q.xq.state(q.repo)['a-bad']['status'] == 'running'
    assert q.xq.state(q.repo)['z-good']['status'] == 'running'
    assert q.xq.events(q.repo) == before


@pytest.mark.parametrize('raw', ['null', '[]', '42'])
def test_cloud_collect_rejects_non_object_json_atomically(queue, capsys, raw):
    q = queue
    q.envelope('a-bad', 'z-good')
    q.publish({'a-bad': raw, 'z-good': q.status('z-good')})
    before = q.xq.events(q.repo)
    capsys.readouterr()
    assert collect(q) == 1
    assert 'PROBLEM a-bad: expected JSON object' in capsys.readouterr().out
    # Critic item 15: a valid peer report cannot cause a partial import.
    assert q.xq.state(q.repo)['a-bad']['status'] == 'running'
    assert q.xq.state(q.repo)['z-good']['status'] == 'running'
    assert q.xq.events(q.repo) == before


def two_batches(q):
    other = 'briefs/batch-two.md'
    (q.repo / other).write_text('## c002\nSecond batch.\n')
    q.git('add', other)
    q.git('commit', '-m', 'second brief fixture')
    q.git('push', 'origin', 'main')
    q.envelope('c001')
    q.envelope('c002', path=other)
    data = q.status('c002')
    data['base'] = q.git('rev-parse', 'HEAD')
    # Critic item 15: the implementation head must descend from its pinned base.
    data['head'] = data['base']
    first = q.status()
    first['base'] = data['base']
    q.publish({'c001': first, 'c002': data})


def test_cloud_collect_batch_filters_progress(queue, capsys):
    two_batches(queue)
    capsys.readouterr()
    assert collect(queue, '--batch', 'batch-one') == 0
    assert 'cloud progress: done 1\n' in capsys.readouterr().out


def test_cloud_collect_batch_does_not_finish_other_batch(queue):
    two_batches(queue)
    assert collect(queue, '--batch', 'batch-one') == 0
    assert queue.xq.state(queue.repo)['c001']['status'] == 'done'
    assert queue.xq.state(queue.repo)['c002']['status'] == 'running'


def test_reassigned_task_needs_fresh_verification(queue, capsys):
    # Critic item 18 fixes XQ-2: every reassignment starts without old approvals.
    q = queue
    assign(q, executor='cloud')
    finish(q)
    verify(q)
    q.cli('merge', '--id', 'local', '--commit', q.base)
    assign(q, executor='cloud')
    finish(q)
    assert q.cli('hook') == 2
    assert 'finished but not verified: local' in capsys.readouterr().err
    task = q.xq.state(q.repo)['local']
    assert not task.get('verdict')
    assert not task.get('merged')
