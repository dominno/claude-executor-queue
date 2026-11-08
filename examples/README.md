# Project templates

Adapt named files, checks and IDs before use; these examples do not launch jobs.

- `codex-task.md`: copy to `.agents/briefs/<id>.md`; dispatch from a fresh worktree.
- `agy-job.json`: name must match the xq assignment. Inspect `/agy:dry-run`'s
  manifest before sending the explicitly authorized nonconfidential files to Google.
- `cloud-batch.md`: copy to `.agents/briefs/<batch>.md`; exact `## <id>` sections
  must match `cloud-envelope --tasks`. Review all outgoing commits before each authorized push.
- `.agents/config.json`: opt-in keep_busy for Codex; merge with existing config.
- `settings.json`: macOS/Linux project `.claude/settings.json` Stop-hook snippet.
- `settings.windows.json`: Windows `cmd.exe` hook command using the Python launcher;
  if using a POSIX hook shell on Windows, adapt the command to that shell's syntax.

Ask the user before adding a Stop hook. Merge snippets into existing settings;
do not replace unrelated configuration. Custom installs must use the installed
script's actual absolute path. No backlog means pause/review config, not invented work.
