# agy review recipes

Run `xq.py init`, then `xq.py doctor --need agy`. The bridge requires Python 3.9+ on macOS or Linux.
`doctor --need agy` runs the installed bridge's diagnostics, including its network check for model access.
Use `xq.py doctor --need agy --offline` for local checks. A successful offline check confirms local setup
while sign-in and model access remain unverified.
See the [plugin setup instructions](codex.md#setup).
`xq.py` means the installed helper; `$root` is the absolute primary checkout,
`$wt` its review worktree and `$base` the explicitly selected base branch.


Use the resolved `"$AGYB"` in every CLI invocation. Start with `/agy:dry-run` and read the manifest for **each new file set**.
Immediately before each delegate or queue run, run `xq.py preflight --executor agy` and proceed only on exit 0.
Run preflight before recording a running assignment, so the new task does not consume its own last slot.
For a single review, record `xq.py assign --id <id> --executor agy --brief .agents/briefs/<id>.md`
after preflight and before running `delegate`. Use a fresh ID. Capture the delegate's exit code immediately, even when it fails.
```sh
xq.py preflight --executor agy || exit $?
# Record the running assignment here, after preflight and before delegate.
rc=0
"$AGYB" delegate --repo "$root" --file A --out "$root/.agy-bridge/results/<id>.md" --cite -- '<task>' || rc=$?
xq.py agy-sync --delegate-exit "<id>=$rc" --out "$root/.agy-bridge/results/<id>.md"
```
Use an absolute `--out` for both commands. A failed delegate can leave no result or archive;
a plain successful answer may lack bridge metadata. Ordinary sync cannot infer either outcome.
The explicit exit records done for 0 (requires a result file), failed otherwise (file may be absent),
without verification. Inspect the actual answer/failure, then independently verify or request a fresh review.
Delegate `--cite` annotates citations. Adding `--require-verified` makes unverified citations return exit 17.
The bridge stages the approved manifest. Run agy in a trusted environment because
staging does not isolate the process from the host. Exclude confidential data,
`.env`, credentials, `.claude/` and user media from the task and its inputs.

The bridge refuses worktree roots and out-of-repo inputs. For worktree review, export a diff inside the primary repo:
```sh
mkdir -p "$root/.agents/review"
git -C "$wt" add -N .
git -C "$wt" diff "$base" > "$root/.agents/review/<id>.diff"
git -C "$wt" status --porcelain
```
Compare every changed/untracked path in status with the diff before delegating with `--repo "$root"`.
Inspect the diff locally, including newly created files. Binary files need explicitly named review inputs;
ignored files are omitted and need separate inspection if in scope. `add -N` records intent only in this worktree.
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
`.agy-bridge/results/` and `queue/done|failed`; missing/unrecognized headers (including plain delegate answer files) need explicit `--delegate-exit` with absolute `--out`, or manual finish/reconciliation.
The bridge's failed archive can establish failure even for a refusal result without an exit header.
If a review was closed while running, sync can record its terminal result while preserving the closure.
Record actual review both in xq and `"$AGYB" verdict --repo "$root" --job <id> --verdict <v> --by <reader> --evidence '<checks>'`.
Ignore `.agy-bridge/` and scan for accidentally tracked results before every push.

