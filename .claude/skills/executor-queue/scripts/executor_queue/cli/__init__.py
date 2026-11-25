# ==================================================================================================================
# CLI
# Argument parsing and the cmd_* handlers: each applies its command rules through CORE helpers and
# adapter_for(), appends ledger events and prints. main.main() is the only entry point.
# ==================================================================================================================
"""CLI: the argparse parser (parser), the command handlers grouped by area (commands/) and the entry point
(main.main, which scripts/xq.py calls). The CLI imports CORE, PORT and ADAPTERS; nothing imports the CLI.
"""
