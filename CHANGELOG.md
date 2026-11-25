# Changelog

## Unreleased

### Fixed: docs
- agy steps and the `doctor --need agy` hint name `agy-bridge dry-run`/`doctor` (`/agy-bridge ...` in Claude Code)
  instead of the removed `/agy:dry-run` and `/agy:doctor` plugin commands.

### Changed: repository layout
- Everything the skill uses now lives in one directory, `.claude/skills/executor-queue/` (`SKILL.md`, `README.md`,
  `LICENSE`, `scripts/xq.py`, `references/` for the former `docs/*.md` guides, `assets/` for the former `examples/`),
  with `.agents/skills/executor-queue` as a relative symlink for Codex. Install the whole directory with the Skills CLI:
  `npx skills add dominno/claude-executor-queue --skill executor-queue -a claude-code -a codex` (`-g` for every
  project, `--copy` for independent copies, or a local clone path instead of the GitHub name).
- `install.sh`, `install.ps1`, `scripts/install.py` and `VERSION` are removed; the version lives in `xq.py --version`
  and `SKILL.md`. Delete a copy made by the old installer (and its `.executor-queue-install.json`) before
  reinstalling. The per-version notes and dated review documents under `docs/` are removed; this changelog is the
  record. `SKILL.md` links its guides relatively and says how to resolve the skill directory in Codex.
- `doctor --need agy`, `poll` and `close` find agy-bridge installed as a skill (`.claude/skills/agy-bridge/scripts/agy-bridge`,
  then `.agents/skills/...` in the project, then the same two under the home directory) before the Claude Code
  plugin; doctor prints the location it used. The Codex companion lookup is unchanged.
- Internal: xq.py split into the executor_queue package (core/port/adapters/cli)

### Upgrading
- Use this helper in every session that shares a ledger once a project uses config `ledger` or `xq import`: the
  3.6.0 helper ignores `import` events (imported history is invisible to it) and always reads
  `.agents/ledger.jsonl`, so it would not see a ledger moved elsewhere.

### Added
- Config `ledger`: a repo-relative `*.jsonl` path for the xq ledger (default `.agents/ledger.jsonl`); the lock lives
  next to it (`<name>.lock`). Absolute paths, `..` and symlinks are refused. Every command reads and appends through
  one accessor; `init` excludes a custom ledger under `.agents/` but leaves one outside `.agents/` tracked (committed
  history, info line) and always excludes its lock; `doctor` prints the active ledger; `compose --out` refuses it.
- `import --from PATH [--map JSON] [--dry-run]` appends an external append-only JSONL ledger as read-only history:
  one `import` event per source line with provenance `{source, line, sha256}`; `--map` (or config `import_map`) maps
  `id` (required), `executor`, `status`, `brief`, `ts` to source keys, with optional `status_values` mapping source
  values to done|failed|running|queued|closed. Re-running skips lines already imported (sha256); malformed lines and
  ids that collide with xq tasks are reported by line number and skipped (exit 1 after importing the good ones);
  a UTF-8 BOM is tolerated. A ledger path inside `.git/` is refused.
  Imported tasks show `IMPORTED` in `status` (`imported: true` and `source_executor` in `status --json`, additive),
  never occupy a slot, are never stale, open or flagged by the Stop hook, and refuse poll --id (also --wait),
  finish/verify/merge/ack/close,
  a new assign under their id and `--follows`.
- `brief-lint --brief PATH [--enforce] [--json]` checks a brief (fenced code and HTML comments removed): one
  non-placeholder `Result:`, one final `## Acceptance` with `Files:`/`Checks:`/`Notes:` (repo-relative Files), configured
  restricted words only on lines with the handoff marker (default `lead runs`, empty list by default), configured header
  field allow-lists, optional Notes file, no match-everything `Files:` glob; CommonMark fence lengths and a UTF-8 BOM
  are handled; warns on compound or long Results and too many numbered steps. Config
  `brief_lint` (`mode` off|warn|enforce); `assign` lints only when it is configured (warn: stderr only; enforce: refuse
  unless `--force`).
