# Contributing

Bug reports, documentation fixes and new executor adapters are welcome. Open an issue first for larger changes.
Report security problems privately as described in the [security policy](SECURITY.md).

## Layout

Everything the skill needs lives in [`.claude/skills/executor-queue/`](.claude/skills/executor-queue/): `SKILL.md`,
`scripts/` (`xq.py` and its `executor_queue/` package), `references/` and `assets/`. `.agents/skills/executor-queue`
is a relative symlink to it for Codex; do not replace it with a copy. Files outside the skill directory (this guide,
the README, `tests/`, `.github/`) are for the repository only and must never be needed at runtime. `SKILL.md` stays
at 125 lines or fewer and links only inside the skill directory.

`scripts/xq.py` is a thin entry point; the code is the standard-library package `scripts/executor_queue/`
(Python 3.8), layered CORE, PORT, ADAPTERS and CLI. CORE lives in `executor_queue/core/` and imports nothing from
the outer layers: it reaches the process runner and the adapter registry through `core/seams.py`. PORT lives in
`executor_queue/port/` (imports CORE only), ADAPTERS in `executor_queue/adapters/` (imports PORT and CORE) and the
CLI in `executor_queue/cli/` (`parser.py`, `main.py` and `commands/` by area: `setup.py`, `record.py`, `read.py`,
`executors.py`). `tests/test_architecture.py` enforces the import direction, no import cycles, CORE starting no
process, at most 600 lines per module and 60 per function, and a registered adapter for every executor.
Executor-specific behaviour belongs in an adapter, never in `executor == ...` branches. See
[Add a new executor connector](README.md#add-a-new-executor-connector).

## Check a change

```sh
python3 -m pytest -q tests          # offline; needs pytest and Git 2.31+
git diff --check
npx skills add "$PWD" --list        # the Skills CLI still discovers executor-queue
```

Add an offline test for every behaviour change and a [changelog](CHANGELOG.md) entry under **Unreleased**.
Releases follow [RELEASING.md](RELEASING.md).
