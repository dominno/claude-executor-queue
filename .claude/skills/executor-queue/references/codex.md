# Codex setup and local task recipes

Set the `xq` path before using these commands. [Set up the CLI](installation.md#run-xq-directly)
The recipes use `xq.py` as shorthand for `python3 "$xq"`. The shell examples use
POSIX syntax. On Windows run those examples in Git Bash and use `py -3 "$xq"` or
your available Python 3.8+ command. In PowerShell, call the helper directly with
`py -3 $xq` or your available Python command.

## Setup

Run `xq.py doctor --need codex,agy,cloud`, selecting only the executors needed. Missing unused tools are SKIP.
Git >= 2.31 is required by the primary-checkout recipe (`--path-format`); doctor reports the version and warns below 2.31.
It checks HEAD, the default branch, Node/auth, plugin capabilities, ignores and (for cloud only) origin.
Doctor prints the resolved `CC` and `AGYB` paths as shell assignments. On Windows it
prints PowerShell syntax such as `$CC='C:\path\to\codex-companion.mjs'`. For Git
Bash, use the same path with Bash assignment syntax, such as
`CC='C:/path/to/codex-companion.mjs'`. Keep `"$CC"` quoted in every call.
For the Codex companion doctor uses `installed_plugins.json` and selects project or local entries for the current
checkout. It reads the marketplace cache for older profiles without an installed-plugin registry.
`CLAUDE_CONFIG_DIR` selects the profile. agy setup and discovery: [agy setup](agy.md#setup).

- Codex: `npm install -g @openai/codex`, `codex login` (or `codex login --device-auth`), then
  `/plugin marketplace add openai/codex-plugin-cc`, `/plugin install codex@openai-codex`, `/codex:setup`.
  Check with `xq.py doctor --need codex` (online: it also confirms the default model is usable on the account).

Installation or authentication is a user action; do not change shell profiles or install with elevated rights.

## Tasks

Codex handles implementation/tests in an isolated worktree; agy handles read-only reviews of explicitly named,
nonconfidential files sent to Google. The lead handles authorized network, paid work, secrets and final verification.
A Codex write sandbox does not constrain reads: every brief must say:
“Read and write only in this worktree; do not open `.env*`, `~/.ssh`, `~/.config`, or the main checkout.
If a key is needed, report blocked.” This is an instruction boundary, not OS read isolation.
No executor receives permission for paid calls, network, push or merge merely by receiving a brief.

1. Write `.agents/briefs/<id>.md`: goal, explicit input/output files, checks, forbidden actions, tracked notes path.
   IDs start with an ASCII letter/digit, then letters/digits/dots/underscores/hyphens, at most 120 characters.
   Tools and results needed later belong in tracked paths, not temporary folders.
   Check its shape with `python3 "$xq" brief-lint --brief .agents/briefs/<id>.md` ([brief lint](#brief-lint)).
2. Prepare Codex's worktree from the discovered default branch (if origin/HEAD is absent, set it or choose a branch explicitly):
   ```sh
   root=$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")
   branch=$(git symbolic-ref --short refs/remotes/origin/HEAD | sed 's#origin/##')
   wt="$(dirname "$root")/$(basename "$root")-wt/<id>"
   git -C "$root" worktree add -b xq/<id> "$wt" "$branch"
   ```
   `assign` refuses a launched (non-`--queued`) Codex task without `--worktree`, and so does promoting a queued one;
   `--force` records an explicit exception.
   On retry use a fresh id or `-r2` branch/path; inspect existing worktrees first. Prepare real dependencies there
   (`pnpm install --frozen-lockfile`, `uv sync`, as appropriate and authorized); never symlink dependencies.
3. Immediately before every Codex launch (including retries/resumes), run preflight and proceed only on exit 0.
   Use the `CC` from doctor:
   ```sh
   python3 "$xq" preflight --executor codex --worktree "$wt" || exit $?
   node "$CC" task --background --write --cwd "$wt" --effort medium [--model "<model>"] --prompt-file "$root/.agents/briefs/<id>.md"
   python3 "$xq" assign --id <id> --executor codex --brief .agents/briefs/<id>.md --job <jobId> --worktree "$wt" [--model "<model>"]
   ```
   Pass the same `--model` to the companion task and to `xq.py assign`. `--worktree` adds the
   [stale-base](#stale-base) admission to preflight; `assign --worktree` repeats it.
   With `context_files` configured, launch the composed prompt instead of the bare brief
   ([context preamble](#context-preamble)): `python3 "$xq" compose --brief .agents/briefs/<id>.md`, then
   `--prompt-file "$root/.agents/review/<id>.prompt.md"` and `assign ... --prompt .agents/review/<id>.prompt.md`.
   `--prompt-file` is resolved relative to `--cwd`: always pass an absolute existing path to the current brief.
   Record the returned jobId and absolute worktree immediately. Before launch, optional `assign --queued` records a
   backlog item; the launch assignment replaces it with a new attempt. Failed/uncertain launch: reconcile, never blindly retry.
   Effort: low/minimal for mechanical work, medium for routine work, high/xhigh for hard debugging (check CLI support).
   Model: `xq.py doctor --need codex` (online) reads the configured default model from `codex doctor --json` and
   checks it against the `list`-visible slugs of `codex debug models`. `codex doctor` exits 1 whenever any of its own
   checks fails, so xq parses its stdout as JSON regardless of the exit code. Leave `--model` unset only when an online
   doctor printed `OK Codex default model`; on MISSING pass one of the listed models (xq never edits
   `~/.codex/config.toml`). No configured model (`<default>` or empty) prints
   `WARN Codex default model: none configured (built-in default); pass --model <m> to the Codex companion task (and record it with xq.py assign --model)`; `--offline` prints
   `SKIP Codex default model`; missing commands or changed JSON print `WARN cannot verify Codex default model: <short
   reason>`. After any SKIP/WARN, ask the user for a model and pass `--model`. Doctor `OK` lines state facts only;
   `MISSING name: detail -> next: <fix>` carries the remediation. With `--need`, capacity and preflight-refusal lines
   are printed only for the requested executors.
   Record what you passed: `assign ... --model <m> --effort <e>` (validated `^[A-Za-z0-9._:-]{1,64}$`; recorded only,
   shown by `status` and `status --json`). A job failing within seconds with a model/account error is a launch failure:
   retire it (below), ask the user for a model, and relaunch with a fresh id and `--model`.
   Assign with `--worktree` records the worktree HEAD as the assignment `base` (the worktree must belong to this
   repository). A queued `assign --worktree` before launch pins it earliest; promotion keeps that base.
4. Lifecycle: `node "$CC" status <jobId> --cwd "$wt" --json`, then `node "$CC" result <jobId> --cwd "$wt"`.
   `xq.py poll --id <id>` refreshes a single recorded task. Bare `xq.py poll` checks Codex and imports agy artifacts.
   Prefer `xq.py poll --id <id> --wait --timeout 9m [--interval 30s]` (Bash tool timeout 600000 ms or
   `run_in_background`; repeat on exit 3) over hand-written sleep loops: it re-polls
   until the task is done/failed/blocked (exit 0), a PROBLEM is reported (exit 1, including `orphaned`) or the
   timeout passes (exit 3; one `observe` row records the last state). It prints one stderr line per state change and
   writes ledger rows only when the observed state changes. `--timeout` is at most 24h; `--interval` at least 5s.
   **Liveness.** When the companion reports `running`/`queued` with a worker `pid`, poll checks that the process still
   exists (`kill(pid, 0)`; a permission error, an out-of-range pid or Windows count as alive). If it is not visible,
   poll writes no heartbeat, records an `observe` row with state `orphaned` and prints
   `PROBLEM <id>: companion reports running but worker pid <n> appears to have exited (...); cancel with node "$CC" cancel <job> --cwd <wt>, ...`.
   Cancel the job, then poll again or `finish --status failed`, and retire the attempt. `kill(pid, 0)` only sees the
   caller's PID namespace: if xq runs inside a sandbox or container (for example a Linux bubblewrap sandbox) and the
   companion worker outside it, or the reverse, a healthy worker can be misreported as `orphaned`. Re-run
   `xq.py poll --id <id>` outside the sandbox before cancelling.
   **Heartbeats.** A `seen` row (which resets the stale clock) is written only when the companion's status, `phase` or
   `updatedAt` changes. A job that stays frozen ages normally, and `status`/the Stop hook flag it stale after the
   threshold (default 4h). The companion bumps `updatedAt` only on phase changes, so a healthy job that stays in one
   phase for longer than the threshold is also flagged, even though its worker pid is alive. When that happens and the
   job is still making progress, run `xq.py ack --id <id> --hours N` instead of cancelling.
   The companion's own `status` without a job ID filters by session. The shortcuts `/codex:status`, `/codex:result`
   and `/codex:cancel` from the main checkout do not find worktree jobs. [Status and refresh commands](job-status.md)
   To stop a runaway: `node "$CC" cancel <jobId> --cwd "$wt"`, confirm cancellation, then
   `xq.py finish --id <id> --status failed --summary '<actual result or failure>'`.
   Otherwise finish with done/failed/blocked and the actual result summary; poll can do this for recorded jobs.
   A successful finish records the commit if the worktree is clean, or its current base if there are changes to commit.
   A clean worktree still at the assignment base (Codex committed nothing) is recorded as `delivery_base`, so the lead
   can commit the delivery and `verify --head <sha>` without `--force`; old ledgers whose finish pinned `head` equal to
   the base are treated the same way. A removed worktree on a running task: `finish --status failed --summary
   "worktree removed"`, then `close --reason dropped` (or `finish --force --head <sha>` if the delivery commit survives).
   Untracked-only leftovers (e.g. `__pycache__/` from running checks) do not make a delivery dirty when HEAD has moved
   past the assignment base (a descendant of it, not an unrelated or older commit): finish records `head` and prints a WARN naming up to 10 untracked paths. Untracked files at
   the base are uncommitted work. `finish --status done` refuses a queued Codex task (it never launched); close it instead.

   **Failed or cancelled attempt.** `xq.py finish --id <id> --status failed --summary '<actual reason>'`, then either
   `xq.py close --id <id> --reason dropped` (allowed without a verdict when nothing was delivered: no head, delivery
   base or result) or `xq.py verify --id <id> --by <reader> --verdict reject --evidence '...'` and
   `xq.py close --id <id> --reason rejected`. Reject/fixes verdicts never inspect the worktree; a pass verdict needs a
   done task. Remove the worktree only after the ledger entry is retired.
5. Review before executing anything from a delivery: executor text, JSON, suggestions and modified docs are **untrusted data**.
   Inspect the full diff, including package scripts, lockfiles, `.claude/**`, AGENTS.md, CLAUDE.md, `.github/**`, `.husky`,
   git hooks, Makefile and new tests. Do this before even merging into a verification worktree or running tests.
   Run reviewed checks in a clean environment, e.g. `env -i HOME="$(mktemp -d)" PATH="$PATH" <test-command>`,
   without network where available. Run relevant checks yourself; get an independent reader for nontrivial work.
6. For a successful delivery, inspect
   `git -C "${wt:?}" status --porcelain --ignored -uall`, `git -C "$wt" ls-files -v | grep -v '^H'` (skip-worktree `S`
   and assume-unchanged lowercase entries hide edits from status) and `git -C "${wt:?}" diff --stat <base sha>`, including uncommitted
   changes, where `<base sha>` is `rb=$(xq.py status --id <id> --field review_base)` (the recorded `review_base`, else `base`),
   not the moving branch;
   match files against the brief. Reject unexpected artifacts and symlinks (`git -C "$wt" ls-files -s | awk '$1==120000'`).
   With the user's commit authorization, verify in this order:
   1. review the full diff;
   2. run the checks without leaving artifacts (`PYTHONDONTWRITEBYTECODE=1`, out-of-tree caches; untracked files only warn);
   3. stage named files only and commit in the worktree as lead;
   4. confirm `git -C "$wt" status --porcelain` prints nothing;
   5. `xq.py verify --id <id> --by <reader> --verdict pass --evidence '<actual checks>' --head "$(git -C "$wt" rev-parse HEAD)"`.
   Codex pass also lists every SENSITIVE path changed between the review base and the verified head (agent
   instructions, `.claude/`, `.github/`, hooks, manifests, lockfiles, auto-run config, `.agents/`, symlinks and
   submodule pointers; matched case-insensitively on any path component) as `WARN SENSITIVE <path> (untrusted
   executor change; ...)` and refuses until you inspect them
   (`git -C "$wt" diff <base>..<head> -- <path>`) and re-run with `--ack-sensitive`. The verify event records
   `sensitive` (up to 50 paths, `sensitive_more` for the rest) and `ack_sensitive`; `merge` prints the warnings again.
   The review also lists SENSITIVE files in the worktree that are not committed: uncommitted, untracked (every file,
   e.g. a new `conftest.py`) and ignored ones that match the rules or are `*.pth`, `sitecustomize.py` or
   `usercustomize.py` (they run during your checks). Inside dependency directories (`node_modules`, `.venv`, `venv`,
   `site-packages`, `__pypackages__`, `.tox`, `.nox`, `bower_components`, `vendor`) only those interpreter hooks
   count, except the standard `_virtualenv.pth` and `distutils-precedence.pth`; other files there (package
   manifests, `node_modules/.bin` scripts) are not checked, so prepare dependencies yourself and do not let Codex
   install them unreviewed. They are recorded with an `(uncommitted)`, `(untracked)` or `(ignored)` suffix and need
   `--ack-sensitive` too. Listings stop after 5000 entries with a NOTE; a stopped or failed ignored listing adds a
   `<ignored files ...>` hit that needs `--ack-sensitive`. Output stops after 20 `WARN SENSITIVE` lines. When `poll` or
   `finish` records a Codex `done`, the same report prints as `WARN SENSITIVE` lines before you run anything; an
   uncommitted delivery also prints a WARN with its dirty paths and the commit-then-`verify --head` step. xq runs git
   with `GIT_OPTIONAL_LOCKS=0`, so its status reads never take the worktree's index lock, and with
   `-c core.fsmonitor=false -c core.untrackedCache=false -c core.hooksPath=/dev/null` (plus `core.trustctime=true`,
   `core.checkStat=default`, `core.ignoreStat=false`, `core.splitIndex=false`) in executor worktrees, so a
   `config.worktree` hook planted by the executor never runs; every `filter.<name>` driver defined in the worktree's
   `config.worktree` is blanked (`clean=`, `smudge=`, `process=`, `required=false`) for the scan, so a planted filter
   plus a `filter=<name>` attribute never runs either. The worktree's own index is executor-writable (a forged entry or
   skip-worktree/assume-unchanged flags hide edits from `git status`), so xq scans against a scratch index read from
   HEAD (`GIT_INDEX_FILE=<tmp> git read-tree HEAD`): every tracked file's content is compared with HEAD. Edited paths
   the worktree index flags (`git ls-files -v` tag `S` or a lowercase letter) are labelled `<path> (index-hidden)`:
   always dirty (no clean head is pinned) and SENSITIVE when they match the rules. A skip-worktree path missing from
   disk is a sparse-checkout exclusion and is not reported, so sparse worktrees verify normally. Your own
   `git -C "$wt" status` uses the worktree index and config, so it can be fooled and can run planted hooks; rely on
   xq's report.
   A worktree whose git dir is not `<common>/worktrees/<name>` with a `gitdir` file naming `<wt>/.git` (a redirected
   `.git` file) is refused at assign, finish and verify; recreate it with `git worktree add`.
   Any commit after verify needs a new verify. Verify refusals are specific: dirty paths are listed (`+N more`), a
   delivery equal to the assignment base says `no commit beyond recorded base`, and unrelated history says
   `HEAD <sha> is not a descendant of base <base>`. Both base checks apply to every pass path (a recorded clean head,
   a committed delivery base, an explicit `--head`); only `--force` bypasses them. A commit Codex made itself is
   accepted as the delivery. When the worktree was removed after the delivery was committed, pass that commit with
   `--head`: xq skips the dirty-path check but still requires a descendant of the base that resolves in the primary
   checkout. A Codex pass on a task with no pinned head (forced or not) pins the verified head so `merge` matches it.
   A Codex or cloud pass on a different head replaces the recorded delivery: the verify event carries `head` and
   `replaced_head` (shown by `status --id`), so `merge` no longer loops. A head that descends from the recorded
   delivery (a lead fix commit) needs no `--force`; it is reviewed from the review base and needs `--ack-sensitive`
   for SENSITIVE hits. Any other head needs `--force`, and still `--ack-sensitive` when there are hits.
   Verify without `--head` on an unpinned delivery names the worktree, its dirty paths (or `no commit beyond base`)
   and the full next `xq.py verify ... --head` command.
   The review base is the assignment base, or the oldest base of earlier unpassed Codex attempts (open, or closed
   `dropped`/`superseded-by`) whose base is an ancestor of the new assignment base, whatever their worktree: a fix
   attempt inherits it (`assign --follows <id>`, or automatically with `NOTE review base inherited from <id>`), so
   the sensitive review also covers the earlier attempts' commits. When main is known (config `main` or
   origin/HEAD), the review also starts no later than `merge-base(head, main)`, so every commit not yet in main is
   reviewed; a new base already contained in main inherits nothing. `--follows` accepts a predecessor in another
   worktree when that worktree is gone or its base is an ancestor of the new HEAD.
   Codex tasks must not run in the primary checkout (`--force` records `primary_checkout` and reviews xq's own
   state there as untrusted) nor in a worktree another Codex task is running in; launch `<id>-fixN` only after
   `poll --id <id>` shows `<id>` terminal. `poll` re-reads the companion once (0.75 s) before reporting a dead
   worker `orphaned` or a status unavailable; `poll --wait` fails only after 3 consecutive unavailable reads.
   A legacy record without delivery provenance can be reconciled after reviewing the original result using
   `xq.py finish --id <id> --status done --force --head <reviewed-commit>`, then verified again.
   Finish/verify/merge enforce order; `--force` is for explicit reconciliation and does not validate the delivery.
7. With the user's merge authorization, merge the accepted commit `--no-ff` from a clean primary checkout.
   Record the resulting **merge** SHA: `xq.py merge --id <id> --commit "$(git rev-parse HEAD)" [--main "$branch"]`.
   `merge` finds the merge that brought the verified head in (on the first-parent history of `--commit`, so later
   unrelated merges do not count) and checks its merged parents that contain the verified head: commits they bring
   in that the verified head does not contain (a commit on `xq/<id>` after verify) are refused with their SHAs,
   subjects and any SENSITIVE paths, and the step `xq.py verify --id <id> ... --head <parent sha>` (plus
   `--ack-sensitive` when SENSITIVE paths are listed; no `--force`); `merge --force` records it with the same list as WARN (cloud
   report commits that only change `.agents/cloud-status/<cloud task>.json` print a WARN instead). A
   fast-forward prints `WARN ... commit(s) after verified head` only when such commits exist. A fix-loop merge does not
   warn about the earlier attempt closed as `superseded-by:<id>`.
   Without origin/HEAD, merge names the detected local branch and needs `--main <branch>` or config `{"main":"<branch>"}`.
   Preserve needed results, then `git worktree remove "$wt"` and `git branch -d xq/<id>`. Run relevant project checks.

Fix loop (`task --resume-last` works only within the same Claude session):
1. Write `.agents/briefs/<id>-fix1.md`.
2. Run `xq.py preflight --executor codex --worktree "$wt"` (the same worktree, so the stale-base check runs).
3. Then launch the -fix1 brief fresh in the same worktree (this or a later session; `--resume-last` only within the same
   session) using the normal `task --background --write --cwd "$wt" --prompt-file "$root/.agents/briefs/<id>-fix1.md"` command.
4. `xq.py assign --id <id>-fix1 --follows <id>` with the normal executor/brief/job/worktree fields (the same worktree;
   a different executor or worktree, or an unknown id, is refused).
5. `xq.py verify --id <id> --by <reader> --verdict fixes --evidence '<what failed>'`.
6. Only then `xq.py close --id <id> --reason superseded-by:<id>-fix1` (the target must already be in the ledger).
A retry needs fresh verification.

## Job state location

The companion keeps job state under `$CLAUDE_PLUGIN_DATA` when set (Claude Code sessions with the Codex plugin
may export it), otherwise under `$TMPDIR/codex-companion`. A job launched from Claude Code can therefore be invisible
to a plain shell. `assign --job` records `codex_state_root` when `CLAUDE_PLUGIN_DATA` names an existing directory.
`poll` tries the recorded root, then the current environment, then the TMPDIR fallback, using the first that knows
the job; only when all fail does it report `unavailable`, naming the roots it tried. Doctor prints the root the
companion will use and warns when `CLAUDE_PLUGIN_DATA` is unset.


## Launch preflight

xq never launches executors. The lead must run `xq.py preflight --executor codex|agy`
immediately before each launch, and wait/reconcile when it exits nonzero. Run it before
agy `queue run` or `delegate` too. For agy, run preflight before recording a running
assignment; otherwise that assignment itself uses a slot. Queued assignments use no slot.
Codex records its running assignment immediately after launch.

`.agents/config.json` accepts these defaults, alongside existing policy keys ([all keys](configuration.md)):
```json
{"limits":{"codex":2,"agy":1},"min_free_gb":5,"max_load_per_cpu":1.0}
```
`limits` accepts only codex/agy keys and nonnegative integers (0 disables launches).
`min_free_gb` is a finite nonnegative number of GiB; `max_load_per_cpu` is finite and
positive. Booleans and numeric strings are rejected. Omitted keys retain defaults.
Preflight refuses when 1-minute load / CPU count is above the maximum, repo-volume
or `tempfile.gettempdir()`-volume free space is below the minimum, or the selected
executor count reaches its limit. Windows skips load with a printed note; disk and
count checks still apply. Probe failures refuse launch. Doctor prints all readings.
The Stop hook prints refusal reasons instead of demanding work from an idle executor;
verification and stale-work checks still apply. Both preflight and hook are read-only
and take no ledger lock. This is a point-in-time check, not a slot reservation; the lead
must serialize launch decisions across sessions to avoid simultaneous-launch races.
The bridge drains all pending jobs when `queue run` starts. Confirm the whole pending set before launch.
See the [agy review recipe](agy.md#queued-reviews).

Counts use `max(ledger running tasks, matched live OS processes)` separately for each
kind, including closed tasks still marked running. OS counts are global, across repos.
POSIX uses `ps -axww -o stat=,ucomm=,args=` (with `LC_ALL=C`) and skips zombie, dead and exiting processes (state
`Z`, `X` or `E`, such as macOS `UE`); Windows uses PowerShell `Get-CimInstance Win32_Process`
for executable names and command lines. The name comes from the basename of argv[0], falling
back to `ucomm` (macOS `comm` can be a truncated path such as `/Users/me/.`).
Matching uses exact executable/script names:
`codex[.exe]` and `agy[.exe]` (excluding known auth/help/status commands and the shared
`codex app-server` broker), Node running `codex.js`, `codex-companion.mjs task|task-worker`
or the npm `codex` shim (same exclusions), and Python/py running `agy-bridge[.py] delegate`
or `queue run`. Shells, substring matches, bridge queue-add/status and companion status
are excluded. Wrappers and their live children can both count, deliberately erring on
the side of refusing another launch. Renamed executables and unrelated/custom launchers
may be missed; ledger counts cover recorded tasks. No executor is started or queried.
Tests inject `resource_readings(root)` and `live_process_counts()`; matcher tests inject
`process_rows()` pairs of `(executable_name, command_line)`. There is no environment
variable that disables the guard.

## Brief lint

`xq.py brief-lint --brief <path> [--enforce] [--json]` reads the brief (UTF-8, a leading BOM ignored) after removing
HTML comments and fenced code blocks (CommonMark: a fence closes only on the same character, at least as long, with no
info string, so a ```` block may contain ``` lines) and reports:

- ERROR: no or several `Result:` lines; a placeholder Result (`tbd`, `todo`, `none`, `n/a`, `-`, `...`); no or several
  `## Acceptance` sections; Acceptance not the last `#`/`##` section; a missing or placeholder `Files:`, `Checks:` or
  `Notes:` in it (each takes an inline value, a comma list, or a bullet list below it); a `Files:` entry that is not
  repo-relative (absolute, `~`, drive letter or `..`) or that matches every file (`*`, `**/*`); each configured restricted word/phrase on a line without the
  handoff marker; a configured header field (a `Key: value` line before the first `##` heading) that is missing or
  outside its allow-list; with `notes_require_file`, Notes naming no `.md`/`.txt` file.
- A `{a,b}` brace group in a comma list stays one `Files:` entry; the [declared-scope check](#declared-scope)
  expands it when matching.
- WARN: Result containing `;` or ` and also `, longer than `result_max_words`, or more than `max_steps` numbered steps
  outside Acceptance (split the task).

Exit 0 with warnings only, 1 on errors (and with `--enforce`, on warnings too), 2 on usage errors. `--json` prints
`{"version":1,"brief":...,"ok":...,"errors":[...],"warnings":[...]}`. Configure it in `.agents/config.json`
(all keys optional; generic defaults shown):

```json
{"brief_lint": {"mode": "warn", "restricted": [], "handoff_marker": "lead runs", "fields": {},
                "result_max_words": 45, "max_steps": 4, "notes_require_file": false}}
```

`restricted` lists project words such as `"network"` or `"git commit"` (matched case-insensitively as whole words);
a line naming one must also contain `handoff_marker` (for example "lead runs the network fetch"). `fields` maps a
header name to its allowed values, for example `{"Domain": ["api", "ui"]}`. `assign` lints the brief only when
`brief_lint` is present: `warn` prints `brief-lint: WARN ...` lines to stderr and never changes the exit code,
`enforce` refuses a brief with errors unless `--force` (recorded as `force` in the ledger), `off` is silent.
Without the key `assign` stays silent, so existing projects see no change; the command itself always lints.

## Declared scope

When a Codex task's brief declares `Files:` in its final `## Acceptance` (see [brief lint](#brief-lint)), xq compares
the delivery with it in code. The delivered paths are `git diff --name-only --no-renames <review base> <head>` over
the same range the SENSITIVE review uses (the recorded review base or the fork from main, whichever is older), started
instead at the current fork from the default branch when main was merged or rebased in mid-task (changes already on
main are not the delivery's), plus the worktree's uncommitted, index-hidden and untracked paths when the worktree
still exists (after it is removed, only the committed range). At verify, untracked paths past a clean delivery
commit (caches or build output from the lead's own checks) are left out, as `finish` already treats them; they
count only while the delivery is uncommitted and the verified head is the worktree HEAD. A path is in scope when one declared entry matches it:

- matching is `fnmatch` per `/`-separated segment and case-sensitive, like Git paths: `*`, `?` and `[...]` never
  cross `/` (`src/*.py` does not match `src/sub/a.py`);
- a `**` segment spans zero or more directories (`src/**/*.py`, `**/README.md`);
- `{a,b}` lists alternatives (`src/{a,b}.py`); a leading `./` is ignored;
- an entry ending in `/`, or one without `*`, `?` or `[`, also covers everything below it (`docs/`, `docs`).

When `poll` or `finish` records a Codex `done`, every out-of-scope path prints as
`WARN SCOPE <id>: <path> is outside the declared Files` (at most 10 lines, then a `+N more` line). `verify --verdict
pass` prints the same lines and records `out_of_scope` (up to 50 paths) in the verify event. Config:

```json
{"scope_check": "warn"}
```

`warn` (default) only reports; `enforce` refuses the pass while any path is out of scope unless
`--accept-scope "<reason>"`, recorded as `scope_accepted` next to `out_of_scope` (`--force` does not bypass it);
`off` checks nothing. A brief without `Files:` prints `WARN SCOPE <id>: no declared Files in the brief's ##
Acceptance; scope not checked` and is never refused. `--accept-scope` is refused on a non-pass verdict or with an
empty reason. Cloud tasks share one batch brief without per-task Acceptance, and agy has no delivered range, so
neither is checked (the adapter's `scope_paths` port op returns None).

## Stale base

`assign --worktree` (and `preflight --executor codex --worktree <wt>`) compares the worktree's HEAD with the default
branch offline (no fetch): config `main`, else `origin/HEAD`, else the primary checkout's current branch, each only
when it exists as a local branch. It tests the worktree's current HEAD, also when a queued entry pinned an older base,
so a rebase after `assign --queued` clears it on promotion. When that branch has commits the worktree HEAD does not contain, xq prints

```
WARN stale base: worktree <wt> HEAD <sha> is <N> commit(s) behind <main>; update it first: git -C <wt> rebase <main> (or git -C <wt> merge <main>)
```

Config `{"stale_base": "warn"}` (default) only warns; `"refuse"` makes assign fail with the same text unless
`--force`, and makes preflight exit 1 (`codex preflight refused: stale base: ...`); `"off"` skips the check. When no
default branch resolves (detached primary checkout, no `main`, no `origin/HEAD`), xq prints `NOTE stale-base check
skipped` and goes on. preflight `--worktree` must be an existing absolute directory of this repository and applies
only to executors whose delivery is a worktree commit.

## Context preamble

xq never launches executors, but it can build the prompt the lead launches. Configure project files to prepend:

```json
{"context_files": ["AGENTS.md", "docs/CONTEXT.md"]}
```

Entries must be repo-relative regular files: absolute paths, `..`, symlinks and non-files are refused, and a missing
file is an error naming it. `xq.py compose --brief .agents/briefs/<id>.md [--out <path>]` writes each context file
between `===== BEGIN xq context <path> sha256:<12 hex> =====` and `===== END xq context <path> =====` lines, then the
brief between the same `brief` delimiters, to `.agents/review/<brief stem>.prompt.md` (private, excluded by `init`)
and prints the path. `--out` never targets xq state or `.agents/config.json`, and overwrites an existing file only
when it is a `*.prompt.md`. compose writes nothing to the ledger; `assign --prompt <path>` records `prompt` and the full
`prompt_sha256` of the file actually launched, so the launched input stays traceable after the context files change.