- `compose --brief PATH [--out PATH]` writes the launch prompt: configured `context_files` (repo-relative regular files,
  each delimited with its path and sha256 prefix) followed by the brief, to `.agents/review/<stem>.prompt.md` by
  default; `--out` refuses xq state, the config and existing files other than `*.prompt.md`. `assign --prompt PATH` records `prompt` and `prompt_sha256`.
- Declared-scope check: a Codex delivery's changed paths (the SENSITIVE review range plus uncommitted/untracked
  worktree paths) are compared with the brief's Acceptance `Files:` (`/`-aware fnmatch, `**`, `{a,b}`, directory
  entries). `poll`/`finish` on done and `verify --verdict pass` print `WARN SCOPE` lines; the verify event records
  `out_of_scope`. Config `scope_check` off|warn|enforce (default warn); enforce refuses a pass unless
  `--accept-scope "<reason>"` (recorded as `scope_accepted`). A brief without `Files:` warns and is never refused.
  Changes that came in from the default branch (merge or rebase mid-task) and, at verify, untracked leftovers past a
  clean delivery commit do not count.
- Stale-base admission: `assign --worktree` and new `preflight --worktree PATH` report a worktree HEAD missing
  default-branch commits (config `main`, `origin/HEAD` or the primary branch; offline) with the commit count and the
  exact, shell-quoted `git -C <wt> rebase <main>` fix; it checks the worktree's current HEAD, also on a queued
  promotion. Config `stale_base` off|warn|refuse (default warn); refuse blocks assign
  unless `--force` and makes preflight exit 1. No resolvable default branch prints a NOTE and skips the check.
- `doctor --need agy` probes `queue cancel --help` and `queue watch --help` (`--detach`) and reports each
  missing one as MISSING with the recipe step it breaks and `/plugin update agy@agy-bridge`; the plugin marketplace
  fallback line prints only when a plugin check is MISSING. `close` on a pending job suggests manual removal plus
  `close --force` when the bridge has no `queue cancel`.
- Promoting a queued agy task whose bridge job a watcher already ran imports the archived result in place
  (`ran_before_promote: true`, WARN that preflight was bypassed); `assign --queued --executor agy` and
  `preflight --executor agy` print a NOTE when a bridge runner/watcher is active.
- `doctor --need codex` (online) checks that the configured Codex default model is offered to the account
  (`codex doctor --json` + `codex debug models`), lists alternatives when it is not, warns when it cannot verify,
  and prints the auth method and the companion job state root.
- `assign --model/--effort` record what was passed to the executor; `status` and `status --json` show them (additive).
- Codex `assign --worktree` records the assignment `base`; `--job` records `codex_state_root` from `CLAUDE_PLUGIN_DATA`.
- `poll --id X --wait [--timeout 9m] [--interval 30s]` re-polls one task until it is terminal (exit 0), a PROBLEM
  (exit 1) or the timeout (new exit code 3), printing one stderr line per state change and writing rows only on change.
- `assign --follows <id>` names the earlier attempt (same executor and worktree) a fix attempt follows and records its
  `review_base`; unknown ids, another executor or another worktree are refused.
- `status --id <id> --field review_base|base|head|verified_head|worktree|job` prints one bare value for shell
  recipes (`review_base` falls back to `base`, the start of the reviewed range).
- The Stop hook adds one `Next:` line with the absolute helper path, the verify command for the first unverified task,
  the poll command for a stale one, and "never create work to silence this hook".
- `preflight` refusals list up to 5 counted processes per executor (`codex counted: pid N <first 3 argv words>`,
  sanitized, `KEY=`/token-like words redacted) and say when live processes are not in this ledger.
- Codex `poll`/`finish` recording `done` print the sensitive report (committed range plus uncommitted, untracked and
  ignored-sensitive worktree files) as `WARN SENSITIVE` lines, and an uncommitted delivery prints its dirty paths with
  the commit-then-`verify --head` step.

### Fixed
- Bridge cancel/refusal results (`<!-- agy-bridge-refusal-v1 sent: no -->`, or the legacy `# Queue refusal`)
  are recorded as `failed` with `not_sent: true` and `refusal: <path>` instead of `result`, so `close --reason
  dropped|superseded-by:X` needs no verdict; `close` imports such a refusal for a running task inside its lock. A
  failed `--delegate-exit` without an `--out` file records no `result`.
