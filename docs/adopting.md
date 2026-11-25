# Adopting xq in a project with an existing ledger

A project that already keeps an append-only JSONL task log can hand its history to xq without rewriting it. Two
independent options, both configured in `.agents/config.json`:

## Keep the ledger where the project wants it

```json
{"ledger": "history/xq-ledger.jsonl"}
```

`ledger` is a repo-relative `*.jsonl` path in the primary checkout (default `.agents/ledger.jsonl`). Absolute paths,
`..`, paths inside `.git/` and symlinked files or
directories are refused. The lock file lives next to it (`history/xq-ledger.lock`).

- Under `.agents/`, `xq.py init` adds the ledger and its lock to `.git/info/exclude`, as for the default.
- Outside `.agents/`, `init` excludes only the lock and prints `ledger <path> is outside .agents/: not excluded from
  Git (commit it as project history)`. Commit it like any other file; xq only appends to it.
- `xq.py doctor` prints `ledger: <path> (default)` or `ledger: <path> (config "ledger")`.

Changing `ledger` later does not move existing events: move the file yourself while no xq command runs.

## Import the old ledger as read-only history

```bash
xq.py import --from old/tasks.jsonl --map '{"id": "task_id", "executor": "agent", "status": "state",
  "brief": "brief_path", "ts": "timestamp", "status_values": {"complete": "done", "error": "failed",
  "active": "running", "archived": "closed"}}' --dry-run
```

- `--map` maps xq fields to source keys. `id` is required (it must be a valid xq task id); `executor`, `status`,
  `brief` and `ts` (a zoned ISO timestamp) are optional. `status_values` maps source values to
  `done|failed|running|queued|closed`; an unmapped value outside that set makes the line malformed. Put the same
  object under `"import_map"` in the config to omit `--map`.
- Each source line becomes one `import` event carrying `imported: {source, line, sha256}` (sha256 of the raw line;
  a leading UTF-8 BOM is tolerated).
  Several lines for one id replay in order; `closed` closes the task and keeps its last status.
- Re-running is idempotent: lines whose sha256 is already in the ledger are counted as `already imported`.
- Malformed lines, and ids that collide with a task xq recorded itself, are reported as
  `xq: <source> line N: <reason>; skipped`; the good lines are still imported and the exit code is 1.
  `--dry-run` prints the same report and writes nothing.

Imported tasks are history. `status` marks them `IMPORTED` (`status --json`: `"imported": true`,
`"source_executor"`, and `executor: null`); they never occupy an executor slot, never count as stale or open, and
never make the Stop hook block. `poll --id` (with or without `--wait`), `finish`, `verify`, `merge`, `ack` and
`close` refuse them, and `assign` refuses their id and `--follows <imported id>`: record new work under a fresh id.
