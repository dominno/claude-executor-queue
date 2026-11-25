"""Offline installer integration tests; never touch the real user skill directory."""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
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
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home))
    env.pop('CLAUDE_CONFIG_DIR', None)
    env.pop('CLAUDE_SKILLS_DIR', None)
    env.pop('PYTHONHOME', None)
    env.pop('PYTHONPATH', None)
    skills = home / '.claude/skills'

    def run(*args, custom=None, config=None):
        local_env = env.copy()
        if config is not None:
            local_env['CLAUDE_CONFIG_DIR'] = str(config)
        if custom is not None:
            local_env['CLAUDE_SKILLS_DIR'] = str(custom)
        launcher = [sys.executable, str(repo / 'scripts/install.py')] if os.name == 'nt' else [
            'sh', str(repo / 'install.sh')]
        return subprocess.run([*launcher, *args], env=local_env,
                              cwd=str(tmp_path), capture_output=True, text=True, timeout=20)

    return SimpleNamespace(repo=repo, home=home, skills=skills, env=env,
                           target=skills / 'executor-queue', run=run)


def ok(result):
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def load_installer(install):
    spec = importlib.util.spec_from_file_location('installer_under_test', install.repo / 'scripts/install.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    install.skills.mkdir(parents=True)
    assert install.run('--check').returncode == 1
    assert not list(install.skills.iterdir())


def test_installer_transaction_config_root_and_explicit_override(install, tmp_path):
    config = tmp_path / 'config root'
    custom = tmp_path / 'explicit skills'
    ok(install.run(config=config))
    ok(install.run('--check', config=config))
    assert (config / 'skills/executor-queue/LICENSE').is_file()
    ok(install.run(config=config, custom=custom))
    assert (custom / 'executor-queue/LICENSE').is_file()
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
    assert len(skill.splitlines()) <= 125
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


def test_council_08_guarded_hook_and_uninstall_message(install):
    for name in ('settings.json', 'settings.windows.json'):
        command = json.loads((ROOT / 'examples' / name).read_text())['hooks']['Stop'][0]['hooks'][0]['command']
        assert '[ -f ' in command and 'exit 0' in command and 'exec ' in command
        result = subprocess.run(['sh', '-c', command], env=dict(os.environ, HOME=str(install.home)),
                                input='{"stop_hook_active":true}', capture_output=True, text=True)
        assert result.returncode == 0
    ok(install.run())
    assert 'project hooks' in ok(install.run('--uninstall'))


def test_council_16_powershell_launcher_probe():
    import re
    text = (ROOT / 'install.ps1').read_text()
    candidates = re.search(r'foreach \(\$candidate in @\(([^)]+)\)', text).group(1)
    assert re.findall(r"'([^']+)'", candidates) == ['py', 'python', 'python3']
    assert "@('-3')" in text
    assert 'WindowsApps' in text
    assert re.search(r'try\s*\{[\s\S]+?\$LASTEXITCODE[\s\S]+?\}\s*catch\s*\{', text)
    assert 'windows-latest' in (ROOT / '.github/workflows/test.yml').read_text()


@pytest.mark.parametrize(('previous_install', 'failing_file', 'mode'), [
    (True, 'executor-queue', ()),
    (True, 'receipt.json', ()),
    (False, 'receipt.json', ()),
    (True, 'previous-receipt.json', ('--uninstall',)),
])
def test_installer_transaction_failed_rename_rolls_back(install, monkeypatch, previous_install, failing_file, mode):
    receipt = install.skills / '.executor-queue-install.json'
    if previous_install:
        ok(install.run())
        previous = (install.target / 'SKILL.md').read_bytes(), receipt.read_bytes()
        (install.repo / 'skills/executor-queue/SKILL.md').write_text('new payload')
    module = load_installer(install)
    replace = module.os.replace

    def fail_publication(src, dst):
        if (Path(src).name == failing_file and Path(dst) in (install.target, receipt)
                or Path(dst).name == failing_file == 'previous-receipt.json'):
            raise OSError('simulated publication failure')
        return replace(src, dst)

    monkeypatch.setattr(module.os, 'replace', fail_publication)
    monkeypatch.setenv('CLAUDE_SKILLS_DIR', str(install.skills))
    monkeypatch.setattr(sys, 'argv', ['install', *mode])
    with pytest.raises(OSError, match='publication failure'):
        module.main()
    if previous_install:
        assert ((install.target / 'SKILL.md').read_bytes(), receipt.read_bytes()) == previous
    else:
        assert not install.target.exists()
        assert not receipt.exists()
    ok(install.run())
    ok(install.run('--check'))


def launch_installer(install, *, repo=None, args=(), interrupt_at='', ready=None, release=None):
    installer = (repo or install.repo) / 'scripts/install.py'
    # Instrument only filesystem boundaries inside the child. os._exit emulates
    # a stopped process without giving the installer's rollback handler a turn.
    code = """
import os
from pathlib import Path
import runpy
import sys
import time

installer, point, ready, release, *args = sys.argv[1:]
replace = os.replace
open_file = os.open

def replace_at_boundary(source, target):
    replace(source, target)
    if Path(target).name == point:
        if not release:
            os._exit(78)
        Path(ready).touch()
        end = time.monotonic() + 20
        while not Path(release).exists():
            if time.monotonic() >= end:
                raise RuntimeError('timed out waiting for the test')
            time.sleep(0.01)

def open_at_boundary(path, *args, **kwargs):
    if ready and not point and Path(path).name == '.executor-queue-install.lock':
        Path(ready).touch()
    return open_file(path, *args, **kwargs)

os.replace = replace_at_boundary
os.open = open_at_boundary
sys.argv = [installer, *args]
runpy.run_path(installer, run_name='__main__')
"""
    return subprocess.Popen(
        [sys.executable, '-c', code, str(installer), interrupt_at,
         str(ready) if ready else '', str(release) if release else '', *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=install.env)


@pytest.mark.parametrize(('args', 'interrupt_at'), [
    ((), 'previous'),
    ((), 'executor-queue'),
    ((), '.executor-queue-install.json'),
    (('--uninstall',), 'previous-receipt.json'),
])
def test_installer_transaction_recovers_after_process_exit(install, args, interrupt_at):
    ok(install.run())
    receipt = install.skills / '.executor-queue-install.json'
    original = receipt.read_bytes()
    source = install.repo / 'skills/executor-queue/SKILL.md'
    original_source = source.read_bytes()
    source.write_bytes(original_source + b'\nnew checkout\n')
    with launch_installer(install, args=args, interrupt_at=interrupt_at) as child:
        child.communicate(timeout=20)
        assert child.returncode == 78
    stage = install.skills / '.executor-queue-install-stage'
    backup = stage / 'previous/SKILL.md'
    assert backup.read_bytes() == original_source
    check = install.run('--check')
    assert check.returncode == 1 and 'PENDING' in check.stdout
    assert backup.read_bytes() == original_source

    # Restoring the old source makes recovery observable even when the new
    # payload and its receipt both reached the destination before the exit.
    source.write_bytes(original_source)
    ok(install.run())
    assert receipt.read_bytes() == original
    assert (install.target / 'SKILL.md').read_bytes() == original_source
    assert not stage.exists()


def test_installer_transaction_preserves_edits_and_saved_backup(install):
    ok(install.run())
    target = install.target / 'SKILL.md'
    original = target.read_bytes()
    (install.repo / 'skills/executor-queue/SKILL.md').write_bytes(original + b'\nnew checkout\n')
    with launch_installer(install, interrupt_at='executor-queue') as child:
        child.communicate(timeout=20)
        assert child.returncode == 78
    target.write_bytes(b'local edit after interruption')
    result = install.run('--force')
    assert result.returncode != 0
    assert 'destination changed during the interrupted operation' in result.stderr
    assert target.read_bytes() == b'local edit after interruption'
    assert (install.skills / '.executor-queue-install-stage/previous/SKILL.md').read_bytes() == original


def test_installer_transaction_committed_update_survives_interrupted_cleanup(install):
    ok(install.run())
    source = install.repo / 'skills/executor-queue/SKILL.md'
    source.write_bytes(source.read_bytes() + b'\nnew checkout\n')
    with launch_installer(install, interrupt_at='complete') as child:
        child.communicate(timeout=20)
        assert child.returncode == 78
    stage = install.skills / '.executor-queue-install-stage'
    assert stage.is_dir()
    ok(install.run('--check'))
    assert stage.is_dir()
    ok(install.run('--uninstall'))
    assert not stage.exists()
    assert not install.target.exists()


def test_installer_transaction_retires_finished_stage_when_cleanup_is_blocked(install, monkeypatch, capsys):
    ok(install.run())
    source = install.repo / 'skills/executor-queue/SKILL.md'
    original = source.read_bytes()
    source.write_bytes(original + b'\nnew checkout\n')
    with launch_installer(install, interrupt_at='complete') as child:
        child.communicate(timeout=20)
        assert child.returncode == 78

    module = load_installer(install)
    rmtree = module.shutil.rmtree

    def deny_removal(path, *args, **kwargs):
        if Path(path).name == 'previous' and Path(path).parent.name == '.executor-queue-install-stage':
            raise PermissionError('simulated cleanup denial')
        return rmtree(path, *args, **kwargs)

    monkeypatch.setattr(module.shutil, 'rmtree', deny_removal)
    monkeypatch.setenv('CLAUDE_SKILLS_DIR', str(install.skills))
    monkeypatch.setattr(sys, 'argv', ['install', '--uninstall'])
    assert module.main() == 0
    assert 'cleanup data retained' in capsys.readouterr().err
    assert not install.target.exists()
    assert not (install.skills / '.executor-queue-install-stage').exists()
    copies = install.skills.glob('.executor-queue-cleanup-*/stage/previous/SKILL.md')
    assert {copy.read_bytes() for copy in copies} == {original, source.read_bytes()}


def test_installer_transaction_diagnoses_legacy_backup_without_removing_it(install):
    ok(install.run())
    backup = install.skills / '.executor-queue-legacy01/previous'
    backup.parent.mkdir()
    install.target.rename(backup)
    result = install.run()
    assert result.returncode != 0
    assert 'previous installation backup needs manual recovery' in result.stderr
    assert (backup / 'SKILL.md').is_file()
    assert not install.target.exists()


def wait_for_file(path):
    end = time.monotonic() + 10
    while not path.exists() and time.monotonic() < end:
        time.sleep(0.01)
    assert path.is_file()


def test_installer_transaction_serializes_versions_and_check(install, tmp_path):
    ok(install.run())
    next_repo = tmp_path / 'another checkout'
    shutil.copytree(install.repo, next_repo)
    source = install.repo / 'skills/executor-queue/SKILL.md'
    source.write_bytes(source.read_bytes() + b'\ncheckout A\n')
    next_source = next_repo / 'skills/executor-queue/SKILL.md'
    next_source.write_bytes(next_source.read_bytes() + b'\ncheckout B\n')
    a_ready, b_ready, check_ready, release = (tmp_path / name for name in (
        'a-ready', 'b-ready', 'check-ready', 'release'))
    with launch_installer(install, interrupt_at='executor-queue', ready=a_ready, release=release) as first:
        try:
            wait_for_file(a_ready)
            with launch_installer(install, repo=next_repo, ready=b_ready) as second, \
                    launch_installer(install, args=('--check',), ready=check_ready) as check:
                try:
                    wait_for_file(b_ready)
                    wait_for_file(check_ready)
                    with pytest.raises(subprocess.TimeoutExpired):
                        check.wait(timeout=0.1)
                    assert second.poll() is None
                finally:
                    release.touch()
                out, err = second.communicate(timeout=20)
                assert second.returncode == 0, out + err
                out, err = check.communicate(timeout=20)
                # The check can legitimately run either before or after the
                # second install, but never inspect a half-published update.
                assert check.returncode in (0, 1) and 'PENDING' not in out, out + err
        finally:
            release.touch()
        out, err = first.communicate(timeout=20)
        assert first.returncode == 0, out + err
    assert (install.target / 'SKILL.md').read_bytes() == next_source.read_bytes()
    receipt = json.loads((install.skills / '.executor-queue-install.json').read_bytes())
    assert receipt['hashes']['SKILL.md'] == hashlib.sha256(next_source.read_bytes()).hexdigest()


def test_installer_transaction_lock_reports_active_installer(install, tmp_path, monkeypatch):
    active, release = tmp_path / 'active', tmp_path / 'release'
    module = load_installer(install)
    monkeypatch.setattr(module, 'LOCK_WAIT_SECONDS', 0.1)
    monkeypatch.setenv('CLAUDE_SKILLS_DIR', str(install.skills))
    with launch_installer(install, interrupt_at='executor-queue', ready=active, release=release) as first:
        try:
            wait_for_file(active)
            for args in (('--check',), ()):
                monkeypatch.setattr(sys, 'argv', ['install', *args])
                with pytest.raises(OSError, match='another installer command is active.*retry after it finishes'):
                    module.main()
                assert first.poll() is None
        finally:
            release.touch()
        out, err = first.communicate(timeout=20)
        assert first.returncode == 0, out + err
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
