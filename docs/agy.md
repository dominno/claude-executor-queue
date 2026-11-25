# agy review recipes

Run `xq.py init`, then `xq.py doctor --need agy`. The bridge requires Python 3.9+ on macOS or Linux.
Use `xq.py poll --id <id>` to refresh one registered review from local artifacts. [Status and refresh commands](job-status.md)
`doctor --need agy` runs the installed bridge's diagnostics, including its network check for model access.
Use `xq.py doctor --need agy --offline` for local checks. A successful offline check confirms local setup
while sign-in and model access remain unverified.
See the [plugin setup instructions](codex.md#setup).
`xq.py` means the installed helper; `$root` is the absolute primary checkout,
`$wt` its review worktree and `$rb` the recorded review base SHA
(`rb=$(xq.py status --id <id> --field review_base)`, which falls back to `base`; never the moving branch).


Use the resolved `"$AGYB"` in every CLI invocation. Start with `/agy:dry-run` and read the manifest for **each new file set**.
Immediately before each delegate or queue run, run `xq.py preflight --executor agy` and proceed only on exit 0.
Run preflight before recording a running assignment, so the new task does not consume its own last slot.
For a single review, record `xq.py assign --id <id> --executor agy --brief .agents/briefs/<id>.md`
after preflight and before running `delegate`. Use a fresh ID. Capture the delegate's exit code immediately, even when it fails:
run delegate and the sync in ONE Bash call with timeout 600000 ms, and give delegate `--timeout 8m` so it ends first.
```sh
xq.py preflight --executor agy || exit $?
# Record the running assignment here, after preflight and before delegate.
"${AGYB:?}" delegate --repo "${root:?}" --timeout 8m --file A --out "${root:?}/.agy-bridge/delegate/<id>.md" --cite --require-verified -- '<task>'; rc=$?
python3 "${CLAUDE_SKILL_DIR}/scripts/xq.py" agy-sync --delegate-exit "<id>=$rc" --out "${root:?}/.agy-bridge/delegate/<id>.md"
```
If the tool killed the call, check `"${AGYB:?}" events --repo "${root:?}" --tail 10` before retrying. Until the exit is
recorded, `poll`/`agy-sync` on the running task print the `--delegate-exit` hint when `.agy-bridge/delegate/<id>.md` exists.
Use an absolute `--out` for both commands, and make this `--delegate-exit` sync the first sync after a delegate.
Plain delegate output goes to `.agy-bridge/delegate/`, a folder ordinary `agy-sync` never scans: a plain answer is
model text, and a line such as `exit_code: 0` inside it must never be read as bridge metadata: the exit comes only from
`--delegate-exit`. An answer may quote the bridge's v2 marker (a review of bridge code); `--delegate-exit` refuses only a file
that starts with the exact bridge result prefix, or a task with a queue archive. A failed delegate can leave no result or archive;
a plain successful answer may lack bridge metadata. Ordinary sync cannot infer either outcome.
The explicit exit records done for 0 (requires a result file), failed otherwise (file may be absent),
without verification. Inspect the actual answer/failure, then independently verify or request a fresh review.
Delegate `--cite` annotates citations. `--require-verified` (in the recipe, matching the queue's strictness) makes unverified citations return exit 17.
The bridge stages the approved manifest. Run agy in a trusted environment because
staging does not isolate the process from the host. Exclude confidential data,
`.env`, credentials, `.claude/` and user media from the task and its inputs.

The bridge refuses worktree roots and out-of-repo inputs. For worktree review, export a diff inside the primary repo
from the recorded review base (never the moving branch), then append each wanted untracked file:
```sh
f="$root/.agents/review/<id>.diff"
mkdir -p "${f%/*}"
git -C "$wt" diff "$rb" > "$f"
git -C "$wt" ls-files -o --exclude-standard
git -C "$wt" diff --no-index /dev/null -- <untracked file> >> "$f" || true
```
List untracked files with `ls-files -o` and leave out caches and build output. Do not use `git add -N`: it leaves
intent-to-add entries in the worktree index (if you used it, run `git -C "$wt" reset -q`).
Before delegating with `--repo "$root"`, compare `git -C "$wt" status --porcelain` with every path in the diff and
check `git -C "$wt" ls-files -v | grep -v '^H'`: skip-worktree and assume-unchanged edits are invisible to status.
Inspect the diff locally, including newly created files. Binary files need explicitly named review inputs;
ignored files are omitted and need separate inspection if in scope.
Review exports are local and git-excluded automatically.

## Queued reviews

Queue job schema (name must equal xq id and be unique across pending/archive/results):
```json
{"name":"review-01","task":"Check these sources","files":["src/parser.py","tests/parser.py"],"model":"gemini-3.8-flash-low","timeout":"5m","cite":true,"context":false,"need":false}
```
`name`, `task`, `files` are required; other fields are optional. Omit model to use agy's default.
`files` takes explicit repo-relative regular paths. Globs, traversal and symlink components are refused.
Set `context: false` for the exact manifest above. By default, a job also sends the bridge's configured
`context_files`. Leave `need: false` unless the user has specifically authorized additional files requested
by the model. Queue runs annotate citations for every job. With `cite: true`, unverified citations mean
exit 17. Unknown fields or invalid jobs yield exit 1, while rule, path or lock refusals yield exit 4.

1. Use `assign --queued` and `"$AGYB" queue add job.json --repo "$root"` to stage each job. Use the bridge's
   default queue and result directories so xq can reconcile the artifacts.
2. Inspect the entire pending set in `.agy-bridge/queue/`. Confirm that every `.job` matches a current xq
   assignment, an inspected manifest and the user's authorization. Coordinate with other producers to keep
   the set stable through launch. If an unrelated job is pending, use `delegate` for an independent review.
3. Run preflight, check that the batch fits the available xq capacity, promote every matching assignment and
   then run `"$AGYB" queue run --repo "$root"`. The bridge processes all pending jobs in filename order.
   xq assignment status has no effect on job selection.

The bounded xq flow uses `queue run`. A `queue watch` waits for newly added jobs and can start them without a
fresh xq preflight. Start a watcher only when the user has explicitly requested continuous processing and
every job admitted to that queue will be explicitly authorized.

Read the result header first: nonempty FILES NOT SENT or exit 15/16/17 means reject or a newly authorized rerun.
VERIFIED means only that a citation exists in an input, not that the conclusion is true. `xq.py agy-sync` reads
`.agy-bridge/results/` and `queue/done|failed`. A result counts as bridge output only when it is a complete
agy-bridge v2 queue result: it starts with `# <task id>`, the warning line and `<!-- agy-bridge-result-v2 -->`, its
header holds a top-level `exit_code:` line and at least one `### Attempt N (round N)` block before `## Round 1`, and
it ends with a `<!-- agy-bridge-result-sha256: <hex> -->` line matching the rest of the file (at most 8 MiB). A file
that carries the v2 marker but fails these checks is reported as PROBLEM. Any other file, including one with a
model-written `exit_code:` line or `## Answer` heading and legacy results without the v2 marker, is plain delegate output: sync observes
`unknown` with the hint `plain delegate output; record its exit with xq.py agy-sync --delegate-exit <id>=<code> --out <abs path>`
and never records a finish from it. Missing or unrecognized headers need explicit `--delegate-exit` with absolute
`--out`, or manual finish/reconciliation.
The bridge's failed archive can establish failure even for a refusal result without an exit header.
If a review was closed while running, sync can record its terminal result while preserving the closure.
Record actual review both in xq and `"${AGYB:?}" verdict --repo "${root:?}" --job <id> --verdict <v> --by <reader> --evidence '<checks>'`.
For a delegate review always pass `--results-dir .agy-bridge/delegate` to the bridge verdict (it works with every
bridge version).

To drop a queued job that has not run, cancel it in the bridge first, then retire the ledger entry:
`"${AGYB:?}" queue cancel <id> --repo "${root:?}" --reason dropped`, then `xq.py poll --id <id>`, then
`xq.py close --id <id> --reason dropped`. A failed archive whose result is a bridge refusal
(`<!-- agy-bridge-refusal-v1 sent: no -->`, or a legacy `# Queue refusal` result) is recorded as `failed` with
`not_sent: true` and the path under `refusal` (not `result`), so `close --reason dropped|superseded-by:X` needs no
verdict; `close` on a still-running task imports such a refusal itself. `sent: maybe` refusals keep `result` and need
a verdict. A failed plain delegate whose `--out` file does not exist records no `result` either.
`close` refuses while a regular `.agy-bridge/queue/<id>.job` is pending (the next queue run would still send it) and
prints the cancel command (or, when `queue cancel --help` fails, the manual removal plus `close --force`);
`close --force` closes anyway with a WARN.
With an active runner or watcher, `queue add` sends the job immediately: `assign --queued` and `preflight --executor
agy` print a NOTE; run preflight and promote before `queue add`. When the watcher already ran a queued task (only its
own `queue/done|failed/<id>.job` and `results/<id>.md` exist), promotion imports the result in place
(`ran_before_promote: true`) with a WARN that preflight was bypassed, instead of demanding a fresh id.
`doctor --need agy` also probes `queue cancel --help` and `queue watch --help` (`--detach`), naming the recipe step a
missing feature breaks.
Ignore `.agy-bridge/` and scan for accidentally tracked results before every push.