- Codex `poll` re-reads the companion once (0.75 s) before reporting a dead worker `orphaned` or a status
  unavailable; `poll --wait` treats an unavailable read as transient and fails after 3 consecutive ones.
- Codex `assign` worktree refusals echo the value: relative path (with the resolved rerun), missing path
  (with the `git worktree add` command), different repository (both common dirs, `run xq from <root> or unset
  XQ_ROOT`), `needs an initial commit` (names the path); a launched assign appends that its job is already running.
- Failed/blocked work with nothing delivered can be closed (`dropped|rejected|superseded-by`) without a verdict, and
  `verify --verdict reject|fixes` never inspects the worktree, so a removed worktree no longer forces `close --force`.
  A missing worktree is reported cleanly instead of leaking `Errno` text. `verify --verdict pass` needs a done task.
- Codex delivery checks use the assignment base: a commit Codex made itself is accepted, untracked-only leftovers on a
  delivered commit only warn, and verify refusals now list dirty paths, name a no-op delivery or a non-descendant HEAD.
  The descent and no-op checks run on every Codex pass path, including a head recorded clean by finish/poll, so
  orphan or reset-to-ancestor history cannot pass without `--force`.
- `doctor` no longer echoes the Codex default model or model slugs when they contain unexpected characters.
- `finish --status done` refuses a queued Codex task that never launched.
- Live process counts read `ps -axww -o ucomm=,args=` and prefer argv[0], fixing missed agy/Codex processes behind
  macOS's truncated `comm`; `codex-companion.mjs task-worker` and the npm `codex` shim count, `app-server` does not.
- `poll` finds Codex jobs outside the SessionStart-hook environment by trying the recorded state root, the current
  environment and the TMPDIR fallback, and names the roots tried when all fail.
- poll detects Codex jobs whose worker died and no longer refreshes staleness on unchanged polls: a `running` job whose
  companion `pid` has exited records an `orphaned` observation and a PROBLEM with the cancel/finish next step, and a
  `seen` heartbeat is written only when the companion status, `phase` or `updatedAt` changes.
  An out-of-range companion pid (>= 2**31) is ignored instead of crashing poll, the PROBLEM says the worker "appears to
  have exited" (a sandboxed poll in another PID namespace can misreport), and docs note that a healthy single-phase job
  longer than the stale threshold needs `ack`.
- `poll --timeout/--interval` without `--wait`, and `verify --ack-sensitive` outside a Codex pass with a recorded base,
  are refused instead of silently ignored.
- A clean Codex finish at the assignment base (nothing committed) records `delivery_base` instead of pinning an
  unusable `head`, so the lead can commit and `verify --head <sha>` without `--force`; old ledgers with `head == base`
  are treated as unpinned, and the `no commit beyond recorded base` refusal names the verify command.
- `doctor` reads the Codex default model from `codex doctor --json` stdout even when it exits nonzero, reports an unset
  model (`<default>`) as a WARN instead of an "unexpected characters" error, prints `SKIP Codex default model` with
  `--offline`, and keeps parse-failure reasons to one sanitized line of at most 120 characters.
- A closed task still marked running can record `finish` without `--force` (frees its slot; preflight names the
  command), and an explicitly closed finished attempt can be reassigned without a verdict.
- CI runs Python 3.8 on ubuntu-22.04 (no longer available on ubuntu-latest) and pins actions to commit SHAs.
- Live process counts skip zombie, dead and exiting processes (`ps` state `Z`, `X` or `E`, such as macOS `UE`), so
  preflight no longer refuses forever behind exiting agy/Codex processes; `ps` runs with `LC_ALL=C`. The live process
  test starts `sleep` under an `agy` argv[0] (no symlinked binary) and always terminates and reaps it.
- Codex `verify --verdict pass --head <sha>` works after the worktree was removed: it skips the dirty-path check but
  still requires a commit past and descending from the base that resolves in the primary checkout. A Codex pass on a
  task with no pinned head, forced or not, pins the verified head, so a later non-forced `merge` succeeds. The removed
  worktree hint names the right next step with and without `--head`.
