#!/usr/bin/env python3
"""Offline executor ledger (stdlib, Python 3.8+): the xq command-line entry point.

The code lives in the executor_queue package next to this file (core, port, adapters, cli).
"""
import os
import sys

# Installs link the skill directory, so resolve this file before adding its directory to the path.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
sys.dont_write_bytecode = True  # never leave __pycache__ inside an installed skill directory

from executor_queue.cli.main import main  # noqa: E402

if __name__ == '__main__':
    sys.exit(main())
