"""Offline v2 contracts; git commits/pushes are fixture-only, to a local bare remote.
Fixture approach adapted from the existing xq-tests v1 suite; tests target tracked source.
"""
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'skill/executor-queue/scripts/xq.py'


@pytest.fixture
def q(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith('GIT_') or key.startswith('XQ_'):
            monkeypatch.delenv(key)
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    monkeypatch.setenv('GIT_ALLOW_PROTOCOL', 'file')
    monkeypatch.setenv('GIT_TERMINAL_PROMPT', '0')
    repo = tmp_path / 'repo'
    repo.mkdir()
    monkeypatch.chdir(repo)
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{}'))
    spec = importlib.util.spec_from_file_location('xq_under_test', SCRIPT)
    xq = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(xq)

    def git(*args):
        return subprocess.run(['git', '-c', 'commit.gpgsign=false', *args],
                              capture_output=True, text=True, check=True, timeout=15).stdout.strip()
    git('init', '-b', 'main')
    git('config', 'user.email', 'fixture@example.invalid')
    git('config', 'user.name', 'Fixture')
    git('config', 'core.hooksPath', os.devnull)
    brief = '.agents/briefs/batch.md'
    (repo / brief).parent.mkdir(parents=True)
    (repo / brief).write_text('## a\nFirst task.\n## b\nSecond task.\n## local\nLocal task.\n', encoding='utf-8')
    (repo / '.gitignore').write_text('.agents/ledger*\n.agy-bridge/\n', encoding='utf-8')
    git('add', '.')
    git('commit', '-m', 'fixture base')
    remote = tmp_path / 'remote.git'
    git('init', '--bare', str(remote))
    git('remote', 'add', 'origin', str(remote))
    git('push', 'origin', 'main')
    git('remote', 'set-head', 'origin', 'main')
    base = git('rev-parse', 'HEAD')

    def cli(*args):
        monkeypatch.setattr(sys, 'argv', ['xq', *args])
        return xq.main()

    def assign(tid='local', executor='codex', *extra):
        return cli('assign', '--id', tid, '--executor', executor, '--brief', brief, *extra)

    def envelope(*ids, **kwargs):
        return cli('cloud-envelope', '--brief', brief, '--tasks', *(ids or ['a']),
                   *sum(([k] if v is True else [k, v] for k, v in kwargs.items()), []))

    def report(tid='a', **kwargs):
        d = dict(task=tid, status='done', summary='Delivered', tests=[{'cmd': 'pytest', 'result': 'pass'}],
                 head=base, base=base, utc='2026-11-08T21:00:00Z', branch='claude/test')
        d.update(kwargs)
        return d

    def publish(files, extra=None, implementations=True):
        git('checkout', '-B', 'claude/test', base)
        for name, text in (extra or {}).items():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(text, encoding='utf-8')
        if implementations:
            for tid, data in files.items():
                if isinstance(data, dict) and data.get('head') == base:
                    marker = 'impl-' + tid.replace(' ', '-')
                    (repo / marker).write_text(tid, encoding='utf-8')
                    git('add', marker, *(extra or {}))
                    git('commit', '-m', 'implement ' + tid)
                    data['head'] = git('rev-parse', 'HEAD')
        for name, value in files.items():
            path = repo / '.agents/cloud-status' / (name + '.json')
            path.parent.mkdir(exist_ok=True)
            path.write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf-8')
        for name, text in (extra or {}).items():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(text, encoding='utf-8')
        git('add', '.agents/cloud-status', *(extra or {}))
        git('commit', '-m', 'fixture statuses')
        git('push', '--force', 'origin', 'claude/test')
        git('checkout', 'main')

    def collect(*args):
        return cli('cloud-collect', '--branch', 'claude/test', *args)

    def fake(name, body):
        folder = tmp_path / 'bin'
        folder.mkdir(exist_ok=True)
        path = folder / name
        path.write_text('#!' + sys.executable + '\n' + body, encoding='utf-8')
        path.chmod(0o755)
        monkeypatch.setenv('PATH', str(folder) + os.pathsep + os.environ['PATH'])
        return path

    return SimpleNamespace(**locals())


def finish(q, tid='local', status='done'):
    return q.cli('finish', '--id', tid, '--status', status)


def verify(q, tid='local', verdict='pass'):
    return q.cli('verify', '--id', tid, '--by', 'reader', '--verdict', verdict, '--evidence', 'checked')


def test_hook_honours_stop_active(q, capsys):
    q.assign()
    finish(q)
    q.monkeypatch.setattr(sys, 'stdin', io.StringIO('{"stop_hook_active":true}'))
    assert q.cli('hook') == 0
    assert 'not verified' in capsys.readouterr().err


@pytest.mark.parametrize('executor', ['codex', 'agy'])
def test_hook_no_invented_work_and_queued_pause(q, executor, capsys):
    q.assign(executor=executor)
    finish(q)
    verify(q)
    assert q.cli('hook') == 0
    q.assign('next', executor, '--queued')
    if executor == 'agy':
        (q.repo / '.agents/config.json').write_text('{"keep_busy":["agy"]}')
    assert q.cli('hook') == 2
    assert f'{executor} idle; next queued item next' in capsys.readouterr().err
    q.cli('pause', '--executor', executor)
    assert q.cli('hook') == 0
    q.cli('pause', '--executor', executor, '--resume')
    assert q.cli('hook') == 2


def test_keep_busy_and_stale_cloud(q, capsys):
    (q.repo / '.agents/config.json').write_text('{"keep_busy":["codex"]}')
    assert q.cli('hook') == 2
    assert 'codex idle' in capsys.readouterr().err
    q.xq.append(q.repo, dict(event='assign', id='old', executor='cloud', ts='2000-01-01T00:00:00Z'))
    assert q.cli('hook', '--stale-hours', '2') == 2
    assert 'stale running: old' in capsys.readouterr().err


def test_reassign_resets_attempt(q):
    q.assign()
    finish(q)
    verify(q)
    q.cli('merge', '--id', 'local', '--commit', q.base)
    q.assign()
    t = q.xq.state(q.repo)['local']
    assert t['attempt'] == 2
    assert not any(t.get(k) for k in ('verdict', 'by', 'merged', 'finished'))


@pytest.mark.parametrize('command', [
    ['finish', '--status', 'done'], ['verify', '--by', 'reader', '--verdict', 'pass', '--evidence', 'checked'],
    ['merge', '--commit', 'HEAD'],
])
def test_unknown_and_order_checks(q, command):
    with pytest.raises(SystemExit, match='unknown'):
        q.cli(command[0], '--id', 'missing', *command[1:])
    q.assign()
    if command[0] != 'finish':
        with pytest.raises(SystemExit, match='order|finished|pass'):
            q.cli(command[0], '--id', 'local', *command[1:])
    else:
        finish(q)
        with pytest.raises(SystemExit, match='order|running'):
            finish(q)


def test_merge_validates_commit_even_with_force(q):
    q.assign()
    finish(q)
    verify(q)
    with pytest.raises(SystemExit, match='commit|git'):
        q.cli('merge', '--id', 'local', '--commit', 'f' * 40, '--force')


@pytest.mark.parametrize('tid', ['../evil', '-option', 'a/b', 'a' * 121, 'x y'])
def test_bad_ids(q, tid):
    with pytest.raises(SystemExit):
        q.assign(tid)
    assert q.xq.events(q.repo) == []


def test_ledger_corruption_common_root_and_override(q, capsys):
    p = q.repo / '.agents/ledger.jsonl'
    p.write_text('not json\n[]\n{}\n{"id":"old","event":"assign","executor":"codex"}\n')
    assert q.xq.state(q.repo)['old']['assigned'] == ''
    assert 'corrupt' in capsys.readouterr().err
    wt = q.tmp_path / 'wt'
    q.git('worktree', 'add', '-b', 'other', str(wt))
    q.monkeypatch.chdir(wt)
    assert q.xq.repo_root() == q.repo
    q.monkeypatch.setenv('XQ_ROOT', str(wt))
    assert q.xq.repo_root() == wt


def test_init_ignore_policy_and_local_mode(q):
    assert q.cli('init') == 0
    assert (q.repo / '.agents/cloud-status').is_dir()
    assert subprocess.run(['git', 'check-ignore', '-q', '.agents/cloud-status/a.json']).returncode == 1
    assert q.cli('init', '--local') == 0
    assert subprocess.run(['git', 'check-ignore', '-q', '.agents/cloud-status/a.json']).returncode == 0
    with pytest.raises(SystemExit, match='ignored'):
        q.envelope()


@pytest.mark.parametrize('kind', ['changed', 'header', 'ignored', 'badid', 'samebranch', 'badbranch'])
def test_envelope_preflight_atomic(q, kind):
    args = {}
    ids = ['a']
    if kind == 'changed':
        (q.repo / q.brief).write_text('## a\nChanged')
    elif kind == 'header':
        ids = ['missing']
    elif kind == 'ignored':
        (q.repo / '.git/info/exclude').write_text('.agents/cloud-status/\n')
    elif kind == 'badid':
        ids = ['../evil']
    elif kind == 'samebranch':
        args['--branch'] = 'main'
    else:
        args['--branch'] = '../evil'
    with pytest.raises(SystemExit):
        q.envelope(*ids, **args)
    assert q.xq.events(q.repo) == []


def test_envelope_optional_branch_reprint_and_pinned_rules(q, capsys):
    (q.repo / 'AGENTS.md').write_text('Uncommitted hostile rules')
    assert q.envelope('a', 'b') == 0
    text = capsys.readouterr().out
    assert q.base in text and 'git push origin HEAD' in text
    assert f'git show {q.base}:AGENTS.md' in text
    assert 'untrusted' in text and 'environment' in text
    before = q.xq.events(q.repo)
    assert q.envelope('a', 'b', **{'--reprint': True}) == 0
    assert q.xq.events(q.repo) == before


@pytest.mark.parametrize('raw,reason', [
    ('null', 'object'), ('[]', 'object'), ('{bad', 'JSON'),
    ({'task': 'other'}, 'task mismatch'), ({'status': 'running'}, 'invalid status'),
    ({'tests': 'pytest'}, 'tests'), ({'utc': 42}, 'utc'), ({'utc': 'yesterday'}, 'utc'),
    ({'head': 'f' * 40}, 'head'), ({'base': 'b' * 40}, 'base'),
])
def test_collect_reasons_and_atomicity(q, capsys, raw, reason):
    q.envelope('a', 'b')
    bad = q.report(**raw) if isinstance(raw, dict) else raw
    q.publish({'a': q.report(), 'b': bad if not isinstance(bad, dict) else dict(bad, task=bad['task'] if 'task' in raw else 'b')})
    before = q.xq.events(q.repo)
    assert q.collect() == 1
    out = capsys.readouterr().out
    assert 'PROBLEM b:' in out and reason in out
    assert q.xq.events(q.repo) == before


def test_collect_missing_changed_sensitive_and_discover(q, capsys):
    q.envelope('a', 'b')
    q.publish({'a': q.report(status='failed')}, {'AGENTS.md': 'changed rules'})
    assert q.collect() == 1
    out = capsys.readouterr().out
    assert 'MISSING b' in out and 'SENSITIVE AGENTS.md' in out
    assert q.xq.state(q.repo)['a']['status'] == 'running'
    # Manual failure cancels b, so it is no longer missing.
    finish(q, 'b', 'failed')
    assert q.collect() == 0
    q.publish({'a': q.report(), 'b': q.report('b', status='failed')})
    assert q.cli('cloud-collect', '--discover') == 1
    assert 'CHANGED a: failed -> done' in capsys.readouterr().out
    assert q.xq.state(q.repo)['a']['status'] == 'failed'


def test_collect_head_must_be_on_branch_and_after_base(q, capsys):
    q.envelope()
    q.git('checkout', '-b', 'unrelated')
    (q.repo / 'only-other').write_text('x')
    q.git('add', 'only-other')
    q.git('commit', '-m', 'unrelated')
    head = q.git('rev-parse', 'HEAD')
    q.git('checkout', 'main')
    q.publish({'a': q.report(head=head)})
    assert q.collect() == 1
    assert 'head' in capsys.readouterr().out


def test_collect_batch_filter_idempotence_and_short_base(q):
    q.envelope('a')
    q.xq.append(q.repo, dict(event='assign', id='b', executor='cloud', base=q.base, batch='other'))
    q.publish({'a': q.report(base=q.base[:7]), 'b': q.report('b')})
    assert q.collect('--batch', 'batch') == 0
    assert q.xq.state(q.repo)['b']['status'] == 'running'
    before = q.xq.events(q.repo)
    assert q.collect('--batch', 'batch') == 0
    assert q.xq.events(q.repo) == before


def install_companion(q, version, body):
    p = q.home / '.claude/plugins/cache/openai-codex/codex' / version / 'scripts/codex-companion.mjs'
    p.parent.mkdir(parents=True)
    p.write_text(body)
    return p


def test_doctor_needs_plugin_resolution_and_auth(q, capsys):
    q.cli('init')
    old = install_companion(q, '1.0.9', '')
    new = install_companion(q, '1.0.10', '')
    q.fake('codex', 'print("codex 1")')
    q.fake('node', 'import json\nprint(json.dumps({"auth":{"loggedIn": True}}))')
    assert q.cli('doctor', '--need', 'codex') == 0
    out = capsys.readouterr().out
    assert str(new) in out and 'CC=' in out and 'SKIP' in out
    plugins = q.home / '.claude/plugins/installed_plugins.json'
    plugins.write_text(json.dumps({'plugins': {'codex@openai-codex': [{'installPath': str(old.parents[1])}]}}))
    assert q.cli('doctor', '--need', 'codex') == 0
    assert str(old) in capsys.readouterr().out
    q.fake('node', 'print(\'{"auth":{"loggedIn":false}}\')')
    assert q.cli('doctor', '--need', 'codex') == 1
    assert 'codex login' in capsys.readouterr().out


def test_doctor_agyb_cache_capabilities_and_cloud_requirements(q, capsys):
    q.cli('init')
    q.fake('agy', 'import sys\nprint("models")\nsys.exit(11 if "--bad" in sys.argv else 0)')
    p = q.home / '.claude/plugins/cache/local/agy/0.1.1/bin/agy-bridge'
    p.parent.mkdir(parents=True)
    p.write_text('#!' + sys.executable + '\nimport sys\nsys.exit(0 if sys.argv[1] == "queue" else 1)')
    p.chmod(0o755)
    assert q.cli('doctor', '--need', 'agy') == 1
    out = capsys.readouterr().out
    assert str(p) in out and '/plugin update agy@agy-bridge-local' in out
    p.write_text('#!' + sys.executable + '\nimport sys\nsys.exit(0)')
    assert q.cli('doctor', '--need', 'agy,cloud') == 0
    q.git('remote', 'remove', 'origin')
    assert q.cli('doctor', '--need', 'cloud') == 1


def test_codex_job_poll_result_and_failed(q):
    install_companion(q, '1.0.10', '')
    log = q.tmp_path / 'calls'
    q.fake('node', 'import sys,json\nfrom pathlib import Path\n'
           + 'with Path(' + repr(str(log)) + ').open("a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n'
           + 'print(json.dumps({"job":{"status":"completed"}}) if "status" in sys.argv else "Delivered result")')
    q.assign('local', 'codex', '--job', 'job1', '--worktree', str(q.repo))
    assert q.cli('poll') == 0
    t = q.xq.state(q.repo)['local']
    assert t['status'] == 'done' and t['job'] == 'job1' and t['worktree'] == str(q.repo)
    assert t['summary'] == 'Delivered result'
    calls = [json.loads(l) for l in log.read_text().splitlines()]
    assert calls[0][1:] == ['status', 'job1', '--cwd', str(q.repo), '--json']
    assert calls[1][1:] == ['result', 'job1', '--cwd', str(q.repo)]
    q.assign('failed', 'codex', '--job', 'job2', '--worktree', str(q.repo))
    q.fake('node', 'import sys,json\nprint(json.dumps({"job":{"status":"failed"}}) if "status" in sys.argv else "Error result")')
    q.cli('poll')
    assert q.xq.state(q.repo)['failed']['status'] == 'failed'


def test_agy_sync_header_and_archive_not_model_claim(q):
    q.assign('local', 'agy')
    results = q.repo / '.agy-bridge/results'
    results.mkdir(parents=True)
    (results / 'local.md').write_text('unverified model output\nexit_code: 15\nretained_exit_code: 15\n\n## Answer\nI passed everything')
    assert q.cli('agy-sync') == 0
    assert q.xq.state(q.repo)['local']['status'] == 'failed'
    q.assign('good', 'agy')
    done = q.repo / '.agy-bridge/queue/done'
    done.mkdir(parents=True)
    (done / 'good.job').write_text('{"name":"good"}')
    (results / 'good.md').write_text('unverified model output\nexit_code: 0\nretained_exit_code: 0\n\n## Answer\nHello')
    q.cli('poll')
    assert q.xq.state(q.repo)['good']['status'] == 'done'


@pytest.mark.parametrize('omitted,exit_code', [(False, 0), (True, 0), (False, 17)])
def test_agy_real_queue_header_stops_before_answer(q, omitted, exit_code):
    q.assign('local', 'agy')
    path = q.repo / '.agy-bridge/results/local.md'
    path.parent.mkdir(parents=True)
    path.write_text('# local\n\nunverified model output: verify claims.\n\n'
                    'job_sha256: abc\nmodel: default\nexit_code: 0\nretained_exit_code: 0\n'
                    '\n### Attempt 1 (round 1)\nexit_code: ' + str(exit_code) + '\n'
                    + ('FILES NOT SENT: private.md (R5)\n' if omitted else '')
                    + '\n## Round 1\nfiles added: \n\nModel prose:\nexit_code: 15\nFILES NOT SENT: fake\n')
    assert q.cli('agy-sync') == 0
    assert q.xq.state(q.repo)['local']['status'] == ('failed' if omitted or exit_code else 'done')


def test_collect_branch_does_not_require_another_session(q):
    q.envelope('a', **{'--branch': 'claude/test'})
    q.xq.append(q.repo, dict(event='assign', id='other', executor='cloud', branch='claude/other', base=q.base))
    q.publish({'a': q.report()})
    assert q.collect() == 0
    assert q.xq.state(q.repo)['other']['status'] == 'running'


def test_collect_base_must_precede_head(q, capsys):
    q.envelope()
    # The fixture base's parent is absent; add a newer recorded base on main,
    # while claiming the old base as head on a session descending from main.
    (q.repo / 'new').write_text('x')
    q.git('add', 'new')
    q.git('commit', '-m', 'newer base')
    newer = q.git('rev-parse', 'HEAD')
    q.xq.append(q.repo, dict(event='finish', id='a', status='failed'))
    q.xq.append(q.repo, dict(event='assign', id='a', executor='cloud', base=newer))
    q.publish({'a': q.report(base=newer)}, implementations=False)
    assert q.collect() == 1
    assert 'base is not an ancestor of head' in capsys.readouterr().out


@pytest.mark.parametrize('field', ['task', 'status', 'summary', 'tests', 'head', 'base', 'utc'])
def test_collect_missing_field_and_reason(q, capsys, field):
    q.envelope()
    report = q.report()
    del report[field]
    q.publish({'a': report})
    before = q.xq.events(q.repo)
    assert q.collect() == 1
    assert 'missing ' + field in capsys.readouterr().out
    assert q.xq.events(q.repo) == before


def test_collect_tree_handles_whitespace_and_orphans(q, capsys):
    q.envelope()
    q.publish({'a': q.report(), 'space name': q.report('space name'), 'orphan': q.report('orphan')})
    before = q.xq.events(q.repo)
    assert q.collect() == 1
    out = capsys.readouterr().out
    assert 'invalid status path' in out and 'no assign' in out
    assert q.xq.events(q.repo) == before


def test_collected_report_content_changes_even_if_status_same(q, capsys):
    q.envelope()
    q.publish({'a': q.report()})
    assert q.collect() == 0
    q.publish({'a': q.report(summary='Different summary')})
    before = q.xq.events(q.repo)
    assert q.collect() == 1
    assert 'CHANGED a: done -> done' in capsys.readouterr().out
    assert q.xq.events(q.repo) == before


def test_force_evidence_limit_and_partial_line_recovery(q):
    q.assign()
    q.cli('verify', '--id', 'local', '--by', 'reader', '--verdict', 'pass', '--evidence', 'x' * 9000, '--force')
    assert len(q.xq.events(q.repo)[-1]['evidence']) == 8000
    path = q.repo / '.agents/ledger.jsonl'
    with path.open('a') as f:
        f.write('{partial')
    finish(q)
    assert q.xq.events(q.repo)[-1]['status'] == 'done'


def test_duplicate_assignment_transaction_lock(q):
    # Two real processes race against the same ledger. Exactly one may assign.
    args = [sys.executable, str(SCRIPT), 'assign', '--id', 'race', '--executor', 'codex', '--brief', q.brief]
    first = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    second = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    first.communicate(timeout=15)
    second.communicate(timeout=15)
    assert sorted([first.returncode, second.returncode]) == [0, 1]
    assert len([e for e in q.xq.events(q.repo) if e['id'] == 'race']) == 1


def test_poll_failure_is_not_a_finish_and_stale_status(q, capsys):
    install_companion(q, '1.0.10', '')
    q.fake('node', 'print("not JSON")')
    q.assign('local', 'codex', '--job', 'job1', '--worktree', str(q.repo))
    before = q.xq.events(q.repo)
    assert q.cli('poll') == 1
    assert q.xq.events(q.repo) == before
    assert 'reconcile' in capsys.readouterr().out
    q.xq.append(q.repo, dict(event='assign', id='old', executor='cloud', ts='2000-01-01T00:00:00Z'))
    assert q.cli('status', '--open') == 0
    assert 'STALE' in capsys.readouterr().out


def test_init_does_not_truncate_ledger(q):
    q.assign()
    before = q.xq.events(q.repo)
    q.cli('init')
    q.cli('init')
    assert q.xq.events(q.repo) == before
    assert (q.repo / '.git/info/exclude').read_text().count('/.agents/ledger.jsonl') == 1


def test_envelope_duplicate_atomic_and_reprint_without_fetch(q, monkeypatch):
    q.envelope('a')
    before = q.xq.events(q.repo)
    with pytest.raises(SystemExit, match='distinct'):
        q.envelope('b', 'b')
    with pytest.raises(SystemExit, match='already'):
        q.envelope('b', 'a')
    assert q.xq.events(q.repo) == before
    q.git('remote', 'remove', 'origin')
    monkeypatch.setattr(q.xq, 'fetch_branch', lambda *args: pytest.fail('reprint must not fetch'))
    assert q.envelope('a', **{'--reprint': True}) == 0


def test_python38_syntax_and_stdlib():
    import ast
    tree = ast.parse(SCRIPT.read_text(encoding='utf-8'), feature_version=(3, 8))
    allowed = {'argparse', 'contextlib', 'datetime', 'glob', 'json', 'os', 'pathlib', 're',
               'shlex', 'shutil', 'subprocess', 'sys', 'fcntl', 'msvcrt', 'time'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(n.name.split('.')[0] in allowed for n in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.module.split('.')[0] in allowed


def test_agy_quota_retry_success_does_not_fail(q):
    q.assign('local', 'agy')
    p = q.repo / '.agy-bridge/results/local.md'
    p.parent.mkdir(parents=True)
    p.write_text('# local\n\nunverified model output\n\nexit_code: 0\nretained_exit_code: 0\n'
                 '\n### Attempt 1 (round 1)\nexit_code: 10\n'
                 '\n### Attempt 2 (round 1)\nexit_code: 0\n'
                 '\n## Round 1\nAnswer text')
    assert q.cli('agy-sync') == 0
    assert q.xq.state(q.repo)['local']['status'] == 'done'


@pytest.mark.parametrize('folder,suffix', [('results', '.md'), ('queue/done', '.job'), ('queue/failed', '.job'), ('queue', '.job')])
def test_agy_reused_artifacts_refused_at_assign(q, folder, suffix):
    q.assign('local', 'agy')
    finish(q)
    p = q.repo / '.agy-bridge' / folder / ('local' + suffix)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('old output')
    before = q.xq.events(q.repo)
    with pytest.raises(SystemExit, match='unique|existing|reuse'):
        q.assign('local', 'agy')
    assert q.xq.events(q.repo) == before


def test_envelope_unpushed_brief_has_clear_reason(q):
    path = '.agents/briefs/new.md'
    (q.repo / path).write_text('## a\nTask')
    with pytest.raises(SystemExit, match='not pushed'):
        q.cli('cloud-envelope', '--brief', path, '--tasks', 'a')
    assert q.xq.events(q.repo) == []


def test_cloud_envelope_refuses_reused_failed_id(q):
    q.envelope('a')
    q.publish({'a': q.report(status='failed')})
    assert q.collect() == 0
    before = q.xq.events(q.repo)
    with pytest.raises(SystemExit, match='new id'):
        q.envelope('a')
    assert q.xq.events(q.repo) == before


@pytest.mark.parametrize('other_batch', [None, 'outstanding'])
def test_review_01_collect_ignores_unbound_other_batch(q, capsys, other_batch):
    q.envelope('a')  # The actual session branch is not known until collection.
    q.xq.append(q.repo, dict(event='assign', id='other', executor='cloud',
                             base=q.base, batch=other_batch))
    q.publish({'a': q.report()})
    capsys.readouterr()
    assert q.collect() == 0
    output = capsys.readouterr().out
    assert 'MISSING' not in output
    assert 'cloud progress: done 1\n' in output
    tasks = q.xq.state(q.repo)
    assert tasks['a']['status'] == 'done'
    assert tasks['other']['status'] == 'running'


def test_review_02_reprint_plain_cloud_assignment_has_clean_error(q):
    q.assign('a', 'cloud')
    before = q.xq.events(q.repo)
    with pytest.raises(SystemExit, match='envelope|pinned base'):
        q.envelope('a', **{'--reprint': True})
    assert q.xq.events(q.repo) == before


@pytest.mark.parametrize('event', ['verify', 'merge'])
def test_review_03_envelope_refuses_id_with_only_forced_event(q, event):
    if event == 'verify':
        q.cli('verify', '--id', 'a', '--by', 'reader', '--verdict', 'pass',
              '--evidence', 'reconciled', '--force')
    else:
        q.cli('merge', '--id', 'a', '--commit', q.base, '--force')
    before = q.xq.events(q.repo)
    with pytest.raises(SystemExit, match='already.*new id'):
        q.envelope('b', 'a')
    assert q.xq.events(q.repo) == before


@pytest.mark.parametrize('status', ['done', 'failed', 'blocked'])
def test_review_04_unverified_attempt_requires_force_and_preserves_history(q, capsys, status):
    q.assign()
    finish(q, status=status)
    before = q.xq.events(q.repo)
    with pytest.raises(SystemExit, match='unverified|not verified'):
        q.assign()
    assert q.xq.events(q.repo) == before
    assert q.cli('hook') == 2
    assert 'finished but not verified: local' in capsys.readouterr().err
    assert q.assign('local', 'codex', '--force') == 0
    assert q.xq.events(q.repo)[:len(before)] == before
    assert q.xq.state(q.repo)['local']['attempt'] == 2
    assert q.xq.state(q.repo)['local']['status'] == 'running'


@pytest.mark.parametrize('command', ['agy-sync', 'poll'])
def test_review_05_sync_queued_agy_results(q, command):
    q.assign('local', 'agy', '--queued')
    q.assign('waiting', 'agy', '--queued')
    result = q.repo / '.agy-bridge/results/local.md'
    result.parent.mkdir(parents=True)
    result.write_text('exit_code: 0\nretained_exit_code: 0\n\n## Answer\nDelivered')
    assert q.cli(command) == 0
    tasks = q.xq.state(q.repo)
    assert tasks['local']['status'] == 'done'
    assert not tasks['local'].get('verdict')
    assert tasks['waiting']['status'] == 'queued'
    before = q.xq.events(q.repo)
    assert q.cli(command) == 0
    assert q.xq.events(q.repo) == before


def test_review_06_doctor_reports_missing_default_branch(q, capsys):
    q.cli('init')
    q.git('symbolic-ref', '--delete', 'refs/remotes/origin/HEAD')
    q.git('checkout', '--detach')
    assert q.cli('doctor', '--need', 'cloud') == 1
    output = capsys.readouterr().out
    assert 'MISSING default branch:' in output
    assert 'OK origin:' in output  # Diagnostics continue after the missing branch.


@pytest.mark.parametrize('need,probe', [
    ('cloud', '.agents/briefs/probe.md'), ('agy', '.agy-bridge/probe'),
])
def test_review_06_doctor_checks_ignores_from_root(q, capsys, monkeypatch, need, probe):
    q.cli('init')
    monkeypatch.setattr(q.xq, 'plugin_binary', lambda kind: '/fake/agy-bridge')
    monkeypatch.setattr(q.xq.shutil, 'which', lambda name: '/fake/' + name)
    original = q.xq.run

    def fake_tools(argv, **kwargs):
        if argv[0] in ('agy', '/fake/agy', '/fake/agy-bridge') or '/fake/agy-bridge' in argv:
            return subprocess.CompletedProcess(argv, 0, '', '')
        return original(argv, **kwargs)

    monkeypatch.setattr(q.xq, 'run', fake_tools)
    if need == 'cloud':
        with (q.repo / '.git/info/exclude').open('a') as fh:
            fh.write('/.agents/briefs/\n')
    else:
        (q.repo / '.gitignore').write_text('/.agy-bridge/\n')
    nested = q.repo / 'nested'
    nested.mkdir()
    monkeypatch.chdir(nested)
    assert q.cli('doctor', '--need', need) == (1 if need == 'cloud' else 0)
    output = capsys.readouterr().out
    assert ('MISSING ' + probe if need == 'cloud' else 'OK .agy-bridge ignored:') in output


def test_review_07_xq_root_controls_git_from_unrelated_cwd(q, monkeypatch, capsys):
    outside = q.tmp_path / 'outside'
    outside.mkdir()
    monkeypatch.setenv('XQ_ROOT', str(q.repo))
    monkeypatch.chdir(outside)
    calls = []
    original = q.xq.run

    def record_git(argv, **kwargs):
        if argv[0] == 'git':
            calls.append((argv, kwargs.get('cwd')))
        return original(argv, **kwargs)

    monkeypatch.setattr(q.xq, 'run', record_git)
    assert q.cli('init') == 0
    assert q.cli('doctor', '--need', 'cloud') == 0
    assert 'default branch: main' in capsys.readouterr().out
    assert q.envelope('a', **{'--branch': 'claude/test'}) == 0
    # Fixture publishing uses its own git helper in the temporary repo.
    monkeypatch.chdir(q.repo)
    q.publish({'a': q.report()}, {'AGENTS.md': 'untrusted change'})
    monkeypatch.chdir(outside)
    assert q.cli('cloud-collect', '--discover') == 0
    assert 'SENSITIVE AGENTS.md' in capsys.readouterr().out
    assert q.xq.state(q.repo)['a']['status'] == 'done'
    verify(q, 'a')
    monkeypatch.chdir(q.repo)
    merged = q.git('merge', '--no-ff', 'claude/test', '-m', 'integrate a')
    assert q.cli('merge', '--id', 'a', '--commit', 'HEAD') == 0
    assert calls
    assert all(cwd == q.repo for argv, cwd in calls), calls


def test_review_07_linked_worktree_uses_primary_checkout_git(q, monkeypatch):
    wt = q.tmp_path / 'linked'
    q.git('worktree', 'add', '--detach', str(wt))
    q.git('symbolic-ref', '--delete', 'refs/remotes/origin/HEAD')
    monkeypatch.chdir(wt)
    # Fallback HEAD must be the primary checkout's branch, not detached wt HEAD.
    assert q.envelope() == 0
    assert q.xq.state(q.repo)['a']['main'] == 'main'


@pytest.mark.parametrize('kind', ['file', 'parent'])
def test_review_08_envelope_refuses_symlinked_brief(q, kind):
    if kind == 'file':
        link = q.repo / '.agents/briefs/link.md'
        link.symlink_to(q.repo / q.brief)
    else:
        directory = q.repo / 'linked-briefs'
        directory.symlink_to(q.repo / '.agents/briefs', target_is_directory=True)
        link = directory / 'batch.md'
    before = q.xq.events(q.repo)
    with pytest.raises(SystemExit, match='unsafe|symlink'):
        q.cli('cloud-envelope', '--brief', str(link.relative_to(q.repo)), '--tasks', 'a')
    assert q.xq.events(q.repo) == before


@pytest.mark.parametrize('bad_report', [False, True])
def test_review_09_collect_parses_ledger_once(q, monkeypatch, capsys, bad_report):
    q.envelope('a', 'b')
    q.publish({'a': q.report(), 'b': q.report('b', **({'utc': 'invalid'} if bad_report else {}))})
    original = q.xq.events
    reads = []

    def counted_events(root):
        reads.append(root)
        return original(root)

    monkeypatch.setattr(q.xq, 'events', counted_events)
    capsys.readouterr()
    assert q.collect() == int(bad_report)
    assert reads == [q.repo]
    assert ('cloud progress: running 2' if bad_report else 'cloud progress: done 2') in capsys.readouterr().out


def test_review_09_collect_caches_sensitive_diff_per_base_ref(q, monkeypatch, capsys):
    q.envelope('a', 'b')
    # A distinct pinned base belongs on its own session branch. Every done task
    # now needs its own implementation head, so assert once per base/head pair.
    (q.repo / 'new-base').write_text('x')
    q.git('add', 'new-base')
    q.git('commit', '-m', 'second base fixture')
    newer = q.git('rev-parse', 'HEAD')
    q.xq.append(q.repo, dict(event='assign', id='c', executor='cloud', base=newer, batch='batch'))
    reports = {'a': q.report(), 'b': q.report('b')}
    q.publish(reports, {'AGENTS.md': 'untrusted', '.claude/settings.json': '{}', q.brief: 'changed brief'})
    third = council_publish(q, {'c': {'base': newer}}, branch='claude/second', start=newer)
    first_ref = q.git('rev-parse', 'claude/test')
    third_ref = q.git('rev-parse', 'claude/second')
    original = q.xq.git
    diffs = []

    def count_diffs(*args, **kwargs):
        if args[:2] == ('diff', '--name-status'):
            diffs.append(args[4:6])
        return original(*args, **kwargs)

    monkeypatch.setattr(q.xq, 'git', count_diffs)
    capsys.readouterr()
    assert q.cli('cloud-collect', '--discover') == 0
    assert sorted(diffs) == sorted([(q.base, reports['a']['head']),
                                   (reports['a']['head'], first_ref),
                                   (q.base, reports['b']['head']),
                                   (reports['b']['head'], first_ref),
                                   (newer, third['c']['head']), (third['c']['head'], third_ref)])
    output = capsys.readouterr().out
    assert output.count('SENSITIVE AGENTS.md ') == 1
    assert output.count('SENSITIVE .claude/settings.json ') == 1
    assert output.count('SENSITIVE ' + q.brief + ' ') == 1


@pytest.mark.parametrize('rows', [[], iter(())])
def test_review_10_empty_append_does_no_io(q, monkeypatch, rows):
    def unexpected(*args, **kwargs):
        pytest.fail('empty append must not lock, open/read/write, or fsync')

    monkeypatch.setattr(q.xq, 'ledger_lock', unexpected)
    monkeypatch.setattr(Path, 'open', unexpected)
    monkeypatch.setattr(q.xq.os, 'fsync', unexpected)
    q.xq.append_many(q.repo, rows)


@pytest.mark.parametrize('ending', [b'', b'\n'])
def test_review_10_append_reads_only_last_byte_and_recovers_partial_line(q, monkeypatch, ending):
    path = q.xq.ledger_path(q.repo)
    previous = ('{"id":"old","summary":"' + 'ż' * 65536 + '"}').encode('utf-8') + ending
    path.write_bytes(previous)
    original_open = Path.open
    original_safe_open = q.xq.safe_open
    reads, seeks = [], []

    class TrackedFile:
        def __init__(self, fh):
            self.fh = fh

        def __enter__(self):
            self.fh.__enter__()
            return self

        def __exit__(self, *args):
            return self.fh.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.fh, name)

        def read(self, size=-1):
            reads.append(size)
            return self.fh.read(size)

        def seek(self, offset, whence=0):
            seeks.append((offset, whence))
            return self.fh.seek(offset, whence)

    def tracked_open(p, *args, **kwargs):
        fh = original_safe_open(p, *args, **kwargs)
        return TrackedFile(fh) if p == path else fh

    monkeypatch.setattr(q.xq, 'safe_open', tracked_open)
    q.xq.append_many(q.repo, [dict(event='finish', id='new', summary='zażółć')])
    assert reads == [1]
    assert (-1, os.SEEK_END) in seeks
    with original_open(path, 'rb') as fh:
        contents = fh.read()
    assert contents.startswith(previous + (b'' if ending else b'\n'))
    assert json.loads(contents.splitlines()[-1])['summary'] == 'zażółć'


@pytest.mark.parametrize('verdict', ['fixes', 'reject'])
def test_review_small_01_open_status_keeps_unresolved_verdicts(q, capsys, verdict):
    q.assign()
    finish(q)
    verify(q, verdict=verdict)
    assert q.cli('status', '--open') == 0
    output = capsys.readouterr().out
    assert 'local' in output and verdict in output


@pytest.mark.parametrize('utc', [
    '2026-11-08T21:00:00Z', '2026-11-08T21:00:00.123456789+00:00',
    '20261108T210000Z', '20261108T210000.123Z',
    '2026-11-08T21:00:00,123Z', '2026-W45-7T21:00:00Z',
])
def test_review_small_02_iso_timestamps_on_python38(q, monkeypatch, utc):
    real_datetime = q.xq.datetime.datetime

    class Python38Datetime(real_datetime):
        @classmethod
        def fromisoformat(cls, value):
            # Older CPython only accepted extended calendar dates and dot fractions.
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{3}|\.\d{6})?\+00:00', value):
                raise ValueError('unsupported by Python 3.8 fromisoformat')
            return super().fromisoformat(value)

    monkeypatch.setattr(q.xq.datetime, 'datetime', Python38Datetime)
    report = q.report(utc=utc)
    q.publish({'a': report})
    ref = q.git('rev-parse', 'claude/test')
    assert q.xq.validate_report(report, 'a', {'base': q.base}, ref, q.repo, 'claude/test') == []
    # A future assignment must not be marked stale because parsing failed.
    future = utc.replace('2026', '2099')
    assert q.xq.stale({'status': 'running', 'assigned': future}, 4) is False


@pytest.mark.parametrize('retained', [10, 15, 16, 17])
def test_review_small_03_agy_retained_exit_is_diagnostic(q, retained):
    q.assign('local', 'agy')
    result = q.repo / '.agy-bridge/results/local.md'
    result.parent.mkdir(parents=True)
    result.write_text('exit_code: 0\nretained_exit_code: ' + str(retained) +
                      '\n\n## Answer\nDelivered')
    assert q.cli('agy-sync') == 0
    assert q.xq.state(q.repo)['local']['status'] == 'done'
    assert not q.xq.state(q.repo)['local'].get('verdict')


def test_review_small_03_agy_retained_exit_alone_is_not_delivery(q, capsys):
    q.assign('local', 'agy')
    result = q.repo / '.agy-bridge/results/local.md'
    result.parent.mkdir(parents=True)
    result.write_text('retained_exit_code: 0\n\n## Answer\nClaims success')
    assert q.cli('agy-sync') == 1
    assert 'missing bridge exit header' in capsys.readouterr().out
    assert q.xq.state(q.repo)['local']['status'] == 'running'


def test_review_small_04_duplicate_merge_requires_force(q):
    q.assign()
    finish(q)
    verify(q)
    assert q.cli('merge', '--id', 'local', '--commit', q.base) == 0
    before = q.xq.events(q.repo)
    with pytest.raises(SystemExit, match='already merged'):
        q.cli('merge', '--id', 'local', '--commit', q.base)
    assert q.xq.events(q.repo) == before
    assert q.cli('merge', '--id', 'local', '--commit', q.base, '--force') == 0
    assert q.xq.events(q.repo)[:len(before)] == before
    assert len(q.xq.events(q.repo)) == len(before) + 1


@pytest.mark.parametrize('source', ['skill/executor-queue/SKILL.md', 'docs/codex.md'])
def test_review_small_05_root_snippet_resolves_primary_checkout(q, source):
    wt = q.tmp_path / 'linked worktree'
    q.git('worktree', 'add', '--detach', str(wt))
    nested = wt / 'nested'
    nested.mkdir()
    text = (SCRIPT.parents[3] / source).read_text()
    lines = [line.strip().strip('`') for line in text.splitlines()
             if line.strip().startswith(('root=', '`root='))]
    assert len(lines) == 1, 'provide an executable primary-checkout root assignment'
    result = subprocess.run(['sh', '-c', lines[0] + '\nprintf "%s\\n" "$root"'],
                            cwd=str(nested), capture_output=True, text=True, check=True, timeout=10)
    assert Path(result.stdout.strip()) == q.repo
    assert (Path(result.stdout.strip()) / q.brief).is_file()

# Council numbers follow the Confirmed findings table (24 actual rows).
def council_publish(q, reports, branch='claude/test', start=None, extra=None):
    q.git('checkout', '-B', branch, start or q.base)
    data = {}
    for tid, changes in reports.items():
        (q.repo / ('impl-' + tid)).write_text(tid)
        q.git('add', 'impl-' + tid)
        q.git('commit', '-m', 'implement ' + tid)
        data[tid] = q.report(tid, head=q.git('rev-parse', 'HEAD'), branch=branch, **changes)
    for name, text in (extra or {}).items():
        path = q.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    for tid, report in data.items():
        path = q.repo / '.agents/cloud-status' / (tid + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report))
    q.git('add', '.')
    q.git('commit', '-m', 'reports')
    q.git('push', '--force', 'origin', branch)
    q.git('checkout', 'main')
    return data


