"""Offline tests for the configurable ledger path and `xq.py import` (existing-ledger hand-off)."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/executor-queue/scripts/xq.py'

BRIEF = '''# Imported follow-up
Result: The app module prints a greeting.

## Acceptance
Files: src/app.py
Checks: python -m pytest -q
Notes: notes/app.md
'''

MAP = {'id': 'task_id', 'executor': 'agent', 'status': 'state', 'brief': 'brief_path', 'ts': 'timestamp',
       'status_values': {'complete': 'done', 'error': 'failed', 'active': 'running', 'archived': 'closed'}}

SAMPLE = [
    {'task_id': 'old-1', 'agent': 'claude-code', 'state': 'active', 'brief_path': 'briefs/a.md',
     'timestamp': '2026-01-02T10:00:00Z'},
    {'task_id': 'old-1', 'agent': 'claude-code', 'state': 'complete', 'timestamp': '2026-01-02T11:00:00Z'},
    {'task_id': 'old-2', 'agent': 'codex', 'state': 'active', 'timestamp': '2026-01-03T09:00:00Z'},
    {'task_id': 'old-3', 'agent': 'agy', 'state': 'archived', 'timestamp': '2026-01-04T09:00:00Z'},
]


@pytest.fixture
def h(tmp_path, monkeypatch, isolated_queue_environment):
    repo = tmp_path / 'repo'
    repo.mkdir()
    monkeypatch.chdir(repo)
    spec = importlib.util.spec_from_file_location('xq_ledger_under_test', SCRIPT)
    xq = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(xq)
    monkeypatch.setattr(xq, 'resource_readings', lambda root: dict(load=0, cpus=2, repo_free_gb=10, temp_free_gb=10))
    monkeypatch.setattr(xq, 'live_process_counts', lambda samples=None: dict(codex=0, agy=0))
    repo = repo.resolve()

    def git(*args, cwd=None):
        return subprocess.run(['git', '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=' + os.devnull, *args],
                              cwd=str(cwd or repo), capture_output=True, text=True, check=True,
                              timeout=15).stdout.strip()

    git('init', '-q', '-b', 'main')
    git('config', 'user.email', 'fixture@example.invalid')
    git('config', 'user.name', 'Fixture')
    (repo / 'README.md').write_text('fixture\n', encoding='utf-8')
    git('add', '.')
    git('commit', '-q', '-m', 'base')

    def cli(*args):
        monkeypatch.setattr(sys, 'argv', ['xq', *args])
        return xq.main()

    def configure(**data):
        (repo / '.agents').mkdir(exist_ok=True)
        (repo / '.agents/config.json').write_text(json.dumps(data), encoding='utf-8')

    def source(lines=None, malformed=True):
        path = tmp_path / 'external.jsonl'
        rows = [json.dumps(row) for row in (SAMPLE if lines is None else lines)]
        if malformed:
            rows.insert(2, '{"task_id": "broken"')
        path.write_text('\n'.join(rows) + '\n', encoding='utf-8')
        return str(path)

    def brief():
        path = repo / '.agents/briefs/t1.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(BRIEF, encoding='utf-8')
        return '.agents/briefs/t1.md'

    def exclude():
        return (repo / '.git/info/exclude').read_text(encoding='utf-8').split('\n')

    return SimpleNamespace(**locals())


def test_default_ledger_path_and_lock_unchanged(h):
    assert h.xq.ledger_path(h.repo) == h.repo / '.agents/ledger.jsonl'
    assert h.xq.lock_path(h.repo) == h.repo / '.agents/ledger.lock'
    assert h.xq.private_paths(h.repo) == h.xq.PRIVATE_PATHS


def test_custom_ledger_outside_agents_is_used_and_not_excluded(h, capsys):
    h.configure(ledger='history/xq.jsonl')
    assert h.cli('init') == 0
    assert 'ledger history/xq.jsonl is outside .agents/: not excluded' in capsys.readouterr().out
    assert (h.repo / 'history/xq.jsonl').is_file()
    assert not (h.repo / '.agents/ledger.jsonl').exists()
    assert '/history/xq.lock' in h.exclude() and '/history/xq.jsonl' not in h.exclude()
    assert '/.agents/ledger.jsonl' not in h.exclude()
    assert h.cli('assign', '--id', 'n1', '--executor', 'agy', '--brief', h.brief(), '--queued') == 0
    rows = [json.loads(line) for line in (h.repo / 'history/xq.jsonl').read_text().splitlines()]
    assert [row['id'] for row in rows] == ['n1']
    assert (h.repo / 'history/xq.lock').exists() and not (h.repo / '.agents/ledger.lock').exists()
    assert set(h.xq.state(h.repo)) == {'n1'}


def test_custom_ledger_inside_agents_is_excluded(h, capsys):
    h.configure(ledger='./.agents/xq/history.jsonl')
    assert h.cli('init') == 0
    assert 'outside .agents/' not in capsys.readouterr().out
    assert '/.agents/xq/history.jsonl' in h.exclude() and '/.agents/xq/history.lock' in h.exclude()


def test_doctor_reports_the_active_ledger(h, capsys):
    check = lambda *args, **kwargs: None
    h.xq.doctor_repository(h.repo, check)
    assert 'ledger: .agents/ledger.jsonl (default)' in capsys.readouterr().out
    h.configure(ledger='history/xq.jsonl')
    h.xq.doctor_repository(h.repo, check)
    out = capsys.readouterr().out
    assert 'ledger: history/xq.jsonl (config "ledger")' in out


@pytest.mark.parametrize('value', ['/abs/ledger.jsonl', '../outside.jsonl', 'a/../../b.jsonl', '~/l.jsonl',
                                   'history/ledger.txt', '.jsonl', '', 7, None, '.git/l.jsonl', '.GIT/l.jsonl',
                                   './.git/l.jsonl', 'sub/.git/l.jsonl'])
def test_invalid_ledger_config_names_valid_shapes(h, value):
    h.configure(ledger=value)
    with pytest.raises(SystemExit, match='ledger: repo-relative \\*.jsonl path'):
        h.cli('status')


def test_symlinked_ledger_directory_is_refused(h, tmp_path):
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    os.symlink(str(elsewhere), str(h.repo / 'history'))
    h.configure(ledger='history/xq.jsonl')
    with pytest.raises(SystemExit, match='unsafe symlink'):
        h.cli('init')
    assert not (elsewhere / 'xq.jsonl').exists()


def test_compose_refuses_the_custom_ledger_as_out(h):
    h.configure(ledger='.agents/xq/history.jsonl')
    assert 'xq state' in h.xq.compose_out_refusal(h.repo, h.repo / '.agents/xq/history.jsonl')
    assert 'xq state' in h.xq.compose_out_refusal(h.repo, h.repo / '.agents/xq/history.lock')


def test_import_maps_reports_malformed_and_is_idempotent(h, capsys):
    path = h.source()
    assert h.cli('import', '--from', path, '--map', json.dumps(MAP)) == 1
    captured = capsys.readouterr()
    assert 'line 3: not valid UTF-8 JSON; skipped' in captured.err
    assert 'imported 4 line(s)' in captured.out and '1 malformed' in captured.out
    tasks = h.xq.state(h.repo)
    assert tasks['old-1']['status'] == 'done' and tasks['old-1']['brief'] == 'briefs/a.md'
    assert tasks['old-1']['imported']['line'] == 2 and len(tasks['old-1']['imported']['sha256']) == 64
    assert tasks['old-2']['status'] == 'running' and tasks['old-3']['closed'] == '2026-01-04T09:00:00Z'
    assert all('executor' not in task for task in tasks.values())
    before = h.xq.events(h.repo)
    assert [e['ts'] for e in before][:2] == ['2026-01-02T10:00:00Z', '2026-01-02T11:00:00Z']
    assert h.cli('import', '--from', path, '--map', json.dumps(MAP)) == 1
    assert 'imported 0 line(s)' in capsys.readouterr().out
    assert h.xq.events(h.repo) == before


def test_import_dry_run_writes_nothing(h, capsys):
    assert h.cli('import', '--from', h.source(malformed=False), '--map', json.dumps(MAP), '--dry-run') == 0
    assert 'would import 4 line(s)' in capsys.readouterr().out
    assert h.xq.events(h.repo) == []


def test_import_uses_config_map_and_validates_it(h, capsys):
    path = h.source(malformed=False)
    with pytest.raises(SystemExit, match='import needs --map JSON or "import_map"'):
        h.cli('import', '--from', path)
    with pytest.raises(SystemExit, match='invalid --map; expected'):
        h.cli('import', '--from', path, '--map', '{"executor": "agent"}')
    h.configure(import_map=dict(MAP, status_values={'x': 'merged'}))
    with pytest.raises(SystemExit, match='import_map'):
        h.cli('status')
    h.configure(import_map=MAP)
    assert h.cli('import', '--from', path) == 0
    assert set(h.xq.state(h.repo)) == {'old-1', 'old-2', 'old-3'}


def test_unmapped_status_and_bad_id_are_malformed(h, capsys):
    rows = [{'task_id': 'a1', 'state': 'weird'}, {'task_id': '../x', 'state': 'complete'}, [1],
            {'task_id': 'a2', 'state': 'complete', 'timestamp': 'yesterday'}, {'task_id': 'a3'}]
    assert h.cli('import', '--from', h.source(rows, malformed=False), '--map', json.dumps(MAP)) == 1
    err = capsys.readouterr().err
    assert 'line 1: status weird is not done|failed|running|queued|closed' in err
    assert 'line 2: missing or invalid task id' in err and 'line 3: not a JSON object' in err
    assert 'line 4: invalid zoned ISO timestamp' in err
    assert set(h.xq.state(h.repo)) == {'a3'} and 'status' not in h.xq.state(h.repo)['a3']


def test_import_never_rewrites_an_xq_task(h, capsys):
    h.xq.append(h.repo, dict(event='assign', id='old-1', executor='agy', brief='b', queued=True))
    h.xq.append(h.repo, dict(event='assign', id='OLD-2', executor='agy', brief='b', queued=True))
    assert h.cli('import', '--from', h.source(malformed=False), '--map', json.dumps(MAP)) == 1
    err = capsys.readouterr().err
    assert 'task id old-1 collides with an xq task old-1' in err and 'collides with an xq task OLD-2' in err
    tasks = h.xq.state(h.repo)
    assert tasks['old-1']['executor'] == 'agy' and not tasks['old-1'].get('imported')
    # Replay ignores a forged import line for a task xq recorded itself.
    rows = h.xq.events(h.repo) + [dict(event='import', id='old-1', status='done', imported={'line': 1})]
    assert h.xq.replay_events(rows)['old-1']['status'] == 'queued'


def test_imported_tasks_are_read_only_history(h, capsys, monkeypatch):
    h.cli('import', '--from', h.source(malformed=False), '--map', json.dumps(MAP))
    capsys.readouterr()
    assert h.cli('status', '--json') == 0
    records = {r['id']: r for r in json.loads(capsys.readouterr().out)['tasks']}
    assert records['old-2']['imported'] is True and records['old-2']['occupies_slot'] is False
    assert records['old-2']['executor'] is None and records['old-2']['source_executor'] == 'codex'
    assert records['old-2']['stale'] is False
    assert h.cli('status') == 0
    assert 'IMPORTED' in capsys.readouterr().out
    assert h.cli('status', '--open') == 0
    assert capsys.readouterr().out.strip() == 'no tasks'
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{}'))
    assert h.cli('hook') == 0
    for args in (('verify', '--id', 'old-1', '--by', 'r', '--verdict', 'pass', '--evidence', 'e'),
                 ('merge', '--id', 'old-1', '--commit', 'HEAD'), ('close', '--id', 'old-1', '--reason', 'dropped'),
                 ('finish', '--id', 'old-2', '--status', 'done', '--force')):
        with pytest.raises(SystemExit, match='imported history'):
            h.cli(*args)
    with pytest.raises(SystemExit, match='imported history'):
        h.cli('assign', '--id', 'old-1', '--executor', 'agy', '--brief', h.brief(), '--queued')
    with pytest.raises(SystemExit, match='--follows old-1: imported history'):
        h.cli('assign', '--id', 'new-1', '--executor', 'agy', '--brief', h.brief(), '--queued', '--follows', 'old-1')


def test_custom_ledger_lock_is_private_state_in_the_primary_checkout(h, capsys):
    h.configure(ledger='history/xq.jsonl')
    assert h.cli('init') == 0
    assert h.cli('assign', '--id', 'n1', '--executor', 'agy', '--brief', h.brief(), '--queued') == 0
    capsys.readouterr()
    private = h.xq.private_paths(h.repo)
    assert h.xq.private_state_path('history/xq.lock', private)
    assert not h.xq.private_state_path('.agents/ledger.lock', private)
    assert (h.repo / 'history/xq.lock').exists()
    task = dict(h.xq.state(h.repo)['n1'], worktree=str(h.repo))
    hits = h.xq.worktree_sensitive(h.repo, task, h.xq.worktree_scan(h.repo, task), None)
    assert not any(hit.startswith('history/xq.lock') for hit in hits)


def test_poll_refuses_imported_tasks(h, capsys):
    h.cli('import', '--from', h.source(malformed=False), '--map', json.dumps(MAP))
    capsys.readouterr()
    assert h.xq.state(h.repo)['old-2']['status'] == 'running'
    for args in (('poll', '--id', 'old-2'), ('poll', '--id', 'old-2', '--wait', '--timeout', '6', '--interval', '5')):
        with pytest.raises(SystemExit, match='old-2 is imported history \\(read-only\\): poll'):
            h.cli(*args)


def test_import_tolerates_a_utf8_bom(h, capsys):
    path = Path(h.source(SAMPLE[:2], malformed=False))
    raw = b'\xef\xbb\xbf' + path.read_bytes()
    path.write_bytes(raw)
    assert h.cli('import', '--from', str(path), '--map', json.dumps(MAP)) == 0
    assert h.xq.state(h.repo)['old-1']['status'] == 'done'
    first = h.xq.events(h.repo)[0]['imported']['sha256']
    assert first == __import__('hashlib').sha256(raw.split(b'\n')[0]).hexdigest()
