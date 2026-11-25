"""Repository-relative paths and symlink-safe file opening for xq's own state (CORE)."""

import os
from pathlib import Path
import re
import stat

from .util import fail


DEFAULT_LEDGER = '.agents/ledger.jsonl'


def repo_relative(value):
    """True for a repo-relative path: not absolute, no drive or home prefix, no ".." part."""
    return not (os.path.isabs(value) or value.startswith(('~', '\\')) or re.match(r'^[A-Za-z]:', value) or
                '..' in re.split(r'[\\/]', value))


def ledger_relative(data):
    """The configured ledger path (config "ledger", a repo-relative *.jsonl) or the default."""
    return data.get('ledger', DEFAULT_LEDGER)


def ledger_committed(data):
    """True when the configured ledger lies outside .agents/: the project's committed history, never excluded."""
    return not Path(ledger_relative(data)).as_posix().startswith('.agents/')


# xq's own private state; PRIVATE_PATHS (after the registry) adds each adapter's private_state directories.
XQ_PRIVATE_PATHS = ('/' + DEFAULT_LEDGER, '/' + DEFAULT_LEDGER[:-len('.jsonl')] + '.lock', '/.agents/review/',
                    '/.agents/config.tmp', '/.agents/config.json.tmp')


def safe_path(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        fail('unsafe symlink: ' + str(path))


def safe_open(path, flags, mode='a+b', **options):
    safe_path(path)
    error = 'expected a regular file: ' + str(path)
    try:
        file_mode = path.lstat().st_mode
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(file_mode):
            raise OSError(error)
    flags |= getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
    fd = os.open(str(path), flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(error)
    except OSError:
        os.close(fd)
        raise
    return os.fdopen(fd, mode, **options)
