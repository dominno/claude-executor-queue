"""Offline installer integration tests; never touch the real user skill directory."""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def install(tmp_path):
    repo = tmp_path / 'repo with spaces'
    for name in ('install.sh', 'install.ps1', 'scripts/install.py', 'VERSION',
                 'skill/executor-queue/SKILL.md', 'skill/executor-queue/scripts/xq.py'):
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(ROOT / name), str(target))
    home = tmp_path / 'temporary home'
    home.mkdir()
    env = dict(os.environ, HOME=str(home))
    env.pop('CLAUDE_SKILLS_DIR', None)
    env.pop('PYTHONHOME', None)
    env.pop('PYTHONPATH', None)
    skills = home / '.claude/skills'

    def run(*args, custom=None):
        local_env = env.copy()
        if custom is not None:
            local_env['CLAUDE_SKILLS_DIR'] = str(custom)
        return subprocess.run(['sh', str(repo / 'install.sh'), *args], env=local_env,
                              cwd=str(tmp_path), capture_output=True, text=True, timeout=20)

    return SimpleNamespace(repo=repo, home=home, skills=skills,
                           target=skills / 'executor-queue', run=run)


def ok(result):
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_default_install_check_and_standalone_version(install, tmp_path):
    out = ok(install.run())
    assert 'repo version: 3.0.0' in out
    assert 'installed version: not installed' in out
    assert (install.target / 'SKILL.md').read_bytes() == (install.repo / 'skill/executor-queue/SKILL.md').read_bytes()
    assert sorted(p.relative_to(install.target).as_posix() for p in install.target.rglob('*') if p.is_file()) == ['SKILL.md', 'scripts/xq.py']
    assert os.access(str(install.target / 'scripts/xq.py'), os.X_OK)
    receipt = install.skills / '.executor-queue-install.json'
    before = receipt.read_bytes(), receipt.stat().st_mtime_ns
    assert 'SHA-256 hashes match' in ok(install.run('--check'))
    assert 'installed version: 3.0.0' in ok(install.run('--check'))
    assert before == (receipt.read_bytes(), receipt.stat().st_mtime_ns)
    # The installed helper must not need VERSION, docs, the installer or a git repo.
    shutil.rmtree(str(install.repo))
    r = subprocess.run([sys.executable, str(install.target / 'scripts/xq.py'), '--version'],
                       cwd=str(tmp_path), capture_output=True, text=True, timeout=10)
    assert r.returncode == 0 and r.stdout.strip() == 'xq 3.0.0'


def test_custom_skills_dir_install_check_uninstall(install, tmp_path):
    custom = tmp_path / 'custom skills with spaces'
    unrelated = custom / 'another-skill'
    unrelated.mkdir(parents=True)
    (unrelated / 'keep').write_text('keep')
    ok(install.run(custom=custom))
    assert not install.skills.exists()
    ok(install.run('--check', custom=custom))
    ok(install.run('--uninstall', custom=custom))
    assert not (custom / 'executor-queue').exists()
    assert not (custom / '.executor-queue-install.json').exists()
    assert (unrelated / 'keep').read_text() == 'keep'


def test_missing_check_and_uninstall_do_not_create_dirs(install):
    result = install.run('--check')
    assert result.returncode == 1 and 'MISSING' in result.stdout
    assert 'installed version: not installed' in result.stdout
    ok(install.run('--uninstall'))
    assert not install.skills.exists()


@pytest.mark.parametrize('edit', ['skill', 'script', 'extra', 'empty-directory'])
def test_edits_refuse_overwrite_and_uninstall_until_forced(install, edit):
    ok(install.run())
    path = install.target / {'skill': 'SKILL.md', 'script': 'scripts/xq.py',
                             'extra': 'notes.txt', 'empty-directory': 'notes'}[edit]
    if edit == 'empty-directory':
        path.mkdir()
    else:
        path.write_text('local edits\n', encoding='utf-8')
    before = (path.is_dir(), None if path.is_dir() else path.read_bytes())
    for args in [(), ('--uninstall',)]:
        r = install.run(*args)
        assert r.returncode != 0 and '--force' in r.stderr
        assert before == (path.is_dir(), None if path.is_dir() else path.read_bytes())
    r = install.run('--check', '--force')
    assert r.returncode == 1 and 'MISMATCH' in r.stdout
    ok(install.run('--force'))
    ok(install.run('--check'))
    (install.target / 'notes').write_text('local')
    ok(install.run('--uninstall', '--force'))
    assert not install.target.exists()