- `poll --wait` stops with exit 1 when an agy job stays pending with no active bridge runner on two consecutive polls
  (probed on `.agy-bridge/queue/watcher.pid` like the bridge's `QueueLocation.running()`), instead of waiting out the
  timeout; poll prints `pending; bridge runner active` or the `queue run` hint. A ledger write conflict during a wait
  (`LedgerConflict`) is re-read: a task recorded terminal by a concurrent writer returns 0, other conflicts retry and
  fail after 3 in a row. Plain `poll` is unchanged.
- A Codex or cloud pass on a head other than the recorded delivery now pins that head and records `replaced_head`
  (shown by `status --id`), so `merge` no longer refuses with "verified head must match the recorded delivery". A
  head that descends from the recorded delivery (a lead fix or a late commit on `xq/<id>`) needs no `--force`; any
  other head still does. Such a pass is reviewed from the review base, and it requires `--ack-sensitive` when there
  are SENSITIVE hits, even with `--force`. cloud-collect keeps comparing the collected delivery.
- `merge` finds the merge that integrated the verified head on the target's first-parent history and checks only
  its parents that contain that head, so a later unrelated `--no-ff` merge on top is no longer refused (or reported
  as the delivery). The refusal's next step is a non-forced `verify --head <parent>`, with `--ack-sensitive` when the
  extra commits touch SENSITIVE paths.
- `merge` no longer prints "commits after verified head" on every `--no-ff` merge (only when such commits exist), and
  no longer warns about the earlier attempt closed as `superseded-by:<this id>` in the documented fix loop.
- `agy-sync --delegate-exit` accepts answers that quote the bridge's v2 marker or show `exit_code:` lines (reviews of
  bridge/xq code); it refuses only a file starting with the exact bridge result prefix, or a task with a queue archive.
  The exit code still comes only from `--delegate-exit`. `poll`/`agy-sync` print the `--delegate-exit` hint for a
  running agy task with `.agy-bridge/delegate/<id>.md` instead of "no bridge artifact found".
- `close` of an agy task refuses while `.agy-bridge/queue/<id>.job` is pending and prints the
  `agy-bridge queue cancel <id>` command (needs agy-bridge with `queue cancel`); `close --force` closes with a WARN.
- `assign` on a running task names the executor and job and the next step (poll/finish, or a fresh id with
  `--follows`); a closed but running task says to finish it failed once stopped.
- `verify` without `--head` on an unpinned Codex delivery names the worktree, its dirty paths or "no commit beyond
  base", `git -C <wt> status` and the full next `xq.py verify ... --head` command.
- `doctor`: the no-default-model WARN names `--model` for the Codex companion task; a non-OpenAI `model provider` prints
  `WARN cannot verify Codex default model for provider <p>` instead of MISSING; the hint uses the reported
  `config.toml` path.

### Changed
- Internal: restructured xq.py into core/port/adapters/cli (executor adapter registry, one injectable process
  runner, long functions split into named helpers; no behaviour change).
- `poll --wait` defaults to `--timeout 9m`, below the 10-minute Bash tool limit; SKILL.md runs waits and `queue run`
  with a 600000 ms Bash timeout or `run_in_background` and repeats on exit 3.
- `status --id X` (text and `--json`) adds the recorded `worktree`, `base`, `review_base`, `job`, `head`,
  `verified_head`, `brief`, `model` and `effort`; existing fields are unchanged and listings without `--id` keep the
  shorter allowlist.
- SKILL.md recipes restate variables in every Bash call and use `"${wt:?}"`, `"${root:?}"`, `"${base:?}"`,
  `"${CC:?}"` and `"${AGYB:?}"` so an unset value fails instead of targeting the primary checkout; review diffs use the
  recorded base SHA from `status --id --json`, not the moving branch.
- Order refusals name the task state, executor and next command; unknown ids suggest a case variant; `close
  superseded-by:X` validates X and says how to record it first; merge without origin/HEAD names the local branch.
  Every subcommand and option has `--help` text, and help lists exit codes.
- Doctor `OK` lines state only the fact; `MISSING name: detail -> next: <fix>` carries the remediation. With `--need`,
  capacity and preflight-refusal lines cover only the requested executors, and unknown `--need` entries are echoed.
