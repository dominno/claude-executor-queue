# Configuration

xq reads optional project policy from `.agents/config.json` in the primary checkout. A missing file means
"all defaults". Every key is optional. An invalid file makes every command stop with
`xq: invalid .agents/config.json (...)`, and that message names the valid shape of each key.
The file is plain JSON, so it cannot hold comments. `xq.py pause` rewrites it.

The config is policy you can track in Git: review it before you commit it. The ledger, lock, review exports and
config temp files stay local (`xq.py init` excludes them).

Two examples are included:
[`assets/config.json`](../assets/config.json) is a minimal opt-in, and
[`assets/config.full.json`](../assets/config.full.json) lists every key at its default
(with sample `main`, `context_files` and `import_map` values; the checks that are off unless set are left out). Merge either one with any existing config.

## Keys

| Key | Type and default | What it does | More |
| --- | --- | --- | --- |
| `keep_busy` | list of `codex`, `agy`, `cloud`; default `[]` | The Stop hook flags a listed executor that has no running task. Without this key, it flags only executors with `assign --queued` backlog, and agy only when listed here. | [Stop hook](../assets/README.md) |
| `paused` | list of `codex`, `agy`, `cloud`; default `[]` | The Stop hook skips idle and stale checks for a listed executor. `xq.py pause --executor X` adds X and `--resume` removes it, so you rarely edit this key by hand. | [Status](job-status.md) |
| `limits` | object with `codex` and `agy` keys, each a nonnegative integer; default `{"codex": 2, "agy": 1}` | The maximum number of running tasks per local executor, used by `preflight` and the Stop hook. `0` disables launches. | [Launch preflight](codex.md#launch-preflight) |
| `min_free_gb` | finite number of 0 or more; default `5` | `preflight` refuses when the repository volume or the temp volume has less free space (GiB). | [Launch preflight](codex.md#launch-preflight) |
| `max_load_per_cpu` | finite number above 0; default `1.0` | `preflight` refuses when the 1-minute load divided by the CPU count is above this value. Windows skips this check. | [Launch preflight](codex.md#launch-preflight) |
| `main` | branch name; default: `origin/HEAD` | The default branch for `merge`, `cloud-envelope`, `cloud-collect`, the stale-base check and the review range. Set it when `origin/HEAD` is unset; `--main` overrides it per command. | [Cloud](cloud.md) |
| `context_files` | list of repo-relative regular files; default: none | `xq.py compose` writes these files and then the brief into one launch prompt. Absolute paths, `..`, symlinks and missing files are refused. | [Context preamble](codex.md#context-preamble) |
| `brief_lint` | object, see below; default: absent | Turns on brief checking at `assign`. Without this key, `assign` does not lint. The `brief-lint` command always lints, using the defaults. | [Brief lint](codex.md#brief-lint) |
| `scope_check` | `off`, `warn` or `enforce`; default `warn` | Compares a Codex delivery with the brief's Acceptance `Files:`. `enforce` refuses `verify --verdict pass` unless you pass `--accept-scope "<reason>"`. | [Declared scope](codex.md#declared-scope) |
| `stale_base` | `off`, `warn` or `refuse`; default `warn` | Warns when a Codex worktree is behind the default branch, at `assign --worktree` and `preflight --worktree`. `refuse` makes `assign` fail unless `--force`, and makes `preflight` exit 1. | [Stale base](codex.md#stale-base) |
| `ledger` | repo-relative `*.jsonl` path; default `.agents/ledger.jsonl` | Where xq keeps its append-only ledger; the lock sits next to it. A ledger outside `.agents/` is not excluded from Git, so commit it as project history. Absolute paths, `..`, `.git/` and symlinks are refused. | [Adopting](adopting.md) |
| `import_map` | object; default: absent | The default `--map` for `xq.py import`. It maps xq fields (`id` is required; `executor`, `status`, `brief`, `ts`, `event`, `verdict`, `by`, `commit`, `model` and `effort` are optional) to source keys. `status_values` maps source values to `done`, `failed`, `running`, `queued` or `closed`; `event_values` maps them to `assign`, `finish`, `verify`, `merge` or `close`. | [Adopting](adopting.md#import-the-old-ledger-as-read-only-history) |
| `max_swap_gb` | finite number of 0 or more, in GiB (1024³ bytes); default: absent (no check) | `preflight` (and the hook's idle check) refuses when the swap space in use is above this value. macOS reads `used` from `sysctl -n vm.swapusage`; Linux reads `SwapTotal - SwapFree` from `/proc/meminfo`. Other platforms print `SKIP memory check`. | [Launch preflight](codex.md#memory-admission) |
| `min_free_mem_gb` | finite number of 0 or more, in GiB; default: absent (no check) | `preflight` refuses when available memory is below this value. macOS: (free + inactive + speculative pages) × page size from `vm_stat`; Linux: `MemAvailable` from `/proc/meminfo`. A failed probe on macOS or Linux refuses. | [Launch preflight](codex.md#memory-admission) |
| `idle_block_every` | duration such as `30m` or `1h` (`s`, `m`, `h`; a bare number is seconds); default: absent | The Stop hook blocks on idle executors at most once per interval; in between it prints the idle line and exits 0. Unverified, stale and `extra_checks` problems still block every time. The last idle block is stamped in `.agents/hook-idle.stamp` (private; `init` excludes it). | [Stop hook](../assets/README.md#stop-hook-options) |
| `extra_checks` | list of command strings; default: absent | The Stop hook runs each command from the primary checkout (no shell, split like a POSIX shell line) and turns each non-empty stdout line (up to 20, 200 characters each) into a problem that blocks. A command that exits nonzero, is missing or times out prints `WARN extra check ...` and adds nothing. | [Stop hook](../assets/README.md#stop-hook-options) |
| `extra_checks_timeout` | duration; default `20s` | Time limit for each `extra_checks` command. Keep the total below your Stop hook's own timeout. | [Stop hook](../assets/README.md#stop-hook-options) |

`extra_checks` commands come from the config file, which you can track in Git: anyone who can change that file can
run commands in every session that runs the Stop hook. Review config changes like code, and keep commands read-only.

### `brief_lint`

```json
{"brief_lint": {"mode": "warn", "shape_mode": "warn", "restricted": [], "handoff_marker": "lead runs", "fields": {},
                "result_max_words": 45, "max_steps": 4, "notes_require_file": false}}
```

- `mode`: `off`, `warn` or `enforce`. With `warn`, `assign` prints the findings to stderr. With `enforce`,
  `assign` refuses a brief that has errors unless you pass `--force`. It governs every rule except the shape rules.
- `shape_mode`: `off`, `warn` or `enforce`; default: the value of `mode`. It governs only the shape rules: exactly
  one `Result:` line and one final `## Acceptance` with `Files:`, `Checks:` and `Notes:`. `{"mode": "warn",
  "shape_mode": "enforce"}` refuses a multi-result brief at `assign` while the other rules only warn, and
  `brief-lint` then prints a line telling you to split the brief and exits 1.
- `restricted`: words or phrases, such as `"network"` or `"git commit"`. A line that names one must also
  contain `handoff_marker`.
- `handoff_marker`: the text that marks a step the lead (not the executor) runs. The default is `lead runs`.
- `fields`: header fields the brief must have, with their allowed values, for example
  `{"Domain": ["api", "ui"]}`.
- `result_max_words` and `max_steps`: positive integers. Going over either one is a warning that the
  task should be split.
- `notes_require_file`: `true` requires `Notes:` to name a `.md` or `.txt` file.

## Environment

- `XQ_ROOT` overrides the primary checkout that xq uses. It is needed for bare or separate-git-dir layouts.
- `CLAUDE_CONFIG_DIR` selects the Claude Code profile that `doctor` reads plugins from.
