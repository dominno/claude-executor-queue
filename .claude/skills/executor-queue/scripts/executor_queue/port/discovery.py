"""Plugin discovery (PORT): where the Codex companion and agy-bridge are installed."""

import json
import os
from pathlib import Path
import re
import shlex
import sys

from ..core import util
from . import runner
from .adapter import ADAPTERS


# --- PORT: plugin discovery ---------------------------------------------------------------------------------------

def version_key(path):
    return tuple((1, int(p)) if p.isdigit() else (0, p) for p in re.split(r'(\d+)', str(path)))


def plugin_project_root():
    project = os.environ.get('XQ_ROOT') or runner.git('rev-parse', '--show-toplevel', check=False).strip()
    return Path(project).expanduser().resolve() if project else None


def skill_dirs(project=None):
    """Agent skill directories, in search order: the project's (Claude Code, then Codex), then the user's."""
    root = Path(project).resolve() if project is not None else plugin_project_root()
    local = [root / '.claude/skills', root / '.agents/skills'] if root is not None else []
    return local + [Path.home() / '.claude/skills', Path.home() / '.agents/skills']


def skill_binary(kind, *, project=None):
    """The executor launcher shipped inside an installed agent skill (npx skills add), or None."""
    if ADAPTERS[kind].skill is None:
        return None
    name, relative = ADAPTERS[kind].skill
    for directory in skill_dirs(project):
        candidate = directory / name / relative
        if candidate.is_file():
            return str(candidate)
    return None


def binary_source(kind, path, *, project=None):
    """Where a launcher plugin_binary() returned was found: its skill directory, or the plugin registry."""
    if ADAPTERS[kind].skill is not None and path:
        name, relative = ADAPTERS[kind].skill
        for directory in skill_dirs(project):
            if Path(path) == directory / name / relative:
                return 'skill ' + str(directory / name)
    return 'Claude Code plugin'


def plugin_binary(kind, *, project=None):
    """The executor's launcher: an installed skill first (adapter.skill), then the Claude Code plugin."""
    return skill_binary(kind, project=project) or registry_binary(kind, project=project)


def registry_binary(kind, *, project=None):
    config_dir = Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude').expanduser()
    home = config_dir / 'plugins'
    relative, marketplaces = ADAPTERS[kind].plugin
    installed = home / 'installed_plugins.json'
    try:
        data = json.loads(installed.read_text(encoding='utf-8')).get('plugins', {})
    except FileNotFoundError:
        # Older installs may only have a cache. A present registry takes
        # precedence so a project cannot pick an unrelated cached install.
        for marketplace in marketplaces:
            cache = home / 'cache' / marketplace / kind
            candidates = [path for path in cache.glob('*/' + relative) if path.is_file()]
            if candidates:
                return str(max(candidates, key=version_key))
        return None
    except (OSError, ValueError, AttributeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    worktree = Path(project).resolve() if project is not None else plugin_project_root()
    priority = {None: 0, 'user': 0, 'managed': 0, 'project': 1, 'local': 2}
    for marketplace in marketplaces:
        entries = data.get(kind + '@' + marketplace, [])
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list):
            continue
        candidates = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            scope, install_path = entry.get('scope'), entry.get('installPath')
            if ((scope is not None and not isinstance(scope, str)) or scope not in priority or
                    not isinstance(install_path, str) or not Path(install_path).is_absolute()):
                continue
            if scope in ('project', 'local'):
                registered_project = entry.get('projectPath')
                if (worktree is None or not isinstance(registered_project, str) or
                        not Path(registered_project).is_absolute() or Path(registered_project).resolve() != worktree):
                    continue
            candidate = Path(install_path) / relative
            if candidate.is_file():
                candidates.append((priority[scope], version_key(candidate), str(candidate)))
        if candidates:
            return max(candidates)[2]
    return None


def bridge_command(path):
    return [sys.executable, path] if util.WINDOWS or not os.access(path, os.X_OK) else [path]


def shell_assignment(name, value):
    return ("$" + name + "='" + value.replace("'", "''") + "'") if util.WINDOWS else name + '=' + shlex.quote(value)
