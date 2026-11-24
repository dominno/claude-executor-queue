"""Offline installer integration tests; never touch the real user skill directory."""
import ast
import hashlib
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
                 'skills/executor-queue/LICENSE', 'skills/executor-queue/SKILL.md',
                 'skills/executor-queue/scripts/xq.py'):
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
    version = (install.repo / 'VERSION').read_text().strip()
    out = ok(install.run())
    assert 'repo version: ' + version in out
    assert 'installed version: not installed' in out
    installed = sorted(p.relative_to(install.target).as_posix() for p in install.target.rglob('*') if p.is_file())
    assert installed == ['LICENSE', 'SKILL.md', 'scripts/xq.py']
    for name in installed:
        assert (install.target / name).read_bytes() == (install.repo / 'skills/executor-queue' / name).read_bytes()
    assert os.access(str(install.target / 'scripts/xq.py'), os.X_OK)
    receipt = install.skills / '.executor-queue-install.json'
    before = receipt.read_bytes(), receipt.stat().st_mtime_ns
    assert 'SHA-256 hashes match' in ok(install.run('--check'))
    assert 'installed version: ' + version in ok(install.run('--check'))
    assert before == (receipt.read_bytes(), receipt.stat().st_mtime_ns)
    # The installed helper must not need VERSION, docs, the installer or a git repo.
    shutil.rmtree(str(install.repo))
    r = subprocess.run([sys.executable, str(install.target / 'scripts/xq.py'), '--version'],
                       cwd=str(tmp_path), capture_output=True, text=True, timeout=10)
    assert r.returncode == 0 and r.stdout.strip() == 'xq ' + version


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


def test_clean_32_update_uses_saved_hashes_and_installs_license(install):
    ok(install.run())
    version = (install.repo / 'VERSION').read_text().strip()
    (install.target / 'LICENSE').unlink()
    receipt = install.skills / '.executor-queue-install.json'
    data = json.loads(receipt.read_text())
    data['version'] = '3.2.0'
    del data['hashes']['LICENSE']
    for name in ('SKILL.md', 'scripts/xq.py'):
        path = install.target / name
        old_contents = path.read_bytes().replace(version.encode(), b'3.2.0')
        path.write_bytes(old_contents)
        data['hashes'][name] = hashlib.sha256(old_contents).hexdigest()
    receipt.write_text(json.dumps(data))
    r = install.run('--check')
    assert r.returncode == 1
    assert 'repo version: ' + version in r.stdout
    assert 'installed version: 3.2.0' in r.stdout
    ok(install.run())
    assert 'installed version: ' + version in ok(install.run('--check'))
    assert (install.target / 'LICENSE').is_file()


def test_uninstall_clean_old_copy_even_after_repo_changes(install):
    ok(install.run())
    (install.repo / 'skills/executor-queue/SKILL.md').write_text('new source')
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
    cache = install.repo / 'skills/executor-queue/scripts/__pycache__'
    cache.mkdir()
    (cache / 'xq.pyc').write_bytes(b'cache')
    ok(install.run())
    assert not (install.target / 'scripts/__pycache__').exists()
    ok(install.run('--check'))


