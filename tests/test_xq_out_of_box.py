"""Offline tests for out-of-the-box adoption: preflight in the primary checkout, per-task compose context, memory
admission, the Stop hook's idle throttle and extra checks, richer import, status timestamps, events and lookups."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from conftest import load_xq

BRIEF = '''# Task
Result: The app module prints a greeting.

## Acceptance
Files: src/app.py
Checks: python -m pytest -q
Notes: notes/app.md
'''


@pytest.fixture
def o(tmp_path, monkeypatch, isolated_queue_environment):
    repo = tmp_path / 'repo'
    repo.mkdir()
    monkeypatch.chdir(repo)
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{}'))
    xq = load_xq()
    readings = dict(load=0, cpus=2, repo_free_gb=10, temp_free_gb=10)
    monkeypatch.setattr(xq.module_of('resource_readings'), 'resource_readings', lambda root: readings)
    monkeypatch.setattr(xq.module_of('live_process_counts'), 'live_process_counts',
                        lambda samples=None: dict(codex=0, agy=0))
    repo = repo.resolve()

    def git(*args, cwd=None):
        return subprocess.run(['git', '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=' + os.devnull, *args],
                              cwd=str(cwd or repo), capture_output=True, text=True, check=True,
                              timeout=15).stdout.strip()

    git('init', '-q', '-b', 'main')
    git('config', 'user.email', 'fixture@example.invalid')
    git('config', 'user.name', 'Fixture')
    (repo / '.gitignore').write_text('.agents/\n', encoding='utf-8')
    git('add', '.')
    git('commit', '-q', '-m', 'base')
    base = git('rev-parse', 'HEAD')

    def cli(*args):
        monkeypatch.setattr(sys, 'argv', ['xq', *args])
        return xq.main()

    def configure(**data):
        (repo / '.agents').mkdir(exist_ok=True)
        (repo / '.agents/config.json').write_text(json.dumps(data), encoding='utf-8')

    def brief(name='t1'):
        path = repo / '.agents/briefs' / (name + '.md')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(BRIEF, encoding='utf-8')
        return '.agents/briefs/' + name + '.md'

    def worktree(name='wt'):
        path = tmp_path / name
        git('worktree', 'add', '-q', '-b', 'xq/' + name, str(path), 'HEAD')
        return path

    def source(rows):
        path = tmp_path / 'external.jsonl'
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')
        return str(path)

    return SimpleNamespace(**locals())


# 1. preflight --worktree refuses the primary checkout -------------------------------------------------------------

def test_preflight_refuses_the_primary_checkout_like_assign(o, capsys):
    assert o.cli('preflight', '--executor', 'codex', '--worktree', str(o.repo)) == 1
    out = capsys.readouterr().out
    assert 'codex preflight refused: Codex must not run in the primary checkout' in out
    with pytest.raises(SystemExit, match='Codex must not run in the primary checkout'):
        o.cli('assign', '--id', 't1', '--executor', 'codex', '--brief', o.brief(), '--worktree', str(o.repo))
    assert o.cli('preflight', '--executor', 'codex', '--worktree', str(o.worktree())) == 0
    assert 'codex preflight OK' in capsys.readouterr().out


@pytest.mark.skipif(os.name == 'nt', reason='symlinks')
def test_preflight_warns_on_a_symlinked_dependency_tree_but_admits(o, capsys, tmp_path):
    shared = tmp_path / 'shared-venv'
    shared.mkdir()
    wt = o.worktree()
    os.symlink(str(shared), str(wt / '.venv'))
    assert o.cli('preflight', '--executor', 'codex', '--worktree', str(wt)) == 0
    out = capsys.readouterr().out
    assert 'WARN shared dependency .venv is a symlink' in out and 'codex preflight OK' in out


# 2. compose --context ---------------------------------------------------------------------------------------------

def test_compose_appends_per_task_context_after_configured_files(o, capsys):
    (o.repo / 'AGENTS.md').write_text('Rules.\n', encoding='utf-8')
    (o.repo / 'docs').mkdir()
    (o.repo / 'docs/task.md').write_text('Task facts.\n', encoding='utf-8')
    o.configure(context_files=['AGENTS.md'])
    assert o.cli('compose', '--brief', o.brief(), '--context', 'docs/task.md', '--context', 'AGENTS.md') == 0
    text = Path(capsys.readouterr().out.strip()).read_text(encoding='utf-8')
    assert text.index('BEGIN xq context AGENTS.md') < text.index('BEGIN xq context docs/task.md') < \
        text.index('BEGIN xq brief')
    assert text.count('BEGIN xq context AGENTS.md') == 1  # a file already configured is included once


@pytest.mark.skipif(os.name == 'nt', reason='symlinks')
@pytest.mark.parametrize('entry, message', [
    ('MISSING.md', 'context file not found: MISSING.md \\(--context\\)'),
    ('/etc/hosts', 'must be repo-relative'),
    ('../outside.md', 'must be repo-relative'),
    ('link.md', 'unsafe symlink'),
])
def test_compose_context_uses_the_context_files_safety_rules(o, entry, message):
    (o.repo / 'real.md').write_text('x', encoding='utf-8')
    os.symlink(str(o.repo / 'real.md'), str(o.repo / 'link.md'))
    with pytest.raises(SystemExit, match=message):
        o.cli('compose', '--brief', o.brief(), '--context', entry)
    assert not (o.repo / '.agents/review/t1.prompt.md').exists()


# 3. memory admission ----------------------------------------------------------------------------------------------

def test_memory_admission_is_off_by_default(o, monkeypatch, capsys):
    def probe():
        pytest.fail('memory is probed only when a memory key is configured')
    monkeypatch.setattr(o.xq.module_of('memory_readings'), 'memory_readings', probe)
    assert o.cli('preflight', '--executor', 'codex') == 0
    assert 'swap' not in capsys.readouterr().out


def test_memory_admission_refuses_swap_and_low_memory(o, monkeypatch, capsys):
    reading = dict(swap_used_gb=3.0, available_gb=1.5)
    monkeypatch.setattr(o.xq.module_of('memory_readings'), 'memory_readings', lambda: reading)
    o.configure(max_swap_gb=2, min_free_mem_gb=1)
    assert o.cli('preflight', '--executor', 'codex') == 1
    out = capsys.readouterr().out
    assert 'swap used 3.00 GiB (max 2.00)' in out and 'codex preflight refused: swap used' in out
    reading.update(swap_used_gb=2.0)
    assert o.cli('preflight', '--executor', 'codex') == 0  # boundaries inclusive
    assert 'memory available 1.50 GiB (min 1.00)' in capsys.readouterr().out
    reading.update(available_gb=0.5)
    assert o.cli('preflight', '--executor', 'agy') == 1
    assert 'agy preflight refused: memory available 0.50 GiB' in capsys.readouterr().out


def test_memory_admission_skips_unsupported_platforms_and_refuses_failed_probes(o, monkeypatch, capsys):
    o.configure(min_free_mem_gb=1)
    monkeypatch.setattr(o.xq.module_of('memory_readings'), 'memory_readings', lambda: None)
    assert o.cli('preflight', '--executor', 'codex') == 0
    assert 'SKIP memory check' in capsys.readouterr().out

    def broken():
        raise OSError('vm_stat failed')
    monkeypatch.setattr(o.xq.module_of('memory_readings'), 'memory_readings', broken)
    assert o.cli('preflight', '--executor', 'codex') == 1
    assert 'memory readings unavailable: vm_stat failed' in capsys.readouterr().out


@pytest.mark.parametrize('data', [{'max_swap_gb': -1}, {'min_free_mem_gb': '1'}, {'max_swap_gb': True},
                                  {'min_free_mem_gb': float('inf')}])
def test_memory_keys_are_validated(o, data):
    o.configure(**data)
    with pytest.raises(SystemExit, match='max_swap_gb/min_free_mem_gb'):
        o.cli('status')


def test_memory_parsers_read_platform_output(o):
    darwin_swap = 'total = 2048.00M  used = 1536.00M  free = 512.00M  (encrypted)'
    assert o.xq.parse_swapusage(darwin_swap) == pytest.approx(1.5)
    vm_stat = ('Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free:      65536.\n'
               'Pages active:   1.\nPages inactive:   65536.\nPages speculative:   0.\n')
    assert o.xq.parse_vm_stat(vm_stat) == pytest.approx(2.0)
    meminfo = 'MemTotal: 8388608 kB\nMemAvailable: 2097152 kB\nSwapTotal: 1048576 kB\nSwapFree: 524288 kB\n'
    assert o.xq.parse_meminfo(meminfo) == {'available_gb': pytest.approx(2.0), 'swap_used_gb': pytest.approx(0.5)}
    with pytest.raises(ValueError):
        o.xq.parse_vm_stat('garbage')


# 4. hook idle throttle --------------------------------------------------------------------------------------------

def test_hook_idle_throttle_blocks_idle_once_per_interval(o, capsys):
    o.configure(keep_busy=['codex'], idle_block_every='1h')
    assert o.cli('hook') == 2
    assert 'codex idle' in capsys.readouterr().err
    stamp = o.repo / '.agents/hook-idle.stamp'
    assert stamp.is_file()
    assert o.cli('hook') == 0
    assert 'idle reminder throttled' in capsys.readouterr().err
    old = stamp.stat().st_mtime - 7200
    os.utime(str(stamp), (old, old))
    assert o.cli('hook') == 2


def test_hook_idle_throttle_never_hides_unverified_work(o, capsys):
    o.configure(keep_busy=['codex'], idle_block_every='1h')
    assert o.cli('hook') == 2
    o.cli('assign', '--id', 'a1', '--executor', 'agy', '--brief', o.brief())
    o.cli('finish', '--id', 'a1', '--status', 'done')
    capsys.readouterr()
    assert o.cli('hook') == 2
    assert 'finished but not verified: a1' in capsys.readouterr().err


def test_hook_without_throttle_keeps_blocking_and_writes_no_stamp(o):
    o.configure(keep_busy=['codex'])
    assert o.cli('hook') == 2 and o.cli('hook') == 2
    assert not (o.repo / '.agents/hook-idle.stamp').exists()


def test_init_excludes_the_hook_stamp(o):
    assert o.cli('init') == 0
    exclude = (o.repo / '.git/info/exclude').read_text(encoding='utf-8').split('\n')
    assert '/.agents/hook-idle.stamp' in exclude


@pytest.mark.parametrize('value', ['soon', '0', '', 5, '-1h'])
def test_idle_block_every_is_validated(o, value):
    o.configure(idle_block_every=value)
    with pytest.raises(SystemExit, match='idle_block_every: duration'):
        o.cli('status')


# 5. hook extra_checks ---------------------------------------------------------------------------------------------

def script(o, name, body):
    path = o.tmp_path / name
    path.write_text(body, encoding='utf-8')
    return '"{}" "{}"'.format(sys.executable, path)


def test_hook_extra_checks_add_problems_and_warn_on_failure(o, capsys):
    ok = script(o, 'ok.py', 'import os\nprint("lesson pending in " + os.path.basename(os.getcwd()))\nprint()\n')
    bad = script(o, 'bad.py', 'import sys\nprint("ignored")\nsys.exit(3)\n')
    slow = script(o, 'slow.py', 'import time\ntime.sleep(10)\n')
    o.configure(extra_checks=[ok, bad, slow, 'no-such-command-xq'], extra_checks_timeout='1s')
    assert o.cli('hook') == 2
    err = capsys.readouterr().err
    assert 'lesson pending in repo' in err and 'ignored' not in err
    assert err.count('WARN extra check') == 3 and 'exit 3' in err


def test_hook_extra_checks_quiet_when_they_print_nothing(o, capsys):
    o.configure(extra_checks=[script(o, 'quiet.py', 'pass\n')])
    assert o.cli('hook') == 0
    assert capsys.readouterr().err == ''


@pytest.mark.parametrize('data', [{'extra_checks': 'cmd'}, {'extra_checks': ['']}, {'extra_checks': [1]},
                                  {'extra_checks_timeout': 'x'}])
def test_extra_checks_are_validated(o, data):
    o.configure(**data)
    with pytest.raises(SystemExit, match='extra_checks: list of command strings'):
        o.cli('status')


# 6. import of verifier and merge history --------------------------------------------------------------------------

RICH_MAP = {'id': 'task', 'executor': 'agent', 'status': 'state', 'ts': 'at', 'event': 'kind',
            'event_values': {'started': 'assign', 'ended': 'finish', 'checked': 'verify', 'landed': 'merge'},
            'verdict': 'verdict', 'by': 'reader', 'commit': 'sha', 'model': 'model', 'effort': 'effort'}
RICH = [
    {'task': 'h1', 'agent': 'codex', 'kind': 'started', 'state': 'running', 'at': '2026-01-01T10:00:00Z',
     'model': 'gpt-x', 'effort': 'high'},
    {'task': 'h1', 'kind': 'ended', 'state': 'done', 'at': '2026-01-01T11:00:00Z'},
    {'task': 'h1', 'kind': 'checked', 'verdict': 'pass', 'reader': 'lead-tests', 'at': '2026-01-01T12:00:00Z'},
    {'task': 'h1', 'kind': 'landed', 'sha': 'abcdef1234567', 'at': '2026-01-01T13:00:00Z'},
]


def test_import_keeps_verifier_and_merge_history(o, capsys):
    path = o.source(RICH)
    assert o.cli('import', '--from', path, '--map', json.dumps(RICH_MAP)) == 0
    assert o.cli('import', '--from', path, '--map', json.dumps(RICH_MAP)) == 0
    assert 'imported 0 line(s)' in capsys.readouterr().out  # idempotent
    assert o.cli('status', '--json') == 0
    record = json.loads(capsys.readouterr().out)['tasks'][0]
    assert record['imported'] is True and record['state'] == 'done' and record['verdict'] == 'pass'
    assert record['verified_by'] == 'lead-tests' and record['merge_commit'] == 'abcdef1234567'
    assert record['merged'] is True and record['model'] == 'gpt-x' and record['effort'] == 'high'
    assert record['assigned_at'] == '2026-01-01T10:00:00Z' and record['finished_at'] == '2026-01-01T11:00:00Z'
    assert record['verified_at'] == '2026-01-01T12:00:00Z' and record['merged_at'] == '2026-01-01T13:00:00Z'
    assert o.cli('status') == 0
    line = capsys.readouterr().out
    assert 'pass' in line and 'lead-tests' in line and 'abcdef1234567' in line and 'IMPORTED' in line
    assert o.cli('status', '--open') == 0
    assert capsys.readouterr().out.strip() == 'no tasks'


def test_import_rejects_unmapped_event_and_bad_verdict(o, capsys):
    rows = [{'task': 'h2', 'kind': 'teleported'}, {'task': 'h3', 'kind': 'checked', 'verdict': 'great'},
            {'task': 'h4', 'kind': 'close'}]
    assert o.cli('import', '--from', o.source(rows), '--map', json.dumps(RICH_MAP)) == 1
    err = capsys.readouterr().err
    assert 'line 1: event teleported is not assign|finish|verify|merge|close' in err
    assert 'line 2: verdict great is not pass|fixes|reject' in err
    assert o.xq.state(o.repo)['h4']['closed']


def test_import_without_new_keys_is_unchanged(o):
    rows = [{'task': 'p1', 'state': 'done', 'verdict': 'pass', 'reader': 'x'}]
    assert o.cli('import', '--from', o.source(rows), '--map', json.dumps({'id': 'task', 'status': 'state'})) == 0
    task = o.xq.state(o.repo)['p1']
    assert task['status'] == 'done' and 'verdict' not in task and 'by' not in task


# 7. status --json timestamps and events ----------------------------------------------------------------------------

def test_status_json_has_lifecycle_timestamps(o, capsys):
    o.cli('assign', '--id', 'a1', '--executor', 'agy', '--brief', o.brief())
    capsys.readouterr()
    assert o.cli('status', '--json') == 0
    record = json.loads(capsys.readouterr().out)['tasks'][0]
    for key in ('finished_at', 'verified_at', 'verified_by', 'merged_at', 'merge_commit'):
        assert record[key] is None
    assert record['assigned_at'].endswith('Z')
    o.cli('finish', '--id', 'a1', '--status', 'done')
    o.cli('verify', '--id', 'a1', '--by', 'reader', '--verdict', 'pass', '--evidence', 'ran')
    capsys.readouterr()
    assert o.cli('status', '--json') == 0
    record = json.loads(capsys.readouterr().out)['tasks'][0]
    assert record['finished_at'].endswith('Z') and record['verified_at'].endswith('Z')
    assert record['verified_by'] == 'reader'
    o.cli('finish', '--id', 'a1', '--status', 'done', '--force')
    capsys.readouterr()
    assert o.cli('status', '--json') == 0
    record = json.loads(capsys.readouterr().out)['tasks'][0]
    assert record['verified_at'] is None and record['verified_by'] is None  # a new finish clears the verdict


def test_events_json_flattens_batches(o, capsys):
    o.xq.append_many(o.repo, [dict(event='assign', id='b1', executor='agy', queued=True),
                              dict(event='assign', id='b2', executor='agy', queued=True)])
    o.xq.append(o.repo, dict(event='close', id='b1', reason='dropped'))
    assert o.cli('events', '--json') == 0
    data = json.loads(capsys.readouterr().out)
    assert data['schema_version'] == 1
    assert [(e['event'], e['id']) for e in data['events']] == [('assign', 'b1'), ('assign', 'b2'), ('close', 'b1')]
    assert o.cli('events', '--json', '--id', 'b1') == 0
    assert [e['event'] for e in json.loads(capsys.readouterr().out)['events']] == ['assign', 'close']
    with pytest.raises(SystemExit, match='xq: unknown id nope'):
        o.cli('events', '--json', '--id', 'nope')


# 8. status --field state and unknown ids ----------------------------------------------------------------------------

def test_status_field_state_and_unknown_id(o, capsys):
    o.cli('assign', '--id', 'a1', '--executor', 'agy', '--brief', o.brief(), '--queued')
    capsys.readouterr()
    assert o.cli('status', '--id', 'a1', '--field', 'state') == 0
    assert capsys.readouterr().out == 'queued\n'
    for args in (('status', '--id', 'nope'), ('status', '--id', 'nope', '--json'),
                 ('status', '--id', 'nope', '--field', 'state')):
        with pytest.raises(SystemExit) as info:
            o.cli(*args)
        assert str(info.value).startswith('xq: unknown id nope')
    helper = Path(__file__).resolve().parents[1] / '.claude/skills/executor-queue/scripts/xq.py'
    proc = subprocess.run([sys.executable, str(helper), 'status', '--id', 'nope'], capture_output=True, text=True,
                          timeout=30)
    assert proc.returncode == 1 and proc.stderr.strip() == 'xq: unknown id nope'


def test_doctor_names_a_private_path_new_since_the_last_init(o, capsys):
    (o.repo / '.gitignore').write_text('', encoding='utf-8')
    assert o.cli('init') == 0
    exclude = o.repo / '.git/info/exclude'
    lines = exclude.read_text(encoding='utf-8').split('\n')
    exclude.write_text('\n'.join(line for line in lines if line != '/.agents/hook-idle.stamp'), encoding='utf-8')
    capsys.readouterr()
    o.cli('doctor', '--offline', '--need', 'agy')
    out = capsys.readouterr().out
    assert ('MISSING /.agents/hook-idle.stamp: not ignored (new in 3.7.0; run xq init after updating) -> next: run '
            'xq init') in out
    assert 'MISSING /.agents/review/' not in out
    assert o.cli('init') == 0
    capsys.readouterr()
    o.cli('doctor', '--offline', '--need', 'agy')
    assert 'hook-idle.stamp' not in capsys.readouterr().out.replace('OK /.agents/hook-idle.stamp', '')