def test_clean_update_uses_saved_hashes_and_prints_both_versions(install):
    ok(install.run())
    for name in ['VERSION', 'skill/executor-queue/SKILL.md', 'skill/executor-queue/scripts/xq.py']:
        p = install.repo / name
        p.write_text(p.read_text().replace('3.0.0', '3.0.1'))
    r = install.run('--check')
    assert r.returncode == 1
    assert 'repo version: 3.0.1' in r.stdout and 'installed version: 3.0.0' in r.stdout
    ok(install.run())
    assert 'installed version: 3.0.1' in ok(install.run('--check'))


def test_uninstall_clean_old_copy_even_after_repo_changes(install):
    ok(install.run())
    (install.repo / 'skill/executor-queue/SKILL.md').write_text('new source')
    ok(install.run('--uninstall'))
    assert not install.target.exists()


@pytest.mark.parametrize('receipt', ['missing', 'invalid-json', 'invalid-schema'])
def test_unmanaged_copy_is_protected_unless_identical(install, receipt):
    ok(install.run())
    path = install.skills / '.executor-queue-install.json'
    if receipt == 'missing':
        path.unlink()
    else:
        path.write_text('not json' if receipt == 'invalid-json' else '[]')
    # An identical legacy/unmanaged copy can be safely adopted.
    ok(install.run())
    path.unlink()
    (install.target / 'SKILL.md').write_text('legacy changes')
    r = install.run()
    assert r.returncode != 0 and 'unmanaged' in r.stderr
    ok(install.run('--force'))
    ok(install.run('--check'))


def test_symlink_destination_is_never_followed(install, tmp_path):
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    (elsewhere / 'keep').write_text('untouched')
    install.skills.mkdir(parents=True)
    install.target.symlink_to(elsewhere, target_is_directory=True)
    for args in [(), ('--check',), ('--uninstall', '--force')]:
        r = install.run(*args)
        assert r.returncode != 0 and 'real directory' in r.stderr
    assert (elsewhere / 'keep').read_text() == 'untouched'


def test_symlink_inside_install_is_an_edit(install, tmp_path):
    ok(install.run())
    external = tmp_path / 'external'
    external.mkdir()
    (external / 'keep').write_text('untouched')
    shutil.rmtree(str(install.target / 'scripts'))
    (install.target / 'scripts').symlink_to(external, target_is_directory=True)
    assert install.run().returncode != 0
    ok(install.run('--force'))
    assert (external / 'keep').read_text() == 'untouched'
    ok(install.run('--check'))


def test_unknown_flags_and_conflicting_modes_refuse_without_writes(install):
    for args in [('--typo',), ('--check', '--uninstall')]:
        r = install.run(*args)
        assert r.returncode == 2
    assert not install.skills.exists()


def test_source_cache_is_not_installed(install):
    cache = install.repo / 'skill/executor-queue/scripts/__pycache__'
    cache.mkdir()
    (cache / 'xq.pyc').write_bytes(b'cache')
    ok(install.run())
    assert not (install.target / 'scripts/__pycache__').exists()
    ok(install.run('--check'))


def test_release_markers_and_python38_installer_syntax():
    version = (ROOT / 'VERSION').read_text().strip()
    skill = (ROOT / 'skill/executor-queue/SKILL.md').read_text()
    assert len(skill.splitlines()) <= 110
    assert '\nversion: ' + version + '\n' in skill
    frontmatter = skill.split('---', 2)[1]
    assert 'version:' not in frontmatter
    for name in ('scripts/install.py', 'skill/executor-queue/scripts/xq.py'):
        ast.parse((ROOT / name).read_text(), feature_version=(3, 8))
    r = subprocess.run([sys.executable, str(ROOT / 'skill/executor-queue/scripts/xq.py'), '--version'],
                       capture_output=True, text=True, timeout=10)
    assert r.returncode == 0 and r.stdout.strip() == 'xq ' + version


def test_example_json_and_exact_cloud_sections():
    for name in ('agy-job.json', '.agents/config.json', 'settings.json', 'settings.windows.json'):
        json.loads((ROOT / 'examples' / name).read_text())
    brief = (ROOT / 'examples/cloud-batch.md').read_text()
    assert '\n## parser-empty-01\n' in brief and '\n## parser-docs-01\n' in brief