def test_council_01_delivery_invalidates_verification(q):
    q.assign()
    finish(q)
    verify(q)
    q.cli('merge', '--id', 'local', '--commit', q.base)
    q.cli('finish', '--id', 'local', '--status', 'done', '--force')
    task = q.xq.state(q.repo)['local']
    assert not any(task.get(k) for k in ('verdict', 'by', 'evidence', 'merged', 'commit', 'verified_head'))
    assert q.cli('hook') == 2
    with pytest.raises(SystemExit, match='pass'):
        q.cli('merge', '--id', 'local', '--commit', q.base)
    q.assign('early')
    q.cli('verify', '--id', 'early', '--by', 'reader', '--verdict', 'pass', '--evidence', 'early', '--force')
    assert not q.xq.state(q.repo)['early'].get('verdict')
    finish(q, 'early')
    assert not q.xq.state(q.repo)['early'].get('verdict')


def test_council_02_reader_and_commit_binding(q, capsys):
    q.envelope()
    reports = council_publish(q, {'a': {}})
    assert q.collect() == 0
    for reader in ('cloud', 'codex', 'agy'):
        with pytest.raises(SystemExit, match='reader|executor'):
            q.cli('verify', '--id', 'a', '--by', reader, '--verdict', 'pass', '--evidence', 'checked')
    verify(q, 'a')
    assert q.xq.events(q.repo)[-1]['verified_head'] == reports['a']['head']
    with pytest.raises(SystemExit, match='head'):
        q.cli('merge', '--id', 'a', '--commit', q.base)
    with pytest.raises(SystemExit, match='default branch'):
        q.cli('merge', '--id', 'a', '--commit', reports['a']['head'])
    q.git('merge', '--no-ff', 'claude/test', '-m', 'integrate a')
    q.cli('merge', '--id', 'a', '--commit', 'HEAD')
    assert 'WARN' in capsys.readouterr().out
    q.cli('verify', '--id', 'a', '--by', 'cloud', '--verdict', 'pass', '--evidence', 'override', '--force')
    assert q.xq.events(q.repo)[-1]['force'] is True


