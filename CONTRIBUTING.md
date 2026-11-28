# Contributing

Bug reports, documentation fixes and new executor adapters are welcome. Open an issue first for larger changes.
Report security problems privately as described in the [security policy](SECURITY.md).

## Layout

Everything the skill needs lives in [`.claude/skills/executor-queue/`](.claude/skills/executor-queue/): `SKILL.md`,
`scripts/` (`xq.py` and its `executor_queue/` package), `references/` and `assets/`. `.agents/skills/executor-queue`
is a relative symlink to it for Codex; do not replace it with a copy. Files outside the skill directory (this guide,
the README, `tests/`, `.github/`) are for the repository only and must never be needed at runtime. `SKILL.md` stays
at 125 lines or fewer and links only inside the skill directory.

## Code layout

`scripts/xq.py` is a thin entry point for the `scripts/executor_queue/` package, in four layers:

- **CORE** (`executor_queue/core/`) holds ledger I/O and replay (`ledger.py`), the task state rules (`state.py`),
  config (`config.py`), brief lint and compose (`brief.py`), review ranges, sensitive paths and the scope and
  stale-base checks (`review.py`), imported history (`importer.py`), capacity (`capacity.py`) and repository queries
  (`repo.py`). It names no executor, starts no process and imports no outer layer.
- **PORT** (`executor_queue/port/`) holds the process runner (`runner.py`: `run`/`git`), plugin discovery
  (`discovery.py`) and the `ExecutorAdapter` interface with the `ADAPTERS` registry (`adapter.py`). It imports CORE
  only.
- **ADAPTERS** (`executor_queue/adapters/`) holds `CodexAdapter` (`codex.py`, with `codex_worktree.py` and
  `codex_poll.py`), `AgyAdapter` (`agy.py`, with `agy_sync.py`) and `CloudAdapter` (`cloud.py`, with
  `cloud_report.py` and `cloud_collect.py`); `adapters/__init__.py` registers them in `ADAPTERS`. Adding an executor
  means subclassing `ExecutorAdapter`, registering it there and adding its name to `EXECUTORS`.
- **CLI** (`executor_queue/cli/`) holds the argparse parser (`parser.py`), the entry point (`main.py`) and the
  `cmd_*` handlers by area in `commands/`: `setup.py` (doctor, preflight, init, import, assign, brief-lint, compose),
  `record.py` (finish, verify, merge, ack, close), `read.py` (status, events, hook, pause) and `executors.py` (poll, agy-sync,
  cloud-envelope, cloud-collect). Nothing imports the CLI.

CORE reaches the runner and executor-specific behaviour only through `core/seams.py` (`seams.git`,
`seams.adapter_for`, `seams.ADAPTERS`), which `cli/main.py` installs. `tests/test_architecture.py` checks the
import direction, import cycles and module and function sizes.

## Add a new executor connector

Executors plug in through the adapter registry: the interface is
[`executor_queue/port/adapter.py`](.claude/skills/executor-queue/scripts/executor_queue/port/adapter.py), each adapter
is a module in [`executor_queue/adapters/`](.claude/skills/executor-queue/scripts/executor_queue/adapters/) and
`adapters/__init__.py` registers it; CORE and the CLI (`executor_queue/cli/`) stay unchanged. Use `AgyAdapter` as the reference for reading
local artifacts, `CodexAdapter` for querying a job by ID, and `CloudAdapter` for importing reports published through
Git.

1. **Define the workflow.** Choose a stable executor ID and decide how a queue task matches the provider's job or
   result, which files and destinations the user approves, where outputs are stored, and what evidence shows that a
   job finished. List private output directories in the adapter's `private_state` so `init` excludes them.
2. **Write the adapter.** Subclass `ExecutorAdapter`, set its capability attributes (`worktree_delivery`, `polled`,
   `plugin`, `queued_hint`, ...) and override only the operations it needs: the interface docstring lists every one
   (`poll`/`poll_all`, `check_wait`, `doctor_checks`, `live_process`, `verify_delivery`, `scope_paths`,
   `close_guard`, ...). Register it in `ADAPTERS` (`executor_queue/adapters/__init__.py`) and add the ID to `EXECUTORS` (`executor_queue/core/util.py`); for
   a local capacity limit add it to `LOCAL_EXECUTOR_LIMITS`, and for Git integration to `INTEGRATING_EXECUTORS` (same
   file). Do not add `executor == ...` branches
   outside the adapter: `tests/test_architecture.py` checks every executor has a registered adapter.
3. **Collect results explicitly.** `poll --id` and `poll --executor` refresh only the selected tasks through the
   adapter. Use `snapshot()` and `append_checked()` (`executor_queue/core/ledger.py`) to guard against a concurrent reassignment or finish, record
   unknown or unavailable results as observations, and write `finish` only with confirmed terminal evidence. Keep
   `status` ledger-only and leave the verdict to an independent `verify`. [Status and refresh contract](.claude/skills/executor-queue/references/job-status.md)
4. **Document and test it.** Update `SKILL.md` with setup, allowed inputs, dispatch and result review steps, and link
   the setup guide from the executor table in the README. Test offline with fake provider responses or local artifacts:
   checks, scoped refresh, terminal evidence, missing or malformed results, and `status --executor <id> --json`.
   [Skill instructions](.claude/skills/executor-queue/SKILL.md) · [CLI test examples](tests/test_xq.py)

## Check a change

```sh
python3 -m pytest -q tests          # offline; needs pytest and Git 2.31+
git diff --check
npx skills add "$PWD" --list        # the Skills CLI still discovers executor-queue
```

Add an offline test for every behaviour change and a [changelog](CHANGELOG.md) entry under **Unreleased**.
Releases follow [RELEASING.md](RELEASING.md).
