"""The .agents/config.json model: validation, defaults and atomic writes (CORE)."""

import json
import math
import os
from pathlib import Path
import re

from . import paths
from .brief import BRIEF_LINT_DEFAULTS, BRIEF_LINT_MODES
from .paths import DEFAULT_LEDGER, repo_relative, safe_path
from .util import duration_seconds, EXECUTORS, fail, LOCAL_EXECUTOR_LIMITS


IMPORT_FIELDS = ('id', 'executor', 'status', 'brief', 'ts', 'event', 'verdict', 'by', 'commit', 'model', 'effort')
IMPORT_STATES = ('done', 'failed', 'running', 'queued', 'closed')
IMPORT_EVENTS = ('assign', 'finish', 'verify', 'merge', 'close')
IMPORT_VALUE_MAPS = {'status_values': IMPORT_STATES, 'event_values': IMPORT_EVENTS}
IMPORT_MAP_SHAPE = ('{"id": "<source key>"} plus optional executor/status/brief/ts/event/verdict/by/commit/model/'
                    'effort source keys, "status_values": {"<source value>": "done|failed|running|queued|closed"} '
                    'and "event_values": {"<source value>": "assign|finish|verify|merge|close"}')


def check_import_map(value):
    """Raise ValueError unless value maps xq fields (id required) to source keys (see IMPORT_MAP_SHAPE)."""
    if not isinstance(value, dict) or not isinstance(value.get('id'), str):
        raise ValueError()
    for key, source in value.items():
        if key in IMPORT_VALUE_MAPS:
            if not isinstance(source, dict) or any(v not in IMPORT_VALUE_MAPS[key] for v in source.values()):
                raise ValueError()
        elif key not in IMPORT_FIELDS or not isinstance(source, str) or not source:
            raise ValueError()


# Optional checks, one config key each; unset means 'warn' (report, never refuse).
CHECK_MODES = dict(scope_check=('off', 'warn', 'enforce'), stale_base=('off', 'warn', 'refuse'))


def check_mode(data, key):
    """The configured mode of a CHECK_MODES key; generic default: warn (reports, never refuses)."""
    return data.get(key, 'warn')


def config(root):
    path = root / '.agents/config.json'
    try:
        with paths.safe_open(path, os.O_RDONLY, 'r', encoding='utf-8') as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            raise ValueError()
        for key in ('keep_busy', 'paused'):
            if key in data and (not isinstance(data[key], list) or any(x not in EXECUTORS for x in data[key])):
                raise ValueError()
        limits = data.get('limits', {})
        if not isinstance(limits, dict) or any(
                k not in LOCAL_EXECUTOR_LIMITS or type(v) is not int or v < 0
                for k, v in limits.items()):
            raise ValueError()
        for key, default in (('min_free_gb', 5), ('max_load_per_cpu', 1.0)):
            value = data.get(key, default)
            if (type(value) not in (int, float) or not math.isfinite(value) or
                    value < 0 or (key == 'max_load_per_cpu' and value == 0)):
                raise ValueError()
        if 'main' in data and (not isinstance(data['main'], str) or not data['main']):
            raise ValueError()
        check_brief_config(data)
        check_history_config(data)
        check_admission_config(data)
        if any(key in data and data[key] not in modes for key, modes in CHECK_MODES.items()):
            raise ValueError()
        return data
    except FileNotFoundError:
        return {}
    except OSError as exc:
        fail('cannot read .agents/config.json: ' + str(exc))
    except (ValueError, OverflowError, RecursionError):
        fail('invalid .agents/config.json (keep_busy/paused: executor lists; limits: {} nonnegative integers; '
             'min_free_gb: finite nonnegative number; max_load_per_cpu: finite positive number; main: branch string; '
             'context_files: list of repo-relative paths; brief_lint: {{}} with mode off|warn|enforce, restricted: '
             'strings, handoff_marker: string, fields: {{"Name": [allowed values]}}, result_max_words/max_steps: '
             'positive integers, notes_require_file: true|false); scope_check: off|warn|enforce; stale_base: '
             'off|warn|refuse; ledger: repo-relative *.jsonl path; import_map: {}; max_swap_gb/min_free_mem_gb: '
             'finite nonnegative GiB; idle_block_every: duration such as 30m or 1h; extra_checks: list of command '
             'strings; extra_checks_timeout: duration)'.format(
                 '/'.join(LOCAL_EXECUTOR_LIMITS), IMPORT_MAP_SHAPE))


def nonempty_strings(value):
    return isinstance(value, list) and all(isinstance(x, str) and x.strip() for x in value)


def check_brief_config(data):
    """Raise ValueError for an invalid brief_lint or context_files shape (config() names the valid shapes)."""
    if 'context_files' in data and not nonempty_strings(data['context_files']):
        raise ValueError()
    lint = data.get('brief_lint', {})
    if not isinstance(lint, dict) or set(lint) - set(BRIEF_LINT_DEFAULTS):
        raise ValueError()
    fields = lint.get('fields', {})
    valid = (lint.get('mode', 'warn') in BRIEF_LINT_MODES and
             nonempty_strings(lint.get('restricted', [])) and
             isinstance(lint.get('handoff_marker', 'x'), str) and lint.get('handoff_marker', 'x').strip() and
             isinstance(fields, dict) and all(nonempty_strings(v) and v for v in fields.values()) and
             all(type(lint.get(k, 1)) is int and lint.get(k, 1) > 0 for k in ('result_max_words', 'max_steps')) and
             type(lint.get('notes_require_file', False)) is bool)
    if not valid:
        raise ValueError()


def check_history_config(data):
    """Raise ValueError for an invalid ledger path or import_map (config() names the valid shapes)."""
    ledger = data.get('ledger', DEFAULT_LEDGER)
    if (not isinstance(ledger, str) or not repo_relative(ledger) or not ledger.endswith('.jsonl') or
            Path(ledger).name == '.jsonl' or '.git' in (part.casefold() for part in re.split(r'[\\/]', ledger))):
        raise ValueError()
    if 'import_map' in data:
        check_import_map(data['import_map'])


def finite_nonnegative(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def check_admission_config(data):
    """Raise ValueError for invalid memory admission or Stop hook keys (config() names the valid shapes)."""
    if any(key in data and not finite_nonnegative(data[key]) for key in ('max_swap_gb', 'min_free_mem_gb')):
        raise ValueError()
    for key in ('idle_block_every', 'extra_checks_timeout'):
        if key in data and not (isinstance(data[key], str) and duration_seconds(data[key])):
            raise ValueError()
    if 'extra_checks' in data and not nonempty_strings(data['extra_checks']):
        raise ValueError()


def brief_lint_settings(data, at_assign=False):
    """brief_lint config merged over generic defaults. assign lints only when the project configures brief_lint,
    so an unconfigured repository keeps its exact assign output; the brief-lint command always lints."""
    configured = data.get('brief_lint')
    settings = dict(BRIEF_LINT_DEFAULTS, **(configured or {}))
    if at_assign and configured is None:
        settings['mode'] = 'off'
    return settings


def write_config(root, data):
    path = root / '.agents/config.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    safe_path(path)
    safe_path(temp)
    safe_path(path.with_name('config.json.tmp'))
    try:
        stream = paths.safe_open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 'w')
    except FileExistsError:
        fail('config temp already exists; inspect it before retrying')
    try:
        with stream as fh:
            fh.write(json.dumps(data, indent=2) + '\n')
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(temp), str(path))
    finally:
        if temp.exists() and not temp.is_symlink():
            temp.unlink()