def test_council_03_discover_skips_foreign(q, capsys):
    q.envelope()
    council_publish(q, {'orphan': {}}, branch='claude/foreign')
    council_publish(q, {'a': {}})
    q.git('push', 'origin', q.base + ':refs/heads/feature/claude/x')
    assert q.cli('cloud-collect', '--discover', '--batch', 'batch') == 0
    assert 'SKIP foreign orphan' in capsys.readouterr().out
    assert q.xq.state(q.repo)['a']['status'] == 'done'
    assert q.cli('cloud-collect', '--branch', 'claude/foreign') == 1


def test_council_04_batch_isolation_and_cancel(q):
    q.envelope()
    q.xq.append(q.repo, dict(event='assign', id='b', executor='cloud', base=q.base, batch='other'))
    council_publish(q, {'a': {'summary': 'first'}}, branch='claude/one')
    council_publish(q, {'a': {'summary': 'second'}}, branch='claude/two')
    council_publish(q, {'b': {}}, branch='claude/good')
    assert q.cli('cloud-collect', '--discover') == 1
    assert q.xq.state(q.repo)['b']['status'] == 'done'
    assert q.xq.state(q.repo)['a']['status'] == 'running'
    finish(q, 'a', 'failed')
    assert q.cli('cloud-collect', '--discover') == 0


