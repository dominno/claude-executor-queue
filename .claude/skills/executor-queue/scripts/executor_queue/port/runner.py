"""The process runner (PORT): the one place every external process starts, Git helpers for executor worktrees
and the live executor process count."""

import json
import os
import re
import shlex
import subprocess

from ..core import util
from ..core.util import display_name, fail, LOCAL_EXECUTOR_LIMITS, short_reason
from .adapter import ADAPTERS


GIT_NETWORK_TIMEOUT = 300


# --- PORT: process runner -----------------------------------------------------------------------------------------

# Every external process (git, the Codex companion, agy-bridge, ps/PowerShell) starts through run() or,
# for a bounded streaming read, open_process(): the one place tests stub to fake a process.

def process_env(argv, env):
    if argv and argv[0] == 'git':
        # Read-only status/diff in a worktree must not take the index lock the lead may hold.
        return dict(os.environ if env is None else env, GIT_OPTIONAL_LOCKS='0')
    return env


def run(argv, cwd=None, timeout=30, env=None):
    """Run to completion and capture text output; a start failure or timeout is exit 1 with the error as stderr."""
    env = process_env(argv, env)
    try:
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                              encoding='utf-8', errors='replace', timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(argv, 1, '', str(exc))


def open_process(argv, cwd=None, env=None):
    """Start a process whose binary stdout the caller streams (stderr discarded); raises OSError."""
    return subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            env=process_env(argv, env))


def git(*args, check=True, cwd=None):
    network = args and args[0] in ('fetch', 'ls-remote')
    r = run(['git', *args], cwd=cwd, timeout=GIT_NETWORK_TIMEOUT if network else 30,
            env=dict(os.environ, GIT_TERMINAL_PROMPT='0'))
    if check and r.returncode:
        fail('git {} failed: {}'.format(' '.join(args), r.stderr.strip()))
    return r.stdout


# Executor worktrees are untrusted: their config.worktree (extensions.worktreeConfig) may name an fsmonitor
# hook or hooksPath that a plain `git status` would run, or relax stat checks so an edited file looks clean.
# Command-line -c overrides every config file.
WORKTREE_GIT_CONFIG = ('-c', 'core.fsmonitor=false', '-c', 'core.untrackedCache=false',
                       '-c', 'core.trustctime=true', '-c', 'core.checkStat=default', '-c', 'core.ignoreStat=false',
                       '-c', 'core.splitIndex=false') + (
    () if util.WINDOWS else ('-c', 'core.hooksPath=' + os.devnull))


def worktree_env(index=None):
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
    env.pop('GIT_INDEX_FILE', None)
    if index:
        env['GIT_INDEX_FILE'] = index
    return env


def worktree_git(checkout, *args, check=True, index=None, config=(), timeout=30):
    """git with cwd=checkout (an executor worktree) and fsmonitor/untracked-cache/hooks disabled.

    index names a scratch index file (GIT_INDEX_FILE); config adds -c pairs after WORKTREE_GIT_CONFIG.
    """
    r = run(['git', *WORKTREE_GIT_CONFIG, *config, *args], cwd=checkout, env=worktree_env(index), timeout=timeout)
    if check and r.returncode:
        fail('git {} failed in {}: {}'.format(' '.join(args), display_name(str(checkout))[:200],
                                              r.stderr.strip()))
    return r.stdout


def process_rows(with_pid=False):
    """Return (executable name, command line) pairs, or (pid, name, command line) with_pid; never
    inspect environments."""
    if util.WINDOWS:
        result = run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                      'Get-CimInstance Win32_Process | Select-Object {}Name,CommandLine | ConvertTo-Json '
                      '-Compress'.format('ProcessId,' if with_pid else '')])
        if result.returncode:
            raise OSError('cannot read live processes: ' + result.stderr.strip())
        rows = json.loads(result.stdout or '[]')
        if isinstance(rows, dict):
            rows = [rows]
        if with_pid:
            return [(row.get('ProcessId'), row['Name'], row.get('CommandLine') or '') for row in rows]
        return [(row['Name'], row.get('CommandLine') or '') for row in rows]
    # ucomm is the accounting basename on BSD/macOS and procps; comm can be a
    # truncated path on macOS. argv[0] from args is preferred when it matches.
    columns = ('pid=,' if with_pid else '') + 'stat=,ucomm=,args='
    result = run(['ps', '-axww', '-o', columns], env=dict(os.environ, LC_ALL='C'))
    if result.returncode:
        raise OSError('cannot read live processes: ' + result.stderr.strip())
    rows = []
    for line in result.stdout.splitlines():
        if with_pid:
            pid, _, line = line.strip().partition(' ')
        fields = line.strip().split(None, 2)
        # Zombie (Z), dead (X) and exiting (E, macOS) processes hold no executor slot.
        if len(fields) == 3 and not any(flag in fields[0] for flag in 'ZXE'):
            rows.append((int(pid) if pid.isdigit() else None, fields[1], fields[2]) if with_pid
                        else (fields[1], fields[2]))
    return rows


# Executable names a live process may run under (each adapter's live_process() classifies them).
PROCESS_NAMES = ('codex', 'agy', 'node', 'nodejs')


def process_name(value):
    name = value.replace('\\', '/').rsplit('/', 1)[-1].lower()
    return name[:-4] if name.endswith('.exe') else name


def interesting_process(name):
    return name in PROCESS_NAMES or re.fullmatch(r'python(?:[0-9]+(?:\.[0-9]+)*)?|py', name) is not None


def process_sample(pid, command):
    """(pid, first three argv words) for a refusal line; argv is untrusted and may hold secrets."""
    words = []
    for word in command.split()[:3]:
        if re.search(r'(?i)(token|secret|passw|api[_-]?key|auth)', word) or '=' in word:
            word = '<redacted>'
        words.append(short_reason(word, 60))
    return pid, ' '.join(words)


def live_process_counts(samples=None):
    """Conservative global count, including wrappers; see references/codex.md.

    samples (dict), when given, receives per executor up to 5 (pid, sanitized argv words) of counted processes.
    """
    counts = dict.fromkeys(LOCAL_EXECUTOR_LIMITS, 0)
    rows = process_rows(with_pid=True) if samples is not None else [(None,) + row for row in process_rows()]
    for pid, comm, command in rows:
        before = dict(counts)
        # ps command lines aren't shell programs. Tokenize only to recognize
        # exact executable/script names, never execute or substring-match them.
        try:
            tokens = shlex.split(command, posix=not util.WINDOWS)
        except ValueError:
            tokens = command.split()
        tokens = [token.strip('"').replace('\\', '/').rsplit('/', 1)[-1] for token in tokens]
        # Prefer argv[0]; fall back to the accounting name (Windows Name, POSIX ucomm).
        name = next((n for n in (process_name(tokens[0]) if tokens else '', process_name(comm))
                     if interesting_process(n)), '')
        # Each name belongs to at most one executor, so at most one adapter counts a process.
        executor = next((ex for ex in counts if ADAPTERS[ex].live_process(name, tokens)), None)
        if executor is not None:
            counts[executor] += 1
        if samples is not None:
            for executor in counts:
                if counts[executor] > before[executor] and len(samples.setdefault(executor, [])) < 5:
                    samples[executor].append(process_sample(pid, command))
    return counts
