#!/bin/sh
# Offline installer; Python 3.8+ is also required by the installed helper.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if command -v python3 >/dev/null 2>&1; then
    exec python3 "$root/scripts/install.py" "$@"
elif command -v python >/dev/null 2>&1; then
    exec python "$root/scripts/install.py" "$@"
fi
echo 'install: Python 3.8+ is required' >&2
exit 1
