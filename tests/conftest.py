import ast
import functools
import importlib
import os
from pathlib import Path
import pkgutil
import sys

import pytest


@pytest.fixture
def isolated_queue_environment(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith(('GIT_', 'XQ_')) or key in ('CLAUDE_CONFIG_DIR', 'CLAUDE_PLUGIN_DATA'):
            monkeypatch.delenv(key)

    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(home / '.config'))
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', os.devnull)
    monkeypatch.setenv('GIT_ALLOW_PROTOCOL', 'file')
    monkeypatch.setenv('GIT_TERMINAL_PROMPT', '0')
    return home


SCRIPTS = Path(__file__).resolve().parents[1] / '.claude/skills/executor-queue/scripts'
PACKAGE = 'executor_queue'


@functools.lru_cache(maxsize=None)
def defined_names(path):
    """Names a module file defines at top level: def, class and assignment targets."""
    names = []
    for node in ast.parse(Path(path).read_text(encoding='utf-8')).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names += [n.id for target in targets for n in ast.walk(target) if isinstance(n, ast.Name)]
    return tuple(names)


class XqPackage:
    """The executor_queue package (what scripts/xq.py runs) read as one namespace.

    xq.<name> is the attribute of the module that defines <name> (a top-level def, class or assignment), else of
    the first module that imports it (stdlib modules such as xq.os). Patch a name where it is looked up at call
    time, which is the module that defines it: monkeypatch.setattr(xq.module_of('run'), 'run', fake).
    """

    def __init__(self, modules):
        homes = {}
        for module in modules:
            for name in defined_names(module.__file__):
                assert homes.setdefault(name, module) is module, name + ' is defined in two modules'
        object.__setattr__(self, '_modules', modules)
        object.__setattr__(self, '_homes', homes)

    def module_of(self, name):
        """The module that defines name."""
        try:
            return self._homes[name]
        except KeyError:
            raise AttributeError('no executor_queue module defines ' + name) from None

    def __getattr__(self, name):
        if name in self._homes:
            return getattr(self._homes[name], name)
        for module in self._modules:
            if hasattr(module, name):
                return getattr(module, name)
        raise AttributeError(name)

    def __setattr__(self, name, value):
        raise AttributeError('patch xq.module_of({!r}), not the package namespace'.format(name))


def load_xq():
    """A fresh import of every executor_queue module, like the per-test module load of the single-file xq.py."""
    for name in [name for name in sys.modules if name == PACKAGE or name.startswith(PACKAGE + '.')]:
        del sys.modules[name]
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    package = importlib.import_module(PACKAGE)
    modules = [package] + [importlib.import_module(info.name) for info in
                           pkgutil.walk_packages(package.__path__, PACKAGE + '.')]
    return XqPackage(modules)
