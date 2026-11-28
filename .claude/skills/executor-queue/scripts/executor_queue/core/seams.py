"""The late-bound seams CORE calls without importing PORT or ADAPTERS (dependency inversion).

CORE names what it needs here; the outer layer installs the modules that provide it when it is imported, and
CORE looks each name up on them at call time (seams.git(...)), so a test stub patched onto the providing module
is the one CORE calls too. CORE itself starts no process and names no executor.

  runner    run, git, live_process_counts, memory_readings (port.runner: the process runner)
  registry  adapter_for, heartbeat_fields, ADAPTERS       (port.adapter: the executor adapter registry)
  adapters  private_paths                                 (the adapters package: paths the adapters keep private)
"""

PROVIDED = {
    'runner': ('run', 'git', 'live_process_counts', 'memory_readings'),
    'registry': ('adapter_for', 'heartbeat_fields', 'ADAPTERS'),
    'adapters': ('private_paths',),
}
_PROVIDER = {name: role for role, names in PROVIDED.items() for name in names}
_installed = {}


def install(**modules):
    """Register the module that provides each role (runner=..., registry=..., adapters=...)."""
    unknown = set(modules) - set(PROVIDED)
    if unknown:
        raise ValueError('unknown seam role: ' + ', '.join(sorted(unknown)))
    _installed.update(modules)


def __getattr__(name):
    role = _PROVIDER.get(name)
    if role is None:
        raise AttributeError(name)
    if role not in _installed:
        raise RuntimeError('executor_queue: the {} seam is not installed (import the CLI or PORT first)'.format(role))
    return getattr(_installed[role], name)
