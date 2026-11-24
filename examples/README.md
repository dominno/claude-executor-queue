# Project templates

Adapt named files, checks and IDs before use; these examples do not launch jobs.

- `codex-task.md`: copy to `.agents/briefs/<id>.md`; dispatch from a fresh worktree.
- `agy-job.json`: name must match the xq assignment. Inspect `/agy:dry-run`'s
  manifest before sending the explicitly authorized nonconfidential files to Google.
- `cloud-batch.md`: copy to `.agents/briefs/<batch>.md`; exact `## <id>` sections
  must match `cloud-envelope --tasks`. Review all outgoing commits before each authorized push.
- `.agents/config.json`: opt-in keep_busy for Codex; merge with existing config.
- `settings.json`: macOS/Linux project `.claude/settings.json` Stop-hook snippet.
- `settings.windows.json`: Windows Git Bash hook command using `py -3` and a missing-script guard.

Ask the user before adding a Stop hook. Merge snippets into existing settings;
do not replace unrelated configuration. The snippets assume a user-level installation.
For a project-scoped or custom install, set `f` to the actual helper path.
If the backlog is empty, pause or review the configuration.

Both Stop commands check for the script before launching it, so cloud clones, teammates and uninstalled
copies can stop normally. With a user-level installation, PowerShell runs the
ledger via `py -3 "$HOME/.claude/skills/executor-queue/scripts/xq.py"` or `python`
with the same path. The agy bridge requires macOS or Linux.
Run init before doctor. Config is trackable policy; ledger/lock/review/config temp files stay local.
For long work use `ack --id parser-empty-01 --hours 12`, then poll/check actual progress. After a verified
fix merges, close its parent with `close --id parser-empty-01 --reason superseded-by:parser-empty-01-fix1`.