def test_council_05_bound_branch_diagnostic(q, capsys):
    q.envelope(**{'--branch': 'claude/wanted'})
    council_publish(q, {'a': {}})
    assert q.collect() == 1
    assert 'report on claude/test but bound to claude/wanted' in capsys.readouterr().out
    with pytest.raises(SystemExit, match='claude/'):
        q.cli('cloud-collect', '--branch', 'feature/outside')
    with pytest.raises(SystemExit, match='claude/'):
        q.envelope('b', **{'--branch': 'feature/outside'})


def test_council_06_sensitive_paths_and_rename(q, capsys):
    (q.repo / 'CLAUDE.md').write_text('rules')
    q.git('add', 'CLAUDE.md'); q.git('commit', '-m', 'rules'); q.git('push', 'origin', 'main')
    q.envelope()
    base = q.git('rev-parse', 'HEAD')
    q.git('checkout', '-B', 'claude/test', base)
    q.git('mv', 'CLAUDE.md', 'notes.md')
    paths = ['src/CLAUDE.md', 'src/AGENTS.md', '.mcp.json', '.github/workflows/x.yml',
             '.husky/pre-commit', 'Makefile', 'package-lock.json', '.agents/config.json',
             '.agents/briefs/other.md', '.agents/cloud-status/other.json']
    for name in paths:
        p = q.repo / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('{}')
    (q.repo / 'link').symlink_to('notes.md')
    q.git('add', '.'); q.git('commit', '-m', 'a authority changes')
    head = q.git('rev-parse', 'HEAD')
    (q.repo / '.agents/cloud-status/a.json').write_text(json.dumps(q.report(head=head, base=base)))
    q.git('add', '.'); q.git('commit', '-m', 'report'); q.git('push', 'origin', 'claude/test'); q.git('checkout', 'main')
    q.collect()
    output = capsys.readouterr().out
    for path in ['CLAUDE.md', 'link'] + paths:
        assert 'SENSITIVE ' + path in output
    assert 'WARN' in output


