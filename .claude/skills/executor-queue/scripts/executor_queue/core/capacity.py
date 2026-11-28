"""Capacity admission: host resource readings and per-executor slot checks (CORE)."""

import os
import re
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


GIB = 1024 ** 3
# Optional memory admission keys (GiB); absent means no memory check.
MEMORY_KEYS = frozenset(('max_swap_gb', 'min_free_mem_gb'))


def parse_swapusage(text):
    """GiB of swap in use from macOS `sysctl -n vm.swapusage` ("total = 2048.00M  used = 1024.50M  free = ...")."""
    match = re.search(r'used\s*=\s*([0-9]+(?:\.[0-9]+)?)([KMGT])', text)
    if not match:
        raise ValueError('unrecognized vm.swapusage output')
    return float(match.group(1)) * 1024 ** 'KMGT'.index(match.group(2)) * 1024 / GIB


def parse_vm_stat(text):
    """GiB of memory available without swapping from macOS `vm_stat`: (free + inactive + speculative) pages."""
    size = re.search(r'page size of ([0-9]+) bytes', text)
    pages = dict(re.findall(r'^Pages (free|inactive|speculative):\s+([0-9]+)\.?\s*$', text, re.M))
    if not size or not {'free', 'inactive'} <= set(pages):
        raise ValueError('unrecognized vm_stat output')
    return sum(int(value) for value in pages.values()) * int(size.group(1)) / GIB


def parse_meminfo(text):
    """{'available_gb', 'swap_used_gb'} from Linux /proc/meminfo (values in kB, meaning KiB)."""
    values = {key: int(value) for key, value in re.findall(r'^(\w+):\s+([0-9]+) kB\s*$', text, re.M)}
    if not {'MemAvailable', 'SwapTotal', 'SwapFree'} <= set(values):
        raise ValueError('/proc/meminfo has no MemAvailable/SwapTotal/SwapFree')
    return dict(available_gb=values['MemAvailable'] * 1024 / GIB,
                swap_used_gb=(values['SwapTotal'] - values['SwapFree']) * 1024 / GIB)


def memory_admission(data, reasons, details):
    """Refuse when swap in use is above max_swap_gb or available memory is below min_free_mem_gb (GiB)."""
    try:
        memory = seams.memory_readings()
    except (OSError, ValueError) as exc:
        reasons.append('memory readings unavailable: ' + str(exc))
        return
    if memory is None:
        details.append('SKIP memory check: no memory probe for this platform (macOS and Linux only)')
        return
    for key, reading, label, refused in (
            ('max_swap_gb', 'swap_used_gb', 'swap used {:.2f} GiB (max {:.2f})', lambda value, limit: value > limit),
            ('min_free_mem_gb', 'available_gb', 'memory available {:.2f} GiB (min {:.2f})',
             lambda value, limit: value < limit)):
        if key in data:
            details.append(label.format(memory[reading], data[key]))
            if refused(memory[reading], data[key]):
                reasons.append(details[-1])


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
    if MEMORY_KEYS & set(data):
        memory_admission(data, reasons, details)
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
