# Installation and maintenance

Install with the [Skills CLI](https://github.com/vercel-labs/skills). Run the command from the Git project where you want to use the skill. Node.js is needed only for the installer.

```sh
npx skills add dominno/claude-executor-queue --skill executor-queue -a claude-code -a codex
```

- Keep only the `-a` option for the agent you use. With both agents, the CLI keeps one copy in `.agents/skills/executor-queue` (read by Codex) and links `.claude/skills/executor-queue` to it for Claude Code. Add `--copy` to give each agent its own copy instead of a symlink.
- Add `-g` to install for your user account (`~/.claude/skills/executor-queue` and `~/.agents/skills/executor-queue`) instead of the current project.
- Add `-y` to skip the prompts, for example in scripts.
- Without network access to GitHub, or to try a local change, replace `dominno/claude-executor-queue` with the absolute path of a clone.

The whole skill directory is installed: `SKILL.md`, `scripts/xq.py`, `references/`, `assets/` and `LICENSE`. The helper needs only `scripts/xq.py`; nothing outside the skill directory is read at runtime.

Invoke the skill with `/executor-queue` in Claude Code or `$executor-queue` in Codex. It runs only when invoked explicitly.

## Run xq directly

The helper uses Python 3.8+ and Git 2.31+. Run these commands from the checkout where you installed the project skill. For a Codex-only install use `.agents/skills/executor-queue` in place of `.claude/skills/executor-queue`.

```sh
if project=$(git rev-parse --show-toplevel); then
  xq="$project/.claude/skills/executor-queue/scripts/xq.py"
  python3 "$xq" --version
  python3 "$xq" init
  python3 "$xq" status --open
fi
```

In Windows PowerShell, use `py -3` or replace it with `python` or `python3` if that is your Python 3.8+ command.

```powershell
$project = git rev-parse --show-toplevel
if ($LASTEXITCODE -eq 0 -and $project) {
    $xq = Join-Path $project '.claude/skills/executor-queue/scripts/xq.py'
    py -3 $xq --version
    py -3 $xq init
    py -3 $xq status --open
}
```

The version check reads the installed helper. `init` sets up the `.agents/` directory and local Git exclusions. `status --open` prints `no tasks` for a fresh ledger. [Status filters and refresh commands](job-status.md)

For a user-level install (`-g`), use `$HOME/.claude/skills/executor-queue/scripts/xq.py` (Claude Code) or `$HOME/.agents/skills/executor-queue/scripts/xq.py` (Codex).

For a Claude Code session opened in a linked worktree, install the skill in that worktree or use a user-level installation. Direct Python calls can use the absolute helper path from any installation. xq locates the shared ledger in the main checkout. Use `XQ_ROOT` to override the ledger root explicitly. Match optional Stop hooks to the helper and interpreter in use. [Hook examples](../assets/README.md)

For cloud work, use `init` so the brief and status files can be tracked. `init --local` excludes them for local-only work. Use xq 3.4 or newer in every session that shares a ledger with multi-task records. [Cloud setup](cloud.md)

## Update or remove a Skills CLI installation

- For an install from GitHub, run `npx skills update` (add `-g` for a global install).
- For an install from a local clone, update the clone and repeat the same `npx skills add` command with its path and original scope.
- To remove the skill, run `npx skills remove executor-queue`. Add `-g` if you installed it globally.

The task ledger and optional project hooks stay in the project after skill removal.

### After updating

A newer helper can add private files (for example `.agents/hook-idle.stamp`, new in 3.7.0) that an older `init` did
not exclude from Git. After every update, run from the primary checkout:

```sh
python3 "$xq" init      # idempotent: adds only the missing exclusions
python3 "$xq" doctor    # every private path should print OK ... ignored and untracked
```

Until `init` runs again, `doctor` prints `MISSING <path>: not ignored (new in <version>; run xq init after updating)`
for such a path.

## Moving from the old installer

Versions up to 3.6 shipped `install.sh` and `install.ps1`, which copied three files into `~/.claude/skills/executor-queue` (or `CLAUDE_SKILLS_DIR`) and wrote `.executor-queue-install.json` beside it. Those installers are gone. Back up any local edits, delete that directory and its `.executor-queue-install.json`, then install with the Skills CLI. Update any Stop hook that points to the previous helper path.
