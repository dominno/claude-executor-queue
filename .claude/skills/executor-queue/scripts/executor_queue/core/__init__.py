"""CORE: ledger I/O, replay, the task state machine and the rules shared by commands (capacity, review
ranges, sensitive paths, declared scope, stale bases, brief lint).

CORE imports nothing from port, adapters or cli, names no executor and starts no process: Git and other tools
run through the PORT runner, executor-specific behaviour through the adapter registry, both reached through
core.seams.
"""