def test_council_07_review_includes_new_files(q):
    q.cli('init')
    assert (q.repo / '.agents/review').is_dir()
    skill = SCRIPT.parents[1] / 'SKILL.md'
    text = skill.read_text()
    assert 'add -N .' in text and 'status --porcelain' in text and 'mkdir -p' in text
    (q.repo / 'brand-new.py').write_text('new code')
    q.git('add', '-N', '.')
    diff = q.git('diff', q.base)
    assert 'brand-new.py' in diff


def test_council_09_heartbeat_ack_and_pause(q, monkeypatch):
    q.xq.append(q.repo, dict(event='assign', id='old', executor='cloud', ts='2000-01-01T00:00:00Z'))
    assert q.cli('hook') == 2
    q.cli('ack', '--id', 'old', '--hours', '12')
    assert q.cli('hook') == 0
    q.xq.append(q.repo, dict(event='assign', id='slow', executor='codex', job='job', worktree=str(q.repo), ts='2000-01-01T00:00:00Z'))
    monkeypatch.setattr(q.xq, 'plugin_binary', lambda kind: 'companion')
    original_run = q.xq.run
    monkeypatch.setattr(q.xq, 'run', lambda argv, **kw: subprocess.CompletedProcess(argv, 0, '{"job":{"status":"running"}}', '') if argv[0] == 'node' else original_run(argv, **kw))
    q.cli('poll')
    assert q.xq.state(q.repo)['slow']['seen']
    assert q.cli('hook') == 0
    q.xq.append(q.repo, dict(event='assign', id='paused', executor='cloud', ts='2000-01-01T00:00:00Z'))
    q.cli('pause', '--executor', 'cloud')
    assert q.cli('hook') == 0


