"""Capacity admission: host resource readings and per-executor slot checks (CORE)."""

import os
import shutil
import tempfile

from . import seams, util
from .state import occupies_slot
from .util import LOCAL_EXECUTOR_LIMITS


def resource_readings(root):
    """Host probes kept separate so tests can inject readings without env overrides."""
    try:
        load = None if util.WINDOWS else os.getloadavg()[0]
    except (AttributeError, OSError) as exc:
        raise OSError('load average unavailable: ' + str(exc))
    return dict(load=load, cpus=os.cpu_count() or 1,
                repo_free_gb=shutil.disk_usage(root).free / 1024**3,
                temp_free_gb=shutil.disk_usage(tempfile.gettempdir()).free / 1024**3)


def preflight_status(root, tasks, data):
    reasons, details = [], []
    try:
        readings = resource_readings(root)
        load = readings['load']
        if load is None:
            details.append('NOTE load average unavailable; skipping load check')
        else:
            ratio = load / readings['cpus']
            details.append('load 1m {:.2f} / {} CPUs = {:.2f} (max {:.2f})'.format(
                load, readings['cpus'], ratio, data.get('max_load_per_cpu', 1.0)))
            if ratio > data.get('max_load_per_cpu', 1.0):
                reasons.append(details[-1])
        for volume in ('repo', 'temp'):
            free = readings[volume + '_free_gb']
            details.append('{} free {:.2f} GiB (min {:.2f})'.format(volume, free, data.get('min_free_gb', 5)))
            if free < data.get('min_free_gb', 5):
                reasons.append(details[-1])
    except OSError as exc:
        reasons.append('resource readings unavailable: ' + str(exc))
    samples = {}
    try:
        live = seams.live_process_counts(samples)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        live = dict.fromkeys(LOCAL_EXECUTOR_LIMITS, 0)
        reasons.append('live process readings unavailable: ' + str(exc))
    per_executor = {}
    for executor, default in LOCAL_EXECUTOR_LIMITS.items():
        running = [t for t in tasks if t.get('executor') == executor and occupies_slot(t)]
        closed = sum(bool(t.get('closed')) for t in running)
        count = max(len(running), live[executor])
        limit = data.get('limits', {}).get(executor, default)
        closed_ids = [t['id'] for t in running if t.get('closed')]
        closed_detail = (', {} closed awaiting terminal evidence; once stopped: xq.py finish --id {} --status failed'
                         .format(closed, closed_ids[0]) if closed else '')
        detail = '{} running {}/{} (ledger {}, live {}{})'.format(
            executor, count, limit, len(running), live[executor], closed_detail)
        details.append(detail)
        per_executor[executor] = reasons + ([detail] if count >= limit else [])
        if count >= limit:
            for pid, words in samples.get(executor, []):
                details.append('{} counted: pid {} {}'.format(executor, pid if pid is not None else '?', words))
            if live[executor] > len(running):
                details.append('{} live: {} process(es) not in this ledger; wait for them to exit, or check with '
                               'ps -p <pid>'.format(executor, live[executor] - len(running)))
    return per_executor, details
