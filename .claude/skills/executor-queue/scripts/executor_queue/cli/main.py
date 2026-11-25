"""The xq entry point: parse the command line and run the selected command."""

import sys

from .. import adapters
from ..core import seams
from ..port import adapter as port_adapter, runner
from .parser import build_parser

# CORE reaches the process runner and the adapter registry only through core.seams.
seams.install(runner=runner, registry=port_adapter, adapters=adapters)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    p = build_parser()
    a = p.parse_args()
    # POSIX argv uses surrogateescape for undecodable bytes. Repair before any
    # command builds events (including notes, evidence, paths and identifiers).
    for key, value in vars(a).items():
        if isinstance(value, str):
            setattr(a, key, value.encode('utf-8', errors='surrogateescape').decode('utf-8', errors='replace'))
        elif isinstance(value, list):
            setattr(a, key, [v.encode('utf-8', errors='surrogateescape').decode('utf-8', errors='replace')
                             if isinstance(v, str) else v for v in value])
    return a.fn(a)