- Refusals name SHAs and next commands: merge without pass (verdict and reader), merge commit missing the verified head,
  unreachable merge commit (`--main <branch>`), verify head mismatch (`got`/`recorded delivery`), invalid commit refs
  (escaped), agy name reuse (existing artifacts and a fresh `-r2` id), agy job-name mismatch, Codex job collisions
  (active task and state), removed worktrees (finish/poll vs verify next steps), queued-task hints per executor and
  `close superseded-by:` without a verdict (`verify --verdict fixes|reject`). Executor-created path lists are escaped,
  truncated to 120 characters each and labelled `untrusted executor-created paths:`.
- `assign --executor codex` without `--queued` requires `--worktree` (also when promoting a queued task) and prints
  the `git worktree add` and full assign commands; `--force` allows it and is recorded.
- SKILL.md/docs: the shell variable for the chosen branch is `branch` (was `base`); the review base comes from
  `status --id <id> --field review_base`; delegate runs with its sync in one Bash call (timeout 600000 ms, delegate
  `--timeout 8m`, `events --tail 10` when killed); bridge verdict for delegate reviews always
  passes `--results-dir .agy-bridge/delegate` (works with every bridge version); Codex launch shows `[--model "<model>"]`
  for the companion and assign; dropping a queued agy job uses `queue cancel` before `close`.

### Security
- The worktree scan also runs `git ls-files -v`: skip-worktree (`S`) and assume-unchanged (lowercase) paths,
  whose edits git status hides, are reported as `<path> (index-hidden)`, always dirty (no clean head is pinned, finish
  WARNs) and SENSITIVE when they match the rules (verify pass needs `--ack-sensitive`, merge repeats it); a failed
  listing fails closed.
- The worktree scan compares against a scratch index read from HEAD (`GIT_INDEX_FILE`, `read-tree`), not
  the executor-writable worktree index: a forged index entry (HEAD's blob id with new stat data) or relaxed stat
  config (`core.trustctime=false`, `core.checkStat=minimal`; now also pinned by `-c`) no longer hides an edit, and
  staged-but-uncommitted ignored files are listed. Skip-worktree paths absent from disk (sparse checkout) are no
  longer dirty, so sparse worktrees verify without `--force`. Filter drivers defined in `config.worktree` are blanked
  for the scan, so a planted `filter.<x>.clean`/`process` never runs. A failed index-flag listing only drops the
  `(index-hidden)` label (the edit is still reported as `(uncommitted)`).
- `poll` keeps the first read when only the re-read fails (a dead-pid running job is still reported
  orphaned, not unavailable), and pauses once before all re-reads instead of once per task.
- Git in executor worktrees runs with `-c core.fsmonitor=false -c core.untrackedCache=false
  -c core.hooksPath=/dev/null` (a `config.worktree` hook never runs), and a worktree whose git dir is not
  `<common>/worktrees/<name>` with a matching `gitdir` file (a redirected `.git` file) is refused at assign and verify.
- The sensitive review covers earlier unpassed Codex attempts by commit ancestry, whatever their worktree
  (open, or closed dropped/superseded-by), and when main is known starts no later than `merge-base(head, main)`; a
  dropped attempt whose branch a new worktree checks out is no longer missed. `--follows` accepts a predecessor whose
  worktree is gone or whose base the new HEAD contains.
- Codex `assign` refuses the primary checkout (`--force` records `primary_checkout` and reviews xq/bridge
  state there as untrusted) and, without `--queued`, a worktree another Codex task is still running in.
- Cloud `WARN SENSITIVE` covers package manifests, auto-run config (`conftest.py`, `.pre-commit-config.yaml`, `.envrc`,
  `.env*`, `.npmrc` ...), editor/devcontainer/agent directories (`.vscode`, `.devcontainer`, `.gemini`, `.codex`,
  `.cursor`, `.agy-bridge` ...), agent instruction files and every `.agents/` path except the task's own status file.
  Rules live in `SENSITIVE_NAMES`/`SENSITIVE_DIRS`/`SENSITIVE_GLOBS`.
- SENSITIVE matching is case-insensitive (macOS/Windows) and covers more auto-run files: `pytest.ini`, `.gitmodules`,
  `AGENTS.override.md`, `mise.toml`, `.mise.toml`, `.tool-versions`, `Justfile`, `build.rs`, `.cargo/` and
  `requirements*.txt`. The task's own status file stays exempt only under its exact-case path.
- Codex `verify --verdict pass` lists SENSITIVE paths and symlinks changed since the assignment base as
  `WARN SENSITIVE`, records them on the verify event (`sensitive`, `sensitive_more`, `ack_sensitive`) and refuses until
  the reader inspects them and passes `--ack-sensitive` (or `--force`); `merge` repeats the warnings.
- agy-sync no longer reads exit metadata from plain delegate output; the delegate recipe writes to .agy-bridge/delegate/
  and passes --require-verified. A result is bridge output only when it is a complete agy-bridge v2 queue result
  (`# <id>`, v2 marker, `### Attempt N (round N)` block, `## Round 1`, matching `agy-bridge-result-sha256` footer);
  a v2-marked file that fails those checks is a PROBLEM, and anything else (including model text with `exit_code: 0`
  and `## Answer`, or legacy results) observes `unknown` with a `--delegate-exit` hint and never finishes.
- A Codex fix attempt in the same worktree inherits the earlier unpassed attempt's base as `review_base` (via
  `--follows`, automatically with `NOTE review base inherited from <id>`, or on same-id reassign), so `verify` and the
  `merge` commit-message scan review the first attempt's commits too.
