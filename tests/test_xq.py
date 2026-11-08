"""Offline v2 contracts; git commits/pushes are fixture-only, to a local bare remote.
Fixture approach adapted from the existing xq-tests v1 suite; tests target tracked source.
"""
import importlib.util
import io
import json
import os
from pathlib import Path
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

    def publish(files, extra=None):
        git('checkout', '-B', 'claude/test', 'main')
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
    q.publish({'a': q.report(base=newer)})
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
               'shlex', 'shutil', 'subprocess', 'sys', 'fcntl', 'msvcrt'}
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