def test_council_10_close_and_open_integration(q, capsys):
    q.assign(); finish(q); verify(q, verdict='fixes')
    q.assign('local-fix1'); finish(q, 'local-fix1'); verify(q, 'local-fix1')
    q.cli('close', '--id', 'local', '--reason', 'superseded-by:local-fix1')
    q.assign('drop', 'codex', '--queued')
    q.cli('close', '--id', 'drop', '--reason', 'dropped')
    capsys.readouterr(); q.cli('status', '--open')
    assert [line.split()[0] for line in capsys.readouterr().out.splitlines()] == ['local-fix1']
    q.cli('merge', '--id', 'local-fix1', '--commit', q.base)
    capsys.readouterr(); q.cli('status', '--open')
    assert capsys.readouterr().out == 'no tasks\n'
    q.assign('unreviewed'); finish(q, 'unreviewed')
    with pytest.raises(SystemExit, match='verif'):
        q.cli('close', '--id', 'unreviewed', '--reason', 'rejected')


def test_council_11_unicode_line_separators(q):
    q.assign()
    text = 'one\u2028two\u2029three\x85four'
    q.cli('finish', '--id', 'local', '--status', 'done', '--summary', text)
    assert q.xq.state(q.repo)['local']['summary'] == text


def test_council_12_network_git_timeout_and_prompt(q, monkeypatch):
    calls = []
    def fake(argv, **kw):
        calls.append((argv, kw))
        return subprocess.CompletedProcess(argv, 0, '', '')
    monkeypatch.setattr(q.xq.subprocess, 'run', fake)
    q.xq.git('fetch', 'origin', 'main', cwd=q.repo)
    q.xq.git('ls-remote', '--heads', 'origin', cwd=q.repo)
    assert all(kw['timeout'] is None and kw['env']['GIT_TERMINAL_PROMPT'] == '0' for argv, kw in calls)