- SENSITIVE rules cover agy-bridge's EDIT_DENY and more auto-run files: `.agent`, `_agents`, `_agent`, `.windsurf`,
  `.clinerules`, `.roo`, `.kiro`, `.direnv`, `.yarn`, `.mvn`, `.circleci`, `.agy-bridge.json`, `.pnpmfile.cjs`,
  `go.mod`, `Cargo.toml`, `Gemfile`, `Rakefile`, `.gitlab-ci.yml`, `gradle-wrapper.properties`, `.agents` at any depth;
  names match on any path component. Gitlinks (mode 160000) are flagged as `submodule pointer`.
- Cloud `verify --verdict pass` runs the same sensitive-path review as Codex (pinned base, or the fork from main for old
  ledgers, to the collected head), accepts `--ack-sensitive`, records the list on the verify event and `merge` repeats
  it. Same-batch sibling status files whose blob at the head still matches the collected report are exempt, so later
  tasks of a per-task-status-commit batch no longer need `--ack-sensitive` for clean deliveries; the range stays
  cumulative from the pinned base (earlier tasks' sensitive changes are listed again).
- `merge` refuses a merge commit whose delivered (non-first) parents bring commits the verified head does not contain
  (e.g. a post-verify commit on `xq/<id>`), naming up to 5 SHAs with subjects and any SENSITIVE paths, with the
  `verify ... --head <parent>` step; `merge --force` records it with the same list as WARN. For cloud tasks, commits
  that only change `.agents/cloud-status/<cloud task>.json` (the batch's report commits) print a WARN instead.
- The Codex sensitive review covers worktree files that are not committed: uncommitted and untracked (listed per file
  with `-uall`, NUL-separated so quoted names cannot hide a directory) and ignored files matching the rules or
  `*.pth`/`sitecustomize.py`/`usercustomize.py`. They need `--ack-sensitive` and are recorded with an
  `(uncommitted)`/`(untracked)`/`(ignored)` suffix. Inside dependency directories (`node_modules`, `.venv`, `venv`,
  `site-packages`, `.tox`, `vendor` ... see `DEPENDENCY_DIRS`) only interpreter hooks count, except the standard
  `_virtualenv.pth` and `distutils-precedence.pth`, so installed packages' manifests and configs do not make
  `--ack-sensitive` routine; dependency trees are listed separately, so they cannot use up the 5000-entry cap of the
  rest. A listing that fails or stops at the cap adds a hit that needs `--ack-sensitive`. `WARN SENSITIVE` output
  stops after 20 lines with `+N more`; `poll` prints the Codex done report once, after the ledger write.
- SENSITIVE rules add Gradle/Maven/CMake/Meson/Bazel/sbt/Nix/Taskfile builds, Dockerfiles and compose files,
  `.devcontainer.json`, `.pdbrc`, interpreter start-up hooks, runtime version pins (`compose*.yml`/`compose*.yaml` too), `*.config.{js,ts,mjs,cjs}` and
  `.eslintrc*` (a `build/` output directory is not treated as a Bazel `BUILD` file).
- xq runs git with `GIT_OPTIONAL_LOCKS=0`, so status reads in a worktree never race the lead's index lock.

### Docs
- README rewritten for new users: what the tool is and is not (it never launches executors), a glossary, a
  quickstart covering one whole Codex task, a command cheat sheet, a configuration section, a docs index and
  contributor notes on the CORE/PORT/ADAPTERS/CLI layout. New `docs/configuration.md` describes every config key
  (including `paused`), and `examples/.agents/config.full.json` lists them at their defaults.
  `examples/codex-task.md` now passes `brief-lint`. `docs/agy.md` exports the worktree review diff with
  `ls-files -o`/`diff --no-index` instead of `git add -N`. `docs/job-status.md` adds a task-state diagram and the
  `IMPORTED` flag and fields. The architecture, executor-path and lifecycle figures show config, the ledger
  setting, import, compose, brief-lint, preflight, the scope and sensitive warnings, merge and close.
  Fix-loop preflight passes `--worktree`. SECURITY.md links the README's "What gets shared" section.
- SKILL.md: the fix loop records `verify --verdict fixes` before `close superseded-by`, launches the fix fresh in this or
  a later session; one ordered agy queue recipe (assign --queued, job name, queue add --dry-run, preflight, promote,
  queue run); the worktree review diff lists untracked files with `ls-files -o` and appends them with
  `diff --no-index` instead of `git add -N`; `poll --wait` and the `orphaned` PROBLEM; model guidance after SKIP/WARN.
- SKILL.md and docs/codex.md: failed/cancelled attempt recipe, numbered fix loop (assign the fix before
  `close superseded-by`), one ordered verify-and-merge sequence, `--main` guidance in the common contract, model
  guidance, the assignment base, untracked files and the Codex job state location.

## v3.6 — 3.6.0 — 2026-11-24

- Add read-only status filters and a versioned JSON result with recorded executor observations and slot occupancy.
- Add scoped Codex and agy polling and keep cloud report collection explicit.
- Separate task replay, ledger transactions and executor observations inside the existing helper.
- Isolate CLI test environments and illustrate the difference between reading and refreshing status.

## v3.5 — 3.5.0 — 2026-11-24

- Bind Codex verification to a committed delivery and retain the dispatch scope during worktree polling.
- Keep cloud reports scoped to their session and require approval for push-hook and Git LFS transfers.
- Add local-only diagnostics with `doctor --offline` and clarify offline installer migration and lock handling.

## v3.4 — 3.4.0 — 2026-11-24

- Bind cloud result pushes to the approved endpoint and session branch.
- Give cloud envelopes independent batch identities and preserve canonical delivery commits.
- Reconcile terminal results for closed tasks and protect integration records from stale verification.
- Align agy diagnostics with the bridge and prefer plugins scoped to the current Claude Code project.
- Harden offline install and uninstall recovery, respect Claude profiles, and clarify Skills CLI updates from local paths.

## v3.3 — 3.3.0 — 2026-11-24

- Package the Claude Code skill for `npx skills add` at project and user scope, under the MIT license.
- Resolve the helper from the loaded skill and require explicit user invocation in Claude Code.
- Align setup and queue handling with the current `claude-agy-bridge` plugin.
- Preserve the prior offline installation if publication fails and harden ledger reconciliation.

## v3.2 — 3.2.0 — 2026-11-13

- Label local HEAD fallback and require an explicit main branch for merge/cloud commands without origin/HEAD.
- Clarify same-Claude-session resume limits and fresh fix launches in the same worktree.
- Reconcile plain delegate successes/failures with explicit exit codes and absolute output paths.
- Repair non-UTF-8 text arguments before events; reject unsupported direct cloud assignments.
- Bound cloud reports, drop unknown stored keys, and retain full report SHA-256 for change detection.
- Document Git >= 2.31 and report/warn on Git versions in doctor. Keep Stop hooks read-only and unlocked.
- Add validated load/disk/concurrency preflight, conservative injectable OS process counts, doctor readings, and idle-hook refusal diagnostics.
- The regression mapping and suite results were in docs/v3.2-notes.md (removed; see Git history).

## v3.1 — 3.1.0 — 2026-11-09

- Address every confirmed council-table row (16 important, 8 actual nice rows; report summary overcounts by one).
- Invalidate stale approvals, pin verified heads, enforce different readers and validate integration ancestry.
- Isolate cloud collection per batch; diagnose branch conflicts and strengthen pinned-base/task provenance and sensitive-path warnings.
- Add heartbeat/ack and explicit close; fix queued agy promotion and complete worktree review exports.
- Protect local state exclusions and symlink writes; guard missing-install hooks and stage upgrade swaps with rollback.
- Harden Windows locking, UTF-8 output, bridge launch and PowerShell Python detection; add Windows CI configuration.
- Keep the installed payload to two stdlib/Python 3.8+ files. Per-finding regressions were in docs/v3.1-notes.md (removed; see Git history).

## v3 — 3.0.0 — 2026-11-08

- Add repo/CLI/skill version markers and release history.
- Protect installed local edits with recorded hashes; add check/uninstall/force
  options to offline POSIX and Windows installers using one Python implementation.
- Add project examples and an Ubuntu/macOS Python 3.8/3.12 test matrix.
- Keep the installed skill self-contained; move longer recipes into repo docs.
- Expand installation, quickstart, executor requirements, safety and contribution docs.

## v2 — 2.0.0 — 2026-11-08

Summary of the 25 items in the v2 critic review (removed from docs/; see Git history; item 25 was a
rejected objection, retained for context rather than claimed as a new fix):

1. Stop hook reads stdin, honors recursion guard, limits idle enforcement to real
   backlog/config, supports pause/resume and never invents work.
2. Resolve agy-bridge from installed plugin metadata, with version-sorted fallback;
   probe queue/verdict capabilities rather than trusting a version label.
3. Store absolute briefs and use absolute Codex prompt-file paths.
4. Track cloud briefs/status, ignore only local state, add local-only init mode.
5. Resolve Codex companion from plugin metadata or numeric version sorting; quote CC.
6. Record Codex job/worktree, query result/cancel explicitly, import terminal states
   with poll and flag stale running work.
7. Add executor-selective doctor checks for tools, auth, Node, Git and cloud origin;
   document installation/authentication and capability fallbacks.
8. Discover the base, use absolute worktrees and fresh retry branches, prepare real dependencies.
9. Inspect scope/artifacts/symlinks, stage named files and integrate only with authorization;
   record the real merge SHA and clean up worktrees.
10. Inspect untrusted scripts, hooks, instructions, lockfiles and tests before execution;
    run reviewed checks in a clean environment.
11. Treat all executor output as data; pin cloud instructions and flag sensitive changes.
12. Require explicit Codex read/write boundaries and blocked-on-key behavior.
13. Bound agy manifests/schema/context, export worktree diffs, assign before dispatch,
    use unique names, validate headers/exits, sync archives and require opt-in watching.
14. Validate pushed brief bytes/headings/IDs; support optional session branches,
    discovery/reprint and explicit remote cancellation instructions.
15. Validate all cloud report schemas/types/provenance before writing; diagnose
    missing/changed reports, base/head ancestry and malformed JSON.
16. Verify batches per task from pinned base; map commits, reject selectively and
    restrict failed/blocked tasks to their own status files.
17. Scan all outgoing commits/blobs/key patterns, consent per push, support protected
    branches and document cloud environment prerequisites.
18. Reset reassignment metadata, share ledger across worktrees, tolerate corrupt
    lines and enforce lifecycle/merge guards and transaction locking.
19. Expand cloud/session triggers while preferring project-owned queues.
20. Document effort defaults and omit model selection by default.
21. Document bounded fix loops with new IDs and fresh verification.
22. Document cloud prerequisites, small trials and stale cloud detection.
23. Add cross-platform locking, evidence bounds, UTF-8 and Windows hook guidance.
24. Validate branch names and use safe fetch argument separation.
25. Preserve the existing all-outgoing-commits scan; add the concrete scan command
    under item 17 (the original objection was rejected).

Historical validation notes were in `docs/v2-notes.md` (removed; see Git history).

## v1 — 1.0.0 — 2026-11-08

- Initial imported Claude Code skill and stdlib xq helper: assignments, results,
  verification/merge records, doctor, Stop hook and cloud envelope/collection.
- Initial copy installer and offline temporary-repository test coverage.

The v1/v2 numbers label historical iterations; explicit version markers begin in v3.
