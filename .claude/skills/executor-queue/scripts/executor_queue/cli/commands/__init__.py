"""The cmd_* handlers by area: setup (doctor, preflight, init, import, assign, brief-lint, compose), record
(finish, verify, merge, ack, close), read (status, hook, pause) and executors (poll, agy-sync, cloud-*). Each applies
its command rules through CORE helpers and adapter_for(), appends ledger events and prints.
"""
