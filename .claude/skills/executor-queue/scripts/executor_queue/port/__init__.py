# ==================================================================================================================
# PORT
# The seams CORE depends on: one process runner (run/git/worktree_git/process_rows) that tests stub
# in one place, plugin discovery, and the executor adapter interface with its registry lookup.
# ==================================================================================================================
"""PORT modules: runner (the process runner), discovery (companion and agy-bridge lookup) and adapter
(ExecutorAdapter and the ADAPTERS registry). PORT imports CORE only; the adapters package fills the registry.
"""
