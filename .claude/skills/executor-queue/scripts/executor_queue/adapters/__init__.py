# ==================================================================================================================
# ADAPTERS
# Everything specific to one executor: Codex (companion jobs in local worktrees), agy (agy-bridge
# artifacts) and Cloud (pushed session branches with status reports). Registered in ADAPTERS.
# ==================================================================================================================
"""Importing this package registers the adapters in ADAPTERS (port.adapter). Adapters use PORT and CORE, never
the CLI.
"""

from pathlib import Path

from ..core.config import config
from ..core.paths import DEFAULT_LEDGER, ledger_committed, ledger_relative, XQ_PRIVATE_PATHS
from ..core.util import EXECUTORS
from ..port.adapter import ADAPTERS
from .agy import AgyAdapter
from .cloud import CloudAdapter
from .codex import CodexAdapter


# --- ADAPTERS: registry -------------------------------------------------------------------------------------------

# Executor name -> adapter. EXECUTORS (argparse choices) lists the same names in the same order.
ADAPTERS.update(codex=CodexAdapter, agy=AgyAdapter, cloud=CloudAdapter)
assert tuple(ADAPTERS) == EXECUTORS
# Everything xq keeps out of commits and status: its own state, then each adapter's private directories.
PRIVATE_PATHS = XQ_PRIVATE_PATHS + tuple('/' + path + '/' for adapter in ADAPTERS.values()
                                         for path in adapter.private_state)


def private_paths(root):
    """PRIVATE_PATHS with the configured ledger and its lock in place of the default pair. A ledger outside
    .agents/ is the project's committed history: only its lock stays private."""
    data = config(root)
    ledger = '/' + Path(ledger_relative(data)).as_posix()
    lock = ledger[:-len('.jsonl')] + '.lock'
    default = '/' + DEFAULT_LEDGER
    rest = tuple(p for p in PRIVATE_PATHS if p not in (default, default[:-len('.jsonl')] + '.lock'))
    return (() if ledger_committed(data) else (ledger,)) + (lock,) + rest