def test_council_13_windows_lock_and_unlocked_queries(q, monkeypatch):
    attempts = []
    def locking(fd, mode, size):
        attempts.append(mode)
        if mode == 2 and attempts.count(2) < 3:
            raise OSError('busy')
    monkeypatch.setitem(sys.modules, 'msvcrt', SimpleNamespace(locking=locking, LK_NBLCK=2, LK_UNLCK=0))
    with monkeypatch.context() as patch:
        patch.setattr(q.xq, 'WINDOWS', True, raising=False)
        patch.setattr(q.xq, 'LOCK_WAIT_SECONDS', 0.1, raising=False)
        with q.xq.ledger_lock(q.repo):
            assert str(q.repo) in q.xq._LOCKED
    assert attempts == [2, 2, 2, 0]
    attempts.clear()
    with monkeypatch.context() as patch:
        patch.setattr(q.xq, 'WINDOWS', True)
        patch.setattr(q.xq, 'LOCK_WAIT_SECONDS', 0)
        with pytest.raises(SystemExit, match='ledger busy'):
            with q.xq.ledger_lock(q.repo):
                pass
    q.assign('local', 'codex', '--job', 'j', '--worktree', str(q.repo))
    monkeypatch.setattr(q.xq, 'plugin_binary', lambda kind: 'cc')
    original = q.xq.run
    def unlocked(argv, **kw):
        if argv[0] == 'node':
            assert not q.xq._LOCKED
            return subprocess.CompletedProcess(argv, 0, '{"job":{"status":"running"}}', '')
        if argv[:2] == ['git', 'fetch']:
            assert not q.xq._LOCKED
        return original(argv, **kw)
    monkeypatch.setattr(q.xq, 'run', unlocked)
    q.cli('poll'); q.envelope('a')
    council_publish(q, {'a': {}})
    q.collect()


def test_council_14_cp1252_envelope(q):
    r = subprocess.run([sys.executable, str(SCRIPT), 'cloud-envelope', '--brief', q.brief, '--tasks', 'a', 'b'],
                       env=dict(os.environ, PYTHONIOENCODING='cp1252'), capture_output=True)
    assert r.returncode == 0, r.stderr
    assert b'BATCH' in r.stdout
    r = subprocess.run([sys.executable, str(SCRIPT), 'cloud-envelope', '--brief', q.brief, '--tasks', 'a', 'b', '--reprint'],
                       env=dict(os.environ, PYTHONIOENCODING='cp1252'), capture_output=True)
    assert r.returncode == 0, r.stderr


def test_council_15_agy_python_and_powershell(q, monkeypatch, capsys):
    q.cli("init")
    bridge = q.tmp_path / "a bridge's script"
    bridge.write_text('import sys\nsys.exit(0)')
    monkeypatch.setattr(q.xq, 'plugin_binary', lambda kind: str(bridge))
    agy = q.fake('agy.cmd', 'print("models")')
    monkeypatch.setattr(q.xq.shutil, 'which', lambda name: str(agy) if name == 'agy' else None)
    assert q.cli('doctor', '--need', 'agy') == 0
    assert str(bridge) in capsys.readouterr().out
    monkeypatch.setattr(q.xq, 'WINDOWS', True)
    assert q.xq.bridge_command(str(bridge)) == [sys.executable, str(bridge)]
    assert q.xq.shell_assignment('AGYB', str(bridge)).startswith("$AGYB='")
    assert "bridge''s" in q.xq.shell_assignment('AGYB', str(bridge))


def test_council_17_init_first_local_hint(q, capsys):
    skill = (SCRIPT.parents[1] / 'SKILL.md').read_text()
    assert skill.index('xq.py init') < skill.index('xq.py doctor')
    q.cli('init', '--local')
    output = capsys.readouterr().out
    assert 'local-only' in output and 'must be committed' not in output


def test_council_18_automatic_private_exclusions(q):
    (q.repo / '.gitignore').write_text('')
    q.assign()
    for name in ['ledger.jsonl', 'ledger.lock', 'review/a.diff', 'config.tmp', 'config.json.tmp']:
        assert subprocess.run(['git', 'check-ignore', '-q', '.agents/' + name]).returncode == 0
    assert subprocess.run(['git', 'check-ignore', '-q', '.agents/config.json']).returncode == 1


@pytest.mark.parametrize('name', ['ledger.jsonl', 'ledger.lock', 'config.json', 'config.tmp', 'config.json.tmp'])
def test_council_19_state_symlinks_refused(q, name):
    victim = q.tmp_path / 'victim'; victim.write_text('{}')
    path = q.repo / '.agents' / name
    if path.exists(): path.unlink()
    path.symlink_to(victim)
    with pytest.raises(SystemExit, match='symlink|unsafe'):
        if name.startswith('config'):
            q.cli('pause', '--executor', 'codex')
        else:
            q.assign()
    assert victim.read_text() == '{}'


