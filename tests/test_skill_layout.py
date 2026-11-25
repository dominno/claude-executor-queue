"""The repository ships one self-contained agent skill; installers copy that directory as is."""
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / '.claude/skills/executor-queue'
SCRIPT = SKILL / 'scripts/xq.py'
PACKAGE = SKILL / 'scripts/executor_queue'
PACKAGE_FILES = ('__init__.py', 'core/__init__.py', 'core/ledger.py', 'core/state.py', 'core/config.py',
                 'core/brief.py', 'core/review.py', 'core/importer.py', 'core/seams.py',
                 'port/__init__.py', 'port/runner.py', 'port/adapter.py', 'port/discovery.py',
                 'adapters/__init__.py', 'adapters/codex.py', 'adapters/agy.py', 'adapters/cloud.py',
                 'cli/__init__.py', 'cli/main.py', 'cli/parser.py', 'cli/commands/__init__.py',
                 'cli/commands/setup.py', 'cli/commands/record.py', 'cli/commands/read.py',
                 'cli/commands/executors.py')
LINK = re.compile(r'\]\(([^)\s]+)\)')


def skill_text():
    return (SKILL / 'SKILL.md').read_text(encoding='utf-8')


def xq_version():
    result = subprocess.run([sys.executable, str(SCRIPT), '--version'], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip().split()[-1]


def relative_links(path):
    """(target file, raw link) for every relative Markdown link in path, anchors dropped."""
    for target in LINK.findall(path.read_text(encoding='utf-8')):
        if re.match(r'[a-z][a-z0-9+.-]*:', target) or target.startswith('#'):
            continue
        yield (path.parent / target.split('#', 1)[0]).resolve(), target


def test_skill_frontmatter_size_and_version():
    text = skill_text()
    assert len(text.splitlines()) <= 125
    frontmatter = text.split('---', 2)[1]
    fields = dict(line.split(':', 1) for line in frontmatter.strip().splitlines())
    assert fields['name'].strip() == SKILL.name
    assert fields['description'].strip()
    assert 'version' not in fields
    version = xq_version()
    assert '\nversion: ' + version + '\n' in text
    assert '— ' + version + ' —' in (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
    assert (ROOT / 'LICENSE').read_bytes() == (SKILL / 'LICENSE').read_bytes()
    for source in [SCRIPT] + sorted(PACKAGE.rglob('*.py')):
        ast.parse(source.read_text(encoding='utf-8'), feature_version=(3, 8))


def test_codex_skill_path_is_a_relative_symlink_to_the_skill():
    link = ROOT / '.agents/skills/executor-queue'
    if os.name == 'nt' and not link.is_symlink():
        pytest.skip('checkout without symlink support')
    assert link.is_symlink()
    assert Path(os.readlink(str(link))).as_posix() == '../../.claude/skills/executor-queue'
    assert link.resolve() == SKILL.resolve()


def test_skill_links_stay_inside_the_skill_and_resolve():
    documents = [SKILL / 'SKILL.md', SKILL / 'README.md', SKILL / 'assets/README.md']
    documents += sorted((SKILL / 'references').glob('*.md'))
    for document in documents:
        for target, raw in relative_links(document):
            assert target.exists(), '{}: broken link {}'.format(document.relative_to(ROOT), raw)
            assert SKILL.resolve() in target.parents or target == SKILL.resolve(), (
                '{}: {} leaves the skill directory'.format(document.relative_to(ROOT), raw))


def test_repository_documents_links_resolve():
    documents = [ROOT / name for name in ('README.md', 'CONTRIBUTING.md', 'RELEASING.md', 'SECURITY.md',
                                          'CHANGELOG.md')]
    for document in documents:
        for target, raw in relative_links(document):
            assert target.exists(), '{}: broken link {}'.format(document.name, raw)


def test_installed_copy_runs_without_the_repository(tmp_path):
    """A copy of the skill directory alone (what npx skills add installs) runs init and status."""
    copy = tmp_path / 'skills/executor-queue'
    shutil.copytree(str(SKILL), str(copy), ignore=shutil.ignore_patterns('__pycache__'))
    for name in PACKAGE_FILES:
        assert (copy / 'scripts/executor_queue' / name).is_file(), name
    project = tmp_path / 'project'
    project.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.startswith(('GIT_', 'XQ_'))}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, PYTHONDONTWRITEBYTECODE='1')
    subprocess.run(['git', 'init', '-q'], cwd=str(project), check=True, env=env, timeout=15)
    for args, expected in ((['--version'], 'xq ' + xq_version()), (['init', '--local'], None),
                           (['status'], 'no tasks')):
        result = subprocess.run([sys.executable, str(copy / 'scripts/xq.py'), *args], cwd=str(project),
                                capture_output=True, text=True, env=env, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        if expected is not None:
            assert result.stdout.strip() == expected


def test_assets_parse_and_cloud_sections_are_exact():
    for name in ('agy-job.json', 'config.json', 'config.full.json', 'settings.json', 'settings.windows.json'):
        json.loads((SKILL / 'assets' / name).read_text(encoding='utf-8'))
    brief = (SKILL / 'assets/cloud-batch.md').read_text(encoding='utf-8')
    assert '\n## parser-empty-01\n' in brief and '\n## parser-docs-01\n' in brief


@pytest.mark.skipif(shutil.which('sh') is None, reason='needs a POSIX shell')
def test_stop_hook_snippets_skip_a_missing_helper(tmp_path):
    for name in ('settings.json', 'settings.windows.json'):
        settings = json.loads((SKILL / 'assets' / name).read_text(encoding='utf-8'))
        command = settings['hooks']['Stop'][0]['hooks'][0]['command']
        assert '[ -f ' in command and 'exit 0' in command and 'exec ' in command
        result = subprocess.run(['sh', '-c', command], env=dict(os.environ, HOME=str(tmp_path), CLAUDE_PROJECT_DIR=str(tmp_path)),
                                input='{"stop_hook_active":true}', capture_output=True, text=True, timeout=10)
        assert result.returncode == 0


def test_entry_point_runs_through_symlinks_and_writes_no_bytecode(tmp_path):
    """Installs link the skill directory (or the script): xq.py finds its package through the real path and
    leaves no __pycache__ inside the skill."""
    copy = tmp_path / 'skills/executor-queue'
    shutil.copytree(str(SKILL), str(copy), ignore=shutil.ignore_patterns('__pycache__'))
    linked_dir, linked_file = tmp_path / 'linked-skill', tmp_path / 'bin/xq.py'
    linked_file.parent.mkdir()
    try:
        linked_dir.symlink_to(copy, target_is_directory=True)
        linked_file.symlink_to(copy / 'scripts/xq.py')
    except (OSError, NotImplementedError):
        pytest.skip('no symlink support')
    env = {key: value for key, value in os.environ.items() if key != 'PYTHONDONTWRITEBYTECODE'}
    for script in (linked_dir / 'scripts/xq.py', linked_file):
        result = subprocess.run([sys.executable, str(script), '--version'], capture_output=True, text=True,
                                env=env, timeout=30)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'xq ' + xq_version()
    assert not list(copy.rglob('__pycache__'))
