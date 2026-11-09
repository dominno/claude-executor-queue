# agy review recipes

Use `xq.py init`, then `xq.py doctor --need agy`; see `codex.md` for plugin setup details.
`xq.py` means the installed helper; `$root` is the absolute primary checkout,
`$wt` its review worktree and `$base` the explicitly selected base branch.


Use the resolved `"$AGYB"` in every CLI invocation. Start with `/agy:dry-run` and read the manifest for **each new file set**.
Before delegate or queue add, record `xq.py assign --id <id> --executor agy --brief .agents/briefs/<id>.md`
(a queued assignment can be promoted after its matching pending job exists). Existing bridge names/artifacts refuse assignment; use a new id.
For a single review: `"$AGYB" delegate --repo "$root" --file A --file B --out .agy-bridge/results/<id>.md --cite -- '<task>'`.
agy cannot search unseen files. No confidential data, `.env`, credentials, `.claude/` or user media.

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

On Windows PowerShell doctor prints `$AGYB='...'` with escaped quotes. Use
`py -3 "$AGYB" queue --help` (and `py -3 "$AGYB"` before the commands below), since
an extensionless Python bridge is not a native executable. Doctor resolves the full agy/agy.cmd path.

Queue job schema (name must equal xq id and be unique across pending/archive/results):
```json
{"name":"review-01","task":"Check these sources","files":["src/parser.py","tests/parser.py"],"model":"gemini-3.8-flash-low","timeout":"5m","cite":true,"context":false,"need":false}
```
`name`, `task`, `files` are required; other fields are optional. Omit model to use agy's default.
`files` are explicit repo-relative regular paths, no globs, traversal or symlink components. `context` defaults to true
and silently adds configured `context_files`: set false for a bounded manifest. `need` stays false without explicit
permission to send additional files. Unknown fields refuse; exit 1 = usage/job error, 4 = rule/path/lock refusal.
Use `"$AGYB" queue add job.json --repo "$root"` then `"$AGYB" queue run --repo "$root"` once.
`queue watch` is allowed only on the user's explicit request; never start it to silence a hook.

Read the result header first: nonempty FILES NOT SENT or exit 15/16/17 means reject or a newly authorized rerun.
VERIFIED means only that a citation exists in an input, not that the conclusion is true. `xq.py agy-sync` reads
`.agy-bridge/results/` and `queue/done|failed`; missing/unrecognized headers (including plain delegate answer files) need manual finish/reconciliation.
Record actual review both in xq and `"$AGYB" verdict --repo "$root" --job <id> --verdict <v> --by <reader> --evidence '<checks>'`.
Ignore `.agy-bridge/` and scan for accidentally tracked results before every push.