def test_council_20_report_branch_matches(q, capsys):
    q.envelope()
    council_publish(q, {'a': {}})
    q.git('push', 'origin', 'claude/test:claude/imposter')
    assert q.cli('cloud-collect', '--branch', 'claude/imposter') == 1
    assert 'branch mismatch' in capsys.readouterr().out


def test_council_21_pinned_fork_and_task_diff(q, capsys):
    q.envelope()
    (q.repo / 'CLAUDE.md').write_text('lead changes')
    q.git('add', '.'); q.git('commit', '-m', 'lead changes')
    council_publish(q, {'a': {}}, start='main')
    assert q.collect() == 1
    output = capsys.readouterr().out
    assert 'fork point' in output and 'SENSITIVE CLAUDE.md' not in output


def test_council_22_done_requires_distinct_task_commit(q, capsys):
    q.envelope('a', 'b')
    q.publish({'a': q.report(), 'b': q.report('b')}, implementations=False)
    assert q.collect() == 1
    assert 'implementation commit' in capsys.readouterr().out
    council_publish(q, {'a': {}, 'b': {}})
    q.git('checkout', 'claude/test')
    path = q.repo / '.agents/cloud-status/b.json'
    data = json.loads(path.read_text()); data['head'] = q.git('rev-parse', 'HEAD~2')
    path.write_text(json.dumps(data)); q.git('add', '.'); q.git('commit', '-m', 'borrow head')
    q.git('push', 'origin', 'claude/test'); q.git('checkout', 'main')
    assert q.collect() == 1
    assert 'distinct' in capsys.readouterr().out


def test_council_23_casefold_ids_and_exact_results(q):
    q.assign('Review1', 'agy')
    with pytest.raises(SystemExit, match='case|collid'):
        q.assign('review1', 'agy')
    folder = q.repo / '.agy-bridge/results'; folder.mkdir(parents=True)
    (folder / 'review1.md').write_text('exit_code: 0\n## Answer\nresult')
    q.cli('agy-sync')
    assert q.xq.state(q.repo)['Review1']['status'] == 'running'


def test_council_24_queued_agy_promotion_and_idle(q):
    q.assign('review', 'agy', '--queued')
    path = q.repo / '.agy-bridge/queue/review.job'; path.parent.mkdir(parents=True)
    path.write_text('{"name":"review"}')
    assert q.cli('hook') == 0
    q.assign('review', 'agy')
    assert q.xq.state(q.repo)['review']['status'] == 'running'
    q.assign('next', 'agy', '--queued')
    finish(q, 'review'); verify(q, 'review')
    (q.repo / '.agents/config.json').write_text('{"keep_busy":["agy"]}')
    assert q.cli('hook') == 2


def test_council_13_concurrent_finish_during_poll_is_preserved(q, monkeypatch):
    q.assign('local', 'codex', '--job', 'j', '--worktree', str(q.repo))
    monkeypatch.setattr(q.xq, 'plugin_binary', lambda kind: 'cc')
    original = q.xq.run
    def racing(argv, **kw):
        if argv[0] == 'node':
            q.xq.append(q.repo, dict(event='finish', id='local', status='failed', summary='cancelled concurrently'))
            return subprocess.CompletedProcess(argv, 0, '{"job":{"status":"running"}}', '')
        return original(argv, **kw)
    monkeypatch.setattr(q.xq, 'run', racing)
    with pytest.raises(SystemExit, match='ledger changed'):
        q.cli('poll')
    assert q.xq.state(q.repo)['local']['summary'] == 'cancelled concurrently'
    assert q.xq.events(q.repo)[-1]['event'] == 'finish'


def test_council_24_promotion_refuses_mismatched_job(q):
    q.assign('review', 'agy', '--queued')
    path = q.repo / '.agy-bridge/queue/review.job'; path.parent.mkdir(parents=True)
    path.write_text('{"name":"somebody-else"}')
    with pytest.raises(SystemExit, match='matching|unique|existing'):
        q.assign('review', 'agy')


def test_council_04_forced_cancel_clears_imported_branch_conflict(q):
    q.envelope()
    council_publish(q, {'a': {}})
    assert q.collect() == 0
    q.cli('finish', '--id', 'a', '--status', 'failed', '--force')
    council_publish(q, {'a': {}}, branch='claude/stale')
    assert q.cli('cloud-collect', '--discover') == 0


def test_council_02_warns_rejected_sibling_before_verified_head(q, capsys):
    q.envelope('a', 'b')
    council_publish(q, {'a': {}, 'b': {}})
    assert q.collect() == 0
    verify(q, 'a', 'reject'); verify(q, 'b')
    q.git('merge', '--no-ff', 'claude/test', '-m', 'integrate accepted b')
    capsys.readouterr()
    q.cli('merge', '--id', 'b', '--commit', 'HEAD')
    assert 'WARN commit names non-pass task a' in capsys.readouterr().out


def test_council_22_same_commit_prefix_is_not_distinct(q):
    q.envelope('a', 'b')
    q.git('checkout', '-B', 'claude/test', q.base)
    (q.repo / 'both').write_text('implementation')
    q.git('add', 'both'); q.git('commit', '-m', 'implement a b')
    head = q.git('rev-parse', 'HEAD')
    folder = q.repo / '.agents/cloud-status'; folder.mkdir(parents=True, exist_ok=True)
    for tid, sha in [('a', head), ('b', head[:12])]:
        (folder / (tid + '.json')).write_text(json.dumps(q.report(tid, head=sha)))
    q.git('add', '.'); q.git('commit', '-m', 'reports'); q.git('push', 'origin', 'claude/test'); q.git('checkout', 'main')
    assert q.collect() == 1
    assert q.xq.state(q.repo)['a']['status'] == 'running'
    assert q.xq.state(q.repo)['b']['status'] == 'running'


def test_council_19_exclusive_config_temp_preserves_existing_file(q):
    path = q.repo / '.agents/config.tmp'
    path.write_text('retained recovery data')
    with pytest.raises((SystemExit, FileExistsError)):
        q.cli('pause', '--executor', 'codex')
    assert path.read_text() == 'retained recovery data'


def test_council_06_sensitive_status_tail_and_repeat(q, capsys):
    q.envelope()
    reports = council_publish(q, {'a': {}})
    q.git('checkout', 'claude/test')
    (q.repo / '.mcp.json').write_text('{}')
    q.git('add', '.mcp.json'); q.git('commit', '-m', 'authority after reported head')
    q.git('push', 'origin', 'claude/test'); q.git('checkout', 'main')
    capsys.readouterr()
    assert q.collect() == 0
    assert 'SENSITIVE .mcp.json' in capsys.readouterr().out
    assert q.collect() == 0
    assert 'SENSITIVE .mcp.json' in capsys.readouterr().out
    assert q.xq.state(q.repo)['a']['head'] == reports['a']['head']


@pytest.mark.parametrize('folder,suffix', [('queue', '.job'), ('results', '.md')])
def test_council_24_promotion_preserves_artifact_guards(q, folder, suffix):
    q.assign('review', 'agy', '--queued')
    path = q.repo / '.agy-bridge' / folder / ('review' + suffix)
    path.mkdir(parents=True)
    with pytest.raises(SystemExit, match='unique|existing'):
        q.assign('review', 'agy')


def test_council_13_parallel_fetch_does_not_reuse_fetch_head(q, monkeypatch):
    council_publish(q, {'a': {}})
    original = q.xq.git
    def competing_fetch(*args, **kw):
        output = original(*args, **kw)
        if args[0] == 'fetch':
            q.git('fetch', '-q', 'origin', 'claude/test')
        return output
    monkeypatch.setattr(q.xq, 'git', competing_fetch)
    q.envelope()
    assert q.xq.state(q.repo)['a']['base'] == q.base
    assert not q.git('for-each-ref', '--format=%(refname)', 'refs/xq-fetch/')