def test_release_markers_and_python38_installer_syntax():
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    skill = (ROOT / 'skills/executor-queue/SKILL.md').read_text(encoding='utf-8')
    assert len(skill.splitlines()) <= 110
    assert '\nversion: ' + version + '\n' in skill
    assert '— ' + version + ' —' in (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
    major_minor = version.rsplit('.', 1)[0]
    notes = ROOT / 'docs' / ('v' + major_minor + '-notes.md')
    assert '# v' + version in notes.read_text(encoding='utf-8')
    assert (ROOT / 'LICENSE').read_bytes() == (ROOT / 'skills/executor-queue/LICENSE').read_bytes()
    frontmatter = skill.split('---', 2)[1]
    assert 'version:' not in frontmatter
    for name in ('scripts/install.py', 'skills/executor-queue/scripts/xq.py'):
        ast.parse((ROOT / name).read_text(encoding='utf-8'), feature_version=(3, 8))
    r = subprocess.run([sys.executable, str(ROOT / 'skills/executor-queue/scripts/xq.py'), '--version'],
                       capture_output=True, text=True, timeout=10)
    assert r.returncode == 0 and r.stdout.strip() == 'xq ' + version


def test_example_json_and_exact_cloud_sections():
    for name in ('agy-job.json', '.agents/config.json', 'settings.json', 'settings.windows.json'):
        json.loads((ROOT / 'examples' / name).read_text())
    brief = (ROOT / 'examples/cloud-batch.md').read_text()
    assert '\n## parser-empty-01\n' in brief and '\n## parser-docs-01\n' in brief


def test_council_08_guarded_hook_and_upgrade_swap(install, monkeypatch):
    for name in ('settings.json', 'settings.windows.json'):
        command = json.loads((ROOT / 'examples' / name).read_text())['hooks']['Stop'][0]['hooks'][0]['command']
        assert '[ -f ' in command and 'exit 0' in command and 'exec ' in command
        result = subprocess.run(['sh', '-c', command], env=dict(os.environ, HOME=str(install.home)),
                                input='{"stop_hook_active":true}', capture_output=True, text=True)
        assert result.returncode == 0
    ok(install.run())
    assert 'project hooks' in ok(install.run('--uninstall'))
    source = (ROOT / 'scripts/install.py').read_text()
    assert 'shutil.rmtree(str(target))' not in source[source.index('# Stage'):]
    assert 'os.replace(str(target)' in source


def test_council_16_powershell_launcher_probe():
    import re
    text = (ROOT / 'install.ps1').read_text()
    candidates = re.search(r'foreach \(\$candidate in @\(([^)]+)\)', text).group(1)
    assert re.findall(r"'([^']+)'", candidates) == ['py', 'python', 'python3']
    assert "@('-3')" in text
    assert 'WindowsApps' in text
    assert re.search(r'try\s*\{[\s\S]+?\$LASTEXITCODE[\s\S]+?\}\s*catch\s*\{', text)
    assert 'windows-latest' in (ROOT / '.github/workflows/test.yml').read_text()


@pytest.mark.parametrize(('previous_install', 'failing_file'),
                         [(True, 'executor-queue'), (True, 'receipt.json'), (False, 'receipt.json')])
def test_council_08_failed_install_rolls_back(install, monkeypatch, previous_install, failing_file):
    import importlib.util
    receipt = install.skills / '.executor-queue-install.json'
    if previous_install:
        ok(install.run())
        previous = (install.target / 'SKILL.md').read_bytes(), receipt.read_bytes()
        (install.repo / 'skills/executor-queue/SKILL.md').write_text('new payload')
    spec = importlib.util.spec_from_file_location('installer_under_test', install.repo / 'scripts/install.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    replace = module.os.replace

    def fail_publication(src, dst):
        if Path(src).name == failing_file and Path(dst) in (install.target, receipt):
            raise OSError('simulated publication failure')
        return replace(src, dst)

    monkeypatch.setattr(module.os, 'replace', fail_publication)
    monkeypatch.setenv('CLAUDE_SKILLS_DIR', str(install.skills))
    monkeypatch.setattr(sys, 'argv', ['install'])
    with pytest.raises(OSError, match='publication failure'):
        module.main()
    if previous_install:
        assert ((install.target / 'SKILL.md').read_bytes(), receipt.read_bytes()) == previous
    else:
        assert not install.target.exists()
        assert not receipt.exists()
    ok(install.run())
    ok(install.run('--check'))


@pytest.mark.skipif(os.name != 'nt', reason='requires native PowerShell and Windows launcher')
def test_council_16_windows_native_install(tmp_path):
    target = tmp_path / 'skills with spaces'
    env = dict(os.environ, CLAUDE_SKILLS_DIR=str(target))
    for flags in [[], ['--check'], ['--uninstall']]:
        result = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                 '-File', str(ROOT / 'install.ps1'), *flags],
                                env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        if not flags:
            for name in ('LICENSE', 'SKILL.md', 'scripts/xq.py'):
                assert (target / 'executor-queue' / name).is_file()
    assert not (target / 'executor-queue').exists()
