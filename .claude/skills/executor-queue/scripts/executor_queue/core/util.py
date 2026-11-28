"""Errors, shared constants and text helpers every layer uses (CORE: no process, no executor)."""

import json
import os
import re


WINDOWS = os.name == 'nt'


EXECUTORS = ('codex', 'agy', 'cloud')
LOCAL_EXECUTOR_LIMITS = {'codex': 2, 'agy': 1}
INTEGRATING_EXECUTORS = ('codex', 'cloud')
TASK_STATES = ('queued', 'running', 'done', 'failed', 'blocked')
OBSERVATION_STATES = TASK_STATES + ('cancelled', 'unknown', 'unavailable', 'orphaned')
STATUS_DIR = '.agents/cloud-status'
ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$')
SHA_RE = re.compile(r'^[0-9a-f]{7,64}$')
OPTION_RE = re.compile(r'^[A-Za-z0-9._:-]{1,64}$')


DURATION_RE = re.compile(r'([0-9]+(?:\.[0-9]+)?)([smh]?)')


def duration_seconds(value):
    """Seconds in a duration such as 30s, 10m or 1h (a bare number is seconds); None when invalid or not positive."""
    match = DURATION_RE.fullmatch(value.strip())
    if not match:
        return None
    seconds = float(match.group(1)) * {'': 1, 's': 1, 'm': 60, 'h': 3600}[match.group(2)]
    return seconds if 0 < seconds < float('inf') else None


def fail(message):
    raise SystemExit('xq: ' + message)


class LedgerConflict(SystemExit):
    """A query's ledger inputs changed before its rows were written (poll --wait re-reads and retries)."""


def display_name(name):
    """An untrusted name (path, ref, job id) with control bytes and quotes escaped for one output line."""
    return json.dumps(name, ensure_ascii=True)[1:-1]


def short(sha):
    return (sha or '')[:12] or '-'


def short_reason(text, limit=120):
    """One sanitized line for diagnostics built from external tool output."""
    line = next((part.strip() for part in str(text).splitlines() if part.strip()), '')
    line = ''.join(ch for ch in line if ch.isprintable())
    return line[:limit]


def listed_paths(paths, limit=10):
    # Executor-created names are untrusted: escape control bytes/quotes and bound each one.
    shown = ', '.join(display_name(path)[:120] for path in paths[:limit])
    return shown + (' (+{} more)'.format(len(paths) - limit) if len(paths) > limit else '')
