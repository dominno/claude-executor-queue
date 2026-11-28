"""Offline tests for the declared-scope check (diff within the brief's Files:) and the stale-base admission."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from conftest import load_xq

SCRIPT = Path(__file__).resolve().parents[1] / '.claude/skills/executor-queue/scripts/xq.py'

BRIEF = '''# Scoped task
Result: The app module prints a greeting.

## Acceptance
Files: {files}
Checks: python -m pytest -q
Notes: notes/app.md
'''


@pytest.fixture
def s(tmp_path, monkeypatch, isolated_queue_environment):
    repo = tmp_path / 'repo'
    repo.mkdir()
    monkeypatch.chdir(repo)
    xq = load_xq()
    monkeypatch.setattr(xq.module_of('resource_readings'), 'resource_readings', lambda root: dict(load=0, cpus=2, repo_free_gb=10, temp_free_gb=10))
    monkeypatch.setattr(xq.module_of('live_process_counts'), 'live_process_counts', lambda samples=None: dict(codex=0, agy=0))
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

    def cli(*args):
        monkeypatch.setattr(sys, 'argv', ['xq', *args])
        return xq.main()

    def brief(files='src/app.py', text=None):
        path = repo / '.agents/briefs/t1.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text if text is not None else BRIEF.format(files=files), encoding='utf-8')
        return '.agents/briefs/t1.md'

    def configure(**data):
        (repo / '.agents').mkdir(exist_ok=True)
        (repo / '.agents/config.json').write_text(json.dumps(data), encoding='utf-8')

    def worktree(name='wt', start='HEAD'):
        path = tmp_path / name
        git('worktree', 'add', '-q', '-b', 'xq/' + name, str(path), start)
        return path

    def assign(wt, *extra, files='src/app.py'):
        return cli('assign', '--id', 't1', '--executor', 'codex', '--brief', brief(files), '--worktree', str(wt),
                   *extra)

    def commit_in(wt, files):
        for name, text in files.items():
            (wt / name).parent.mkdir(parents=True, exist_ok=True)
            (wt / name).write_text(text, encoding='utf-8')
        git('add', '-A', cwd=wt)
        git('commit', '-q', '-m', 'delivery', cwd=wt)
        return git('rev-parse', 'HEAD', cwd=wt)

    def verify(*extra):
        return cli('verify', '--id', 't1', '--by', 'reader', '--verdict', 'pass', '--evidence', 'ran', *extra)

    def last():
        return xq.events(repo)[-1]

    return SimpleNamespace(**locals())


@pytest.mark.parametrize('path, pattern, expected', [
    ('src/app.py', 'src/app.py', True),
    ('src/app.py', 'src/*.py', True),
    ('src/sub/app.py', 'src/*.py', False),
    ('src/sub/app.py', 'src/**/*.py', True),
    ('src/app.py', 'src/**/*.py', True),
    ('src/app.py', '**/app.py', True),
    ('docs/a/b.md', 'docs/', True),
    ('docs/a/b.md', 'docs', True),
    ('docs2/a.md', 'docs', False),
    ('src/a.py', 'src/{a,b}.py', True),
    ('src/c.py', 'src/{a,b}.py', False),
    ('src/app.py', './src/app.py', True),
    ('SRC/app.py', 'src/app.py', False),
    ('src/app.pyc', 'src/app.py', False),
    ('src/app.py/x', 'src/*.py', False),
])
def test_scope_match_is_slash_aware(s, path, pattern, expected):
    assert s.xq.scope_match(path, pattern) is expected


def test_out_of_scope_keeps_order(s):
    assert s.xq.out_of_scope(['b.txt', 'src/a.py', 'a.txt'], ['src/']) == ['b.txt', 'a.txt']


def test_finish_reports_committed_and_untracked_paths_outside_declared_files(s, capsys):
    wt = s.worktree()
    assert s.assign(wt) == 0
    s.commit_in(wt, {'src/app.py': 'x', 'other.txt': 'y'})
    (wt / 'stray.log').write_text('z', encoding='utf-8')
    capsys.readouterr()
    assert s.cli('finish', '--id', 't1', '--status', 'done') == 0
    err = capsys.readouterr().err
    assert 'WARN SCOPE t1: other.txt is outside the declared Files' in err
    assert 'WARN SCOPE t1: stray.log is outside the declared Files' in err
    assert 'src/app.py is outside' not in err


def test_verify_warn_mode_records_out_of_scope_and_passes(s, capsys):
    wt = s.worktree()
    s.assign(wt)
    s.commit_in(wt, {'src/app.py': 'x', 'other.txt': 'y'})
    s.cli('finish', '--id', 't1', '--status', 'done')
    capsys.readouterr()
    assert s.verify() == 0
    assert 'WARN SCOPE t1: other.txt is outside the declared Files' in capsys.readouterr().err
    event = s.last()
    assert event['out_of_scope'] == ['other.txt'] and 'scope_accepted' not in event
    assert s.xq.state(s.repo)['t1']['out_of_scope'] == ['other.txt']


def test_verify_enforce_refuses_until_accept_scope_reason(s, capsys):
    s.configure(scope_check='enforce')
    wt = s.worktree()
    s.assign(wt)
    s.commit_in(wt, {'src/app.py': 'x', 'other.txt': 'y'})
    s.cli('finish', '--id', 't1', '--status', 'done')
    with pytest.raises(SystemExit, match=r'outside the brief\'s declared Files: other.txt; .*--accept-scope'):
        s.verify()
    with pytest.raises(SystemExit, match='accept-scope'):
        s.verify('--force')
    assert s.verify('--accept-scope', 'fixture needs other.txt') == 0
    event = s.last()
    assert event['scope_accepted'] == 'fixture needs other.txt' and event['out_of_scope'] == ['other.txt']


def test_verify_enforce_passes_inside_scope_and_notes_unused_accept(s, capsys):
    s.configure(scope_check='enforce')
    wt = s.worktree()
    s.assign(wt, files='src/')
    s.commit_in(wt, {'src/app.py': 'x', 'src/lib/util.py': 'y'})
    s.cli('finish', '--id', 't1', '--status', 'done')
    capsys.readouterr()
    assert s.verify('--accept-scope', 'not needed') == 0
    err = capsys.readouterr().err
    assert 'WARN SCOPE' not in err and '--accept-scope unused' in err
    assert 'out_of_scope' not in s.last() and 'scope_accepted' not in s.last()


def test_scope_check_off_reports_nothing(s, capsys):
    s.configure(scope_check='off')
    wt = s.worktree()
    s.assign(wt)
    s.commit_in(wt, {'other.txt': 'y'})
    s.cli('finish', '--id', 't1', '--status', 'done')
    assert s.verify() == 0
    assert 'SCOPE' not in capsys.readouterr().err
    assert 'out_of_scope' not in s.last()


def test_brief_without_files_warns_and_never_refuses(s, capsys):
    s.configure(scope_check='enforce')
    wt = s.worktree()
    s.cli('assign', '--id', 't1', '--executor', 'codex', '--worktree', str(wt),
          '--brief', s.brief(text='# Task\nResult: something.\n'))
    s.commit_in(wt, {'other.txt': 'y'})
    capsys.readouterr()
    s.cli('finish', '--id', 't1', '--status', 'done')
    message = "WARN SCOPE t1: no declared Files in the brief's ## Acceptance; scope not checked"
    assert message in capsys.readouterr().err
    assert s.verify() == 0
    assert message in capsys.readouterr().err


def test_removed_worktree_still_checks_committed_range(s):
    s.configure(scope_check='enforce')
    wt = s.worktree()
    s.assign(wt)
    head = s.commit_in(wt, {'other.txt': 'y'})
    s.cli('finish', '--id', 't1', '--status', 'done')
    shutil.rmtree(str(wt))
    with pytest.raises(SystemExit, match='outside the brief\'s declared Files: other.txt'):
        s.verify('--head', head)
    assert s.verify('--head', head, '--accept-scope', 'reviewed') == 0


@pytest.mark.parametrize('extra', [('--verdict', 'reject', '--accept-scope', 'x'),
                                   ('--verdict', 'pass', '--accept-scope', ' ')])
def test_accept_scope_needs_pass_and_reason(s, extra):
    wt = s.worktree()
    s.assign(wt)
    s.commit_in(wt, {'src/app.py': 'x'})
    s.cli('finish', '--id', 't1', '--status', 'done')
    with pytest.raises(SystemExit, match='applies only to a pass verdict and needs a nonempty reason'):
        s.cli('verify', '--id', 't1', '--by', 'reader', '--evidence', 'ran', *extra)


@pytest.mark.parametrize('data', [dict(scope_check='strict'), dict(stale_base='enforce'), dict(scope_check=True)])
def test_invalid_check_modes_name_valid_shapes(s, data):
    s.configure(**data)
    with pytest.raises(SystemExit, match=r'scope_check: off\|warn\|enforce; stale_base: off\|warn\|refuse'):
        s.xq.config(s.repo)


def advance_main(s):
    (s.repo / 'main.txt').write_text('later', encoding='utf-8')
    s.git('add', 'main.txt')
    s.git('commit', '-q', '-m', 'main moves on')


def test_stale_base_warns_with_exact_fix_by_default(s, capsys):
    wt = s.worktree()
    advance_main(s)
    (s.repo / 'second.txt').write_text('x', encoding='utf-8')
    s.git('add', 'second.txt')
    s.git('commit', '-q', '-m', 'second')
    assert s.assign(wt) == 0
    err = capsys.readouterr().err
    assert 'WARN stale base: worktree {0} HEAD '.format(wt) in err
    assert 'is 2 commit(s) behind main; update it first: git -C {0} rebase main (or git -C {0} merge main)'.format(
        wt) in err
    assert s.last()['event'] == 'assign'


def test_stale_base_refuse_blocks_unless_force(s, capsys):
    s.configure(stale_base='refuse')
    wt = s.worktree()
    advance_main(s)
    with pytest.raises(SystemExit, match=r'stale base: .* 1 commit\(s\) behind main; .*rebase main'):
        s.assign(wt)
    assert s.xq.events(s.repo) == []
    assert s.assign(wt, '--force') == 0
    assert 'WARN stale base' in capsys.readouterr().err


def test_stale_base_current_or_off_is_silent(s, capsys):
    wt = s.worktree()
    assert s.assign(wt) == 0
    assert 'stale' not in capsys.readouterr().err
    s.configure(stale_base='off')
    wt2 = s.worktree('wt2')
    advance_main(s)
    assert s.cli('assign', '--id', 't2', '--executor', 'codex', '--brief', s.brief(), '--worktree', str(wt2)) == 0
    captured = capsys.readouterr()
    assert 'stale' not in captured.err + captured.out


def test_stale_base_skipped_when_no_main_resolves(s, capsys):
    s.configure(stale_base='refuse')
    wt = s.worktree()
    advance_main(s)
    s.git('checkout', '-q', '--detach')
    assert s.assign(wt) == 0
    assert 'NOTE stale-base check skipped: no default branch resolves' in capsys.readouterr().out


def test_stale_base_uses_configured_main(s, capsys):
    first = s.git('rev-parse', 'HEAD')
    s.git('branch', 'trunk')
    s.configure(main='trunk')
    advance_main(s)
    assert s.assign(s.worktree('wt', first)) == 0
    assert 'stale' not in capsys.readouterr().err
    s.git('branch', '-f', 'trunk', 'main')
    assert s.cli('assign', '--id', 't2', '--executor', 'codex', '--brief', s.brief(), '--worktree',
                 str(s.worktree('wt2', first))) == 0
    assert '1 commit(s) behind trunk; update it first' in capsys.readouterr().err


def test_preflight_worktree_modes_and_errors(s, capsys):
    wt = s.worktree()
    advance_main(s)
    assert s.cli('preflight', '--executor', 'codex', '--worktree', str(wt)) == 0
    assert 'WARN stale base: worktree' in capsys.readouterr().out
    s.configure(stale_base='refuse')
    assert s.cli('preflight', '--executor', 'codex', '--worktree', str(wt)) == 1
    assert 'codex preflight refused: stale base: worktree' in capsys.readouterr().out
    with pytest.raises(SystemExit, match='only to an executor whose delivery is a worktree commit'):
        s.cli('preflight', '--executor', 'agy', '--worktree', str(wt))
    shutil.rmtree(str(wt))
    with pytest.raises(SystemExit, match='must be an existing absolute directory'):
        s.cli('preflight', '--executor', 'codex', '--worktree', str(wt))
    with pytest.raises(SystemExit, match='must be an existing absolute directory'):
        s.cli('preflight', '--executor', 'codex', '--worktree', 'relative/wt')


def test_stale_base_queued_promotion_checks_current_worktree_head(s, capsys):
    s.configure(stale_base='refuse')
    wt = s.worktree()
    assert s.assign(wt, '--queued') == 0
    pinned = s.xq.state(s.repo)['t1']['base']
    advance_main(s)
    with pytest.raises(SystemExit, match=r'stale base: worktree .* HEAD {} is 1 commit\(s\) behind main'.format(
            s.xq.short(pinned))):
        s.assign(wt)
    s.git('rebase', '-q', 'main', cwd=wt)
    capsys.readouterr()
    assert s.assign(wt) == 0
    assert 'stale' not in capsys.readouterr().err
    assert s.xq.state(s.repo)['t1']['status'] == 'running'


def test_stale_base_fix_quotes_branch_name(s, capsys):
    s.git('branch', 'feat;x')
    s.configure(main='feat;x')
    wt = s.worktree()
    (s.repo / 'main.txt').write_text('later', encoding='utf-8')
    s.git('add', 'main.txt')
    s.git('commit', '-q', '-m', 'later')
    s.git('branch', '-f', 'feat;x', 'main')
    assert s.assign(wt) == 0
    assert "rebase 'feat;x' (or git -C {} merge 'feat;x')".format(wt) in capsys.readouterr().err


def test_main_merged_mid_task_is_not_out_of_scope(s, capsys):
    s.configure(scope_check='enforce')
    wt = s.worktree()
    s.assign(wt)
    advance_main(s)
    s.commit_in(wt, {'src/app.py': 'x'})
    s.git('merge', '-q', '--no-edit', 'main', cwd=wt)
    capsys.readouterr()
    s.cli('finish', '--id', 't1', '--status', 'done')
    assert 'SCOPE' not in capsys.readouterr().err
    assert s.verify() == 0
    assert 'out_of_scope' not in s.last()


def test_rebased_onto_main_mid_task_is_not_out_of_scope(s, capsys):
    s.configure(scope_check='enforce')
    wt = s.worktree()
    s.assign(wt)
    advance_main(s)
    s.commit_in(wt, {'src/app.py': 'x', 'other.txt': 'y'})
    s.git('rebase', '-q', 'main', cwd=wt)
    s.cli('finish', '--id', 't1', '--status', 'done')
    with pytest.raises(SystemExit, match=r'outside the brief\'s declared Files: other.txt; '):
        s.verify()


def test_verify_ignores_lead_leftovers_past_clean_commit(s, capsys):
    s.configure(scope_check='enforce')
    wt = s.worktree()
    s.assign(wt)
    s.commit_in(wt, {'src/app.py': 'x'})
    s.cli('finish', '--id', 't1', '--status', 'done')
    (wt / 'src/__pycache__').mkdir()
    (wt / 'src/__pycache__/app.cpython-38.pyc').write_text('c', encoding='utf-8')
    assert s.verify() == 0
    assert 'out_of_scope' not in s.last()


def test_verify_counts_untracked_paths_of_an_uncommitted_delivery(s, capsys):
    s.configure(scope_check='enforce')
    wt = s.worktree()
    s.assign(wt)
    (wt / 'stray.txt').write_text('z', encoding='utf-8')
    (wt / 'README').write_text('tracked edit', encoding='utf-8')
    s.cli('finish', '--id', 't1', '--status', 'done')
    paths = s.xq.ADAPTERS['codex'].scope_paths(s.repo, s.xq.state(s.repo)['t1'], s.git('rev-parse', 'HEAD', cwd=wt))
    assert 'stray.txt' in paths


def fast_forward_after_assign(s, executor_files):
    """Assign at base B; the lead commits AGENTS.md on main; the worktree fast-forwards to it; the executor commits."""
    s.configure(stale_base='refuse', scope_check='enforce')
    wt = s.worktree()
    assert s.assign(wt) == 0
    (s.repo / 'AGENTS.md').write_text('lead rules\n', encoding='utf-8')
    s.git('add', 'AGENTS.md')
    s.git('commit', '-q', '-m', 'lead: agent rules')
    s.git('merge', '-q', '--ff-only', 'main', cwd=wt)
    s.commit_in(wt, executor_files)
    assert s.cli('finish', '--id', 't1', '--status', 'done') == 0
    return wt


def test_fast_forward_to_main_does_not_attribute_main_commits(s, capsys):
    fast_forward_after_assign(s, {'src/app.py': 'x'})
    capsys.readouterr()
    assert s.verify() == 0
    err = capsys.readouterr().err
    assert 'AGENTS.md' not in err and 'WARN SENSITIVE' not in err and 'WARN SCOPE' not in err
    event = s.last()
    assert event['verdict'] == 'pass' and not event.get('sensitive') and 'out_of_scope' not in event


def test_fast_forward_to_main_still_flags_executor_sensitive_change(s, capsys):
    fast_forward_after_assign(s, {'src/app.py': 'x', '.claude/settings.json': '{}'})
    capsys.readouterr()
    with pytest.raises(SystemExit, match=r'1 sensitive path\(s\): \.claude/settings\.json; inspect'):
        s.verify('--accept-scope', 'settings reviewed')
    assert 'AGENTS.md' not in capsys.readouterr().err
    assert s.verify('--ack-sensitive', '--accept-scope', 'settings reviewed') == 0
    assert s.last()['sensitive'] == ['.claude/settings.json']


def test_fast_forward_to_configured_main_is_not_attributed(s, capsys):
    s.git('branch', 'trunk')
    s.git('checkout', '-q', 'trunk')
    s.configure(main='trunk', stale_base='refuse', scope_check='enforce')
    wt = s.worktree()
    assert s.assign(wt) == 0
    (s.repo / 'CLAUDE.md').write_text('lead\n', encoding='utf-8')
    s.git('add', 'CLAUDE.md')
    s.git('commit', '-q', '-m', 'lead on trunk')
    s.git('merge', '-q', '--ff-only', 'trunk', cwd=wt)
    s.commit_in(wt, {'src/app.py': 'x'})
    assert s.cli('finish', '--id', 't1', '--status', 'done') == 0
    capsys.readouterr()
    assert s.verify() == 0
    err = capsys.readouterr().err
    assert 'CLAUDE.md' not in err and 'WARN SCOPE' not in err


def test_follows_chain_still_reviews_unmerged_predecessor_after_main_merge(s, capsys):
    s.configure(stale_base='refuse')
    wt = s.worktree()
    assert s.assign(wt) == 0
    s.commit_in(wt, {'.github/workflows/ci.yml': 'on: push'})
    assert s.cli('finish', '--id', 't1', '--status', 'failed') == 0
    (s.repo / 'AGENTS.md').write_text('lead rules\n', encoding='utf-8')
    s.git('add', 'AGENTS.md')
    s.git('commit', '-q', '-m', 'lead: agent rules')
    s.git('merge', '-q', '--no-edit', 'main', cwd=wt)
    assert s.cli('assign', '--id', 't2', '--executor', 'codex', '--brief', s.brief(), '--worktree', str(wt),
                 '--follows', 't1') == 0
    s.commit_in(wt, {'src/app.py': 'x'})
    assert s.cli('finish', '--id', 't2', '--status', 'done') == 0
    capsys.readouterr()
    with pytest.raises(SystemExit, match=r'1 sensitive path\(s\): \.github/workflows/ci\.yml; inspect'):
        s.cli('verify', '--id', 't2', '--by', 'reader', '--verdict', 'pass', '--evidence', 'ran')
    assert 'AGENTS.md' not in capsys.readouterr().err


def merge_main_mid_task(s, evil=None):
    s.configure(stale_base='refuse')
    wt = s.worktree()
    assert s.assign(wt) == 0
    s.commit_in(wt, {'src/app.py': 'x'})
    (s.repo / 'AGENTS.md').write_text('lead rules\n', encoding='utf-8')
    s.git('add', 'AGENTS.md')
    s.git('commit', '-q', '-m', 'lead: agent rules')
    s.git('merge', '-q', '--no-ff', '--no-commit', 'main', cwd=wt)
    if evil:
        (wt / evil).parent.mkdir(parents=True, exist_ok=True)
        (wt / evil).write_text('{}', encoding='utf-8')
        s.git('add', evil, cwd=wt)
    s.git('commit', '-q', '-m', 'merge main', cwd=wt)
    assert s.cli('finish', '--id', 't1', '--status', 'done') == 0


def test_merge_of_main_mid_task_is_not_attributed(s, capsys):
    merge_main_mid_task(s)
    capsys.readouterr()
    assert s.verify() == 0
    err = capsys.readouterr().err
    assert 'WARN SENSITIVE' not in err and 'WARN SCOPE' not in err


def test_change_made_inside_a_merge_of_main_is_still_sensitive(s, capsys):
    merge_main_mid_task(s, evil='.claude/settings.json')
    with pytest.raises(SystemExit, match=r'1 sensitive path\(s\): \.claude/settings\.json; inspect'):
        s.verify('--accept-scope', 'reviewed')
    assert 'AGENTS.md' not in capsys.readouterr().err


def test_executor_commit_fast_forwarded_into_main_before_review_is_still_sensitive(s, capsys):
    wt = s.worktree()
    assert s.assign(wt) == 0
    s.commit_in(wt, {'.claude/settings.json': '{}'})
    assert s.cli('finish', '--id', 't1', '--status', 'done') == 0
    s.git('merge', '-q', '--ff-only', 'xq/wt')  # the lead integrates before reviewing
    with pytest.raises(SystemExit, match=r'1 sensitive path\(s\): \.claude/settings\.json; inspect'):
        s.verify('--accept-scope', 'reviewed')
