# Installation and maintenance

[Start with the Skills CLI quickstart](../README.md#quickstart-in-a-new-project) to install and try the skill in a Git project. The installed files are `SKILL.md`, `scripts/xq.py` and `LICENSE`.

## Run xq directly

The helper uses Python 3.8+ and Git 2.31+. Run these commands from the checkout where you installed the project skill.

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

The version check reads the installed helper. `init` sets up the `.agents/` directory and local Git exclusions. `status --open` prints `no tasks` for a fresh ledger.

For a user-level install, use the script under your active Claude Code profile. The default path is `$HOME/.claude/skills/executor-queue/scripts/xq.py`. When `CLAUDE_CONFIG_DIR` selects another profile, use that directory in place of `$HOME/.claude`.

For a Claude Code session opened in a linked worktree, install the skill in that worktree or use a user-level installation. Direct Python calls can use the absolute helper path from any installation. xq locates the shared ledger in the main checkout. Use `XQ_ROOT` to override the ledger root explicitly. Match optional Stop hooks to the helper and interpreter in use. [Hook examples](../examples/README.md)

For cloud work, use `init` so the brief and status files can be tracked. `init --local` excludes them for local-only work. Use xq 3.4 or newer in every session that shares a ledger with multi-task records. [Cloud setup](cloud.md)

## Update or remove a Skills CLI installation

- For an install from GitHub, run `npx skills update`.
- For an install from a local clone, update the clone and repeat the same `npx skills add` command with its path and original scope.
- To remove the skill, run `npx skills remove executor-queue -a claude-code`. Add `-g` if you installed it globally.

Choose one installer per destination. Back up local edits and remove the old copy with its original installer before switching. The task ledger and optional project hooks stay in the project after skill removal.

## Offline installation

Run the wrapper from a clone of this repository with Python 3.8+ available.

```sh
./install.sh
./install.sh --check
./install.sh --uninstall
```

Use PowerShell on Windows.

```powershell
.\install.ps1
.\install.ps1 --check
.\install.ps1 --uninstall
```

The offline installer appends `executor-queue` to the first destination available in this list.

1. `CLAUDE_SKILLS_DIR`
2. `$CLAUDE_CONFIG_DIR/skills`
3. `~/.claude/skills`

`CLAUDE_SKILLS_DIR` selects the copy destination. Use a directory that Claude Code scans for skills. For an offline project install, run from the target Git checkout and use the same destination for install, `--check` and `--uninstall`.

```sh
if project=$(git rev-parse --show-toplevel); then
  CLAUDE_SKILLS_DIR="$project/.claude/skills" "/path/to/claude-executor-queue/install.sh"
fi
```

```powershell
$project = git rev-parse --show-toplevel
if ($LASTEXITCODE -eq 0 -and $project) {
    $env:CLAUDE_SKILLS_DIR = Join-Path $project '.claude/skills'
    & 'C:\path\to\claude-executor-queue\install.ps1'
}
```

### Check, update and recover

`--check` reads versions and compares SHA-256 hashes. Exit 0 means the installed files match the repository. Exit 1 reports a missing or different copy, a busy destination or an interrupted transaction. `--check` is read-only, and its result includes local drift even with `--force`.

Rerun the installer to update a clean copy. Back up local edits, extra files or unmanaged older copies before using `--force` to replace them or `--uninstall --force` to remove them. An unmanaged copy can be adopted if it matches the repository. Keep `.executor-queue-install.json` beside the installed skill. It stores the hashes used to detect edits during upgrades.

Following an interrupted installer process, the next install or uninstall attempts recovery and prints the path to saved data if it needs manual intervention. After a power loss or OS crash, recovery depends on the transaction files the filesystem preserved. Finish all installer commands from older checkouts before using a 3.4+ installer at the same destination. The offline installers use the full repository checkout.

### Move a 3.3 installation to a custom profile

Version 3.3 with no `CLAUDE_SKILLS_DIR` override installed into `~/.claude/skills`, including when `CLAUDE_CONFIG_DIR` was set. Check the old destination explicitly before switching.

```sh
CLAUDE_SKILLS_DIR="$HOME/.claude/skills" ./install.sh --check
```

In PowerShell, set `$env:CLAUDE_SKILLS_DIR = Join-Path $HOME '.claude/skills'` before running `.\install.ps1 --check`. A clean older copy can report a mismatch against the new source. Back up any edits and use the same override with `--uninstall` to remove the old copy. Clear the temporary override before installing into `$CLAUDE_CONFIG_DIR/skills`. Update any hook that points to the previous helper. To keep using the old destination, continue passing the explicit override.
