#!/bin/sh
# Offline installer; Python 3.8+ is also required by the installed helper.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for python_cmd in python3 python; do
    if command -v "$python_cmd" >/dev/null 2>&1 &&
       "$python_cmd" -c 'import sys; sys.exit(sys.version_info < (3, 8))'; then
        exec "$python_cmd" "$root/scripts/install.py" "$@"
    fi
done
echo 'install: Python 3.8+ is required' >&2
exit 1
