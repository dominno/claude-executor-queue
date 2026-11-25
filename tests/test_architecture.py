"""The executor_queue package keeps its layers: imports point inward (cli -> adapters -> port -> core), CORE starts
no process, there are no import cycles, modules and functions stay small, and every executor has an adapter."""
import ast
from pathlib import Path

from conftest import load_xq, PACKAGE, SCRIPTS

ROOT = SCRIPTS / PACKAGE
LAYERS = ('core', 'port', 'adapters', 'cli')
# Layer -> the layers it may import (the package root holds only VERSION).
ALLOWED = {
    'core': {'core'},
    'port': {'port', 'core'},
    'adapters': {'adapters', 'port', 'core'},
    'cli': {'cli', 'adapters', 'port', 'core'},
}
PROCESS_MODULES = {'subprocess', 'multiprocessing', 'pty', 'asyncio', 'concurrent'}
PROCESS_CALLS = {'system', 'popen', 'fork', 'forkpty', 'startfile', 'posix_spawn', 'posix_spawnp'}
MAX_MODULE_LINES = 600
MAX_FUNCTION_LINES = 60


def modules():
    """{dotted module name: path} for every .py file of the package."""
    found = {}
    for path in sorted(ROOT.rglob('*.py')):
        parts = path.relative_to(SCRIPTS).with_suffix('').parts
        if parts[-1] == '__init__':
            parts = parts[:-1]
        found['.'.join(parts)] = path
    return found


def is_package(path):
    return path.name == '__init__.py'


def imports(name, path, known):
    """(line, imported module) for every import in a module, relative imports resolved; a name imported from a
    package resolves to its submodule when one exists."""
    package = name if is_package(path) else name.rsplit('.', 1)[0]
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split('.')
                base = base[:len(base) - node.level + 1]
                target = '.'.join(base + ([node.module] if node.module else []))
            else:
                target = node.module
            for alias in node.names:
                submodule = target + '.' + alias.name
                yield node.lineno, submodule if submodule in known else target


def layer(module):
    parts = module.split('.')
    if parts[0] != PACKAGE:
        return None
    return parts[1] if len(parts) > 1 and parts[1] in LAYERS else 'root'


def internal_graph():
    known = modules()
    graph = {}
    for name, path in known.items():
        graph[name] = {(line, target) for line, target in imports(name, path, known) if target in known}
    return known, graph


def test_every_module_belongs_to_a_layer():
    for name in modules():
        assert layer(name) in LAYERS or name == PACKAGE, name + ' is outside core/port/adapters/cli'


def test_imports_point_inward():
    known, graph = internal_graph()
    violations = []
    for name, edges in graph.items():
        source = layer(name)
        for line, target in sorted(edges):
            target_layer = layer(target)
            if target_layer == 'root' or source == 'root':
                continue
            if target_layer not in ALLOWED[source]:
                violations.append('{}:{} ({}) imports {} ({})'.format(
                    known[name].relative_to(SCRIPTS), line, source, target, target_layer))
    assert not violations, '\n'.join(violations)


def test_root_package_holds_only_the_version():
    tree = ast.parse((ROOT / '__init__.py').read_text(encoding='utf-8'))
    assert not [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]


def test_core_starts_no_process():
    violations = []
    for name, path in modules().items():
        if layer(name) != 'core':
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or '']
                for module in names:
                    if module.split('.')[0] in PROCESS_MODULES:
                        violations.append('{}:{} imports {}'.format(path.name, node.lineno, module))
                if isinstance(node, ast.ImportFrom) and node.module == 'os':
                    for alias in node.names:
                        if alias.name in PROCESS_CALLS or alias.name.startswith(('exec', 'spawn')):
                            violations.append('{}:{} imports os.{}'.format(path.name, node.lineno, alias.name))
            elif (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == 'os'
                  and (node.attr in PROCESS_CALLS or node.attr.startswith(('exec', 'spawn')))):
                violations.append('{}:{} uses os.{}'.format(path.name, node.lineno, node.attr))
    assert not violations, '\n'.join(violations)


def test_no_import_cycles():
    _, graph = internal_graph()
    edges = {name: sorted({target for _, target in targets}) for name, targets in graph.items()}
    done, cycles = set(), []

    def visit(name, stack):
        if name in stack:
            cycles.append(' -> '.join(stack[stack.index(name):] + [name]))
            return
        if name in done:
            return
        for target in edges.get(name, ()):
            visit(target, stack + [name])
        done.add(name)

    for name in sorted(edges):
        visit(name, [])
    assert not cycles, '\n'.join(cycles)


def test_modules_and_functions_stay_small():
    too_long = []
    for path in [SCRIPTS / 'xq.py'] + sorted(ROOT.rglob('*.py')):
        text = path.read_text(encoding='utf-8')
        lines = len(text.splitlines())
        if lines > MAX_MODULE_LINES:
            too_long.append('{}: {} lines (max {})'.format(path.relative_to(SCRIPTS), lines, MAX_MODULE_LINES))
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                length = node.end_lineno - node.lineno + 1
                if length > MAX_FUNCTION_LINES:
                    too_long.append('{}:{} {}(): {} lines (max {})'.format(
                        path.relative_to(SCRIPTS), node.lineno, node.name, length, MAX_FUNCTION_LINES))
    assert not too_long, '\n'.join(too_long)


def test_entry_point_is_thin():
    tree = ast.parse((SCRIPTS / 'xq.py').read_text(encoding='utf-8'))
    assert not [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))]
    assert {node.module for node in tree.body if isinstance(node, ast.ImportFrom)} == {PACKAGE + '.cli.main'}


def test_every_executor_has_a_registered_adapter():
    xq = load_xq()
    assert tuple(xq.ADAPTERS) == xq.EXECUTORS
    for name in xq.EXECUTORS:
        adapter = xq.ADAPTERS[name]
        assert issubclass(adapter, xq.ExecutorAdapter), name
        assert adapter.name == name
