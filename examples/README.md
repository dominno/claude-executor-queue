# Project templates

Adapt named files, checks and IDs before use; these examples do not launch jobs.

- `codex-task.md`: copy to `.agents/briefs/<id>.md`; dispatch from a fresh worktree.
- `agy-job.json`: name must match the xq assignment. Inspect `/agy:dry-run`'s
  manifest before sending the explicitly authorized nonconfidential files to Google.
- `cloud-batch.md`: copy to `.agents/briefs/<batch>.md`; exact `## <id>` sections
  must match `cloud-envelope --tasks`. Review all outgoing commits before each authorized push.
- `.agents/config.json`: opt-in keep_busy for Codex; merge with existing config.
- `settings.json`: macOS/Linux Stop-hook snippet using `python3`.
- `settings.windows.json`: Windows Stop-hook snippet for Claude Code with Git Bash using `py -3`.

Ask the user before adding a Stop hook. Merge the snippet into the existing
project `.claude/settings.json`. Both snippets find the project-scoped skill using
`$CLAUDE_PROJECT_DIR` and skip the hook when its helper is absent.
When you start a new Claude Code session in a linked worktree, the project path
can point to that worktree. To run the hook there, make its settings available
in that checkout and install the skill there or configure the hook to use your
globally installed helper. Both installations read the shared project ledger.

For a global install, set `f` to the helper path under your active Claude Code
profile, usually `$HOME/.claude/skills/executor-queue/scripts/xq.py`. If your path
or interpreter is specific to your machine, put the hook in `.claude/settings.local.json`.
On Windows confirm Git Bash can run `py -3 --version`. If it uses `python` or
`python3`, confirm that command reports Python 3.8+ and replace `exec py -3` in
the snippet with `exec python` or `exec python3`. Leave the command in Bash syntax
even when you use PowerShell to install the skill. Follow the
[PowerShell commands](../docs/installation.md#run-xq-directly) for direct calls.
The agy bridge requires macOS or Linux.

If the backlog is empty, pause or review the configuration.
Run init before doctor. Config is trackable policy; ledger/lock/review/config temp files stay local.
For long work use `ack --id parser-empty-01 --hours 12`, then poll/check actual progress. After a verified
fix merges, close its parent with `close --id parser-empty-01 --reason superseded-by:parser-empty-01-fix1`.
