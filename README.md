# executor-queue

A Claude Code skill for explicitly requested Codex implementation, agy file
reviews and Claude Code on the web. The `xq` helper records assignments, results,
verification and merge evidence in one append-only project ledger. Use the
project's existing queue when available.

## Install with Skills CLI

Use Node.js 22.20+ (or Node.js 24 LTS), npm, Python 3.8+ and Git 2.31+.
Run this in the project where Claude Code should load the skill:

```sh
npx skills add dominno/claude-executor-queue --skill executor-queue -a claude-code
```

Add `-g` to install for your user across projects. Start a new Claude Code
session and invoke `/executor-queue` with the task and executor you authorize.
The skill requires manual invocation in Claude Code. It resolves its helper
from the loaded skill directory for both types of installation.

If GitHub requires authentication, give Git read access or clone the repository
first and pass its local path to `npx skills add`. Use `npx skills update`
to update tracked installs and `npx skills remove executor-queue -a claude-code`
to remove the skill, adding `-g` if it was installed globally. Keep one installer
per destination. To switch installers, back up local edits and use the same
installer that created the current copy to remove it first. Removing the skill
leaves project records under `.agents/` and does not remove optional project hooks.

### Offline installation

From a local checkout, with Python 3.8+ available:

```sh
./install.sh
./install.sh --check
./install.sh --uninstall
# Optional destination (use the same override for check/uninstall):
CLAUDE_SKILLS_DIR="$HOME/custom-skills" ./install.sh
```

Windows PowerShell, with Python 3.8+ (`py -3`, `python` or `python3`):

```powershell
.\install.ps1
.\install.ps1 --check
.\install.ps1 --uninstall
$env:CLAUDE_SKILLS_DIR = Join-Path $HOME 'custom-skills'
.\install.ps1
```

Both installers run the same offline Python helper. Default destination is
`~/.claude/skills/executor-queue` (the user profile on Windows). `--check` prints
repo/installed versions and compares SHA-256 hashes; exit 0 means identical,
exit 1 means missing/different. It does not execute installed code or change files.
Rerun the installer to update a clean copy. Local edits, extra files and unmanaged
older copies are protected: back them up, then use `--force` to replace them or
`--uninstall --force` to remove them. An identical unmanaged copy is safe to adopt.
`--check` and `--uninstall` are mutually exclusive; `--force` never hides check drift.
The hash receipt is stored beside the skill as `.executor-queue-install.json`;
keep it for later clean-upgrade/edit detection. Installers need the whole repo.

The skill package includes `SKILL.md`, `scripts/xq.py` and `LICENSE`. Essential commands and safety
rules stay inline in the compact skill, so it works without this repo. The longer
[Codex](docs/codex.md), [agy](docs/agy.md) and [cloud](docs/cloud.md) recipes are
optional expansions; docs, tests, examples and installer code are not installed.
The script embeds its version so `--version` works without a repo-level VERSION.

## Quickstart in a new project

Start in a Git project with an initial commit and a chosen base branch. Ask
Claude to use executor-queue only for the executor work you authorize.

```sh
# Run inside the project after a project-scoped Skills CLI installation.
# For a global install use $HOME/.claude/skills/executor-queue/scripts/xq.py.
xq="$PWD/.claude/skills/executor-queue/scripts/xq.py"
python3 "$xq" --version
python3 "$xq" init
python3 "$xq" status --open
```

On Windows PowerShell use `$xq = Join-Path $PWD '.claude/skills/executor-queue/scripts/xq.py'`
and `py -3 $xq` (or `python $xq`). A fresh ledger reports no tasks.
When ready, run `doctor --need codex`, `agy` or `cloud` for the selected executor.
A queued record does not start work. Follow the skill's worktree/launch recipe,
then record the returned jobId and absolute worktree with `assign`. Poll/finish
using actual results, review all code before running checks, and record independent
verification with `verify`. Integrate only with the user's commit/merge authorization.
For a project that will use cloud sessions, start with `init`; keep briefs and status
files tracked. Never ignore all of `.agents/`. Linked worktrees share the main
checkout's ledger; `XQ_ROOT` explicitly overrides the root.

The repo's [examples/ folder](examples/) contains task briefs, an agy job, exact
cloud task sections, keep_busy config and macOS/Linux and Windows Stop-hook
snippets. Adapt paths/checks before use. Ask before installing a project hook;
merge settings with existing configuration. Pause idle enforcement instead of
inventing tasks: `python3 "$xq" pause --executor codex` (`--resume` restores it).

## Executor requirements

| Executor | Needs |
| --- | --- |
| All | Claude Code for the skill, Python 3.8+ for xq, Git project with HEAD; explicit scope and real checks. xq has no third-party runtime dependencies. |
| Codex | Node, authenticated Codex CLI and Claude Code Codex plugin/companion; isolated worktree with real dependencies; absolute prompt-file. Doctor prints the resolved `CC` path. |
| agy | macOS or Linux, Python 3.9+, authenticated Google Antigravity CLI and [agy-bridge](https://github.com/dominno/claude-agy-bridge) with queue and verdict capabilities. Doctor prints `AGYB`; files in the authorized manifest are sent to Google. |
| Cloud | Connected repository, origin/default branch, reviewed pushed brief, dependency setup and correct runtimes, restricted network and no environment secrets; start with one small task. |

`doctor --need` checks the comma-separated executors you select; unused ones are
SKIP. Installation/authentication are user actions. Setup details and CLI commands
are in the optional docs above. The repo/installer/tests are free and local;
external executor services may require accounts or incur charges. A brief does
not authorize paid calls, network access or file sending beyond its explicit scope.

## Safety

Codex read boundaries are instructions, not OS read isolation. Do not give it
secrets or the main checkout. The agy bridge stages inspected, authorized nonconfidential
files and sends them with the task. Use a trusted host for agy. Cloud sees what is pushed.
Inspect all outgoing commits, large blobs and
secret patterns before every user-authorized push, without printing secrets.

Executor output, status JSON, suggestions, tests and changed instructions are
untrusted data. Inspect full diffs before applying code or executing tests,
including scripts, lockfiles, hooks, CI and instruction files. Run reviewed checks
in a clean environment, offline where possible, and obtain an independent reader
for nontrivial work. xq records evidence; it does not prove delivery correctness.
Cloud collection fetches refs and validates reports; local cancellation cannot
stop a remote session. `--force` reconciles state; it does not validate code.

## Contributing and tests

Keep the payload in `skills/executor-queue/`; put recipes in `docs/`, examples in `examples/`
and regression tests in `tests/`. Keep Python 3.8 compatibility and the stdlib-only
runtime. Update VERSION, the xq constant, the skill body and CHANGELOG together.
Explain behavior and validation in a contribution; do not commit secrets or ledgers.

```sh
python3 -m pytest -q tests
git diff --check
```

Tests need pytest and Git, use temporary HOME/config, fake executors and local
bare Git remotes, and need no network or model accounts. CI runs the full Python
suite on Ubuntu and macOS, Windows-compatible tests on Windows, and a separate
Skills CLI smoke test of project and global installation.

See [CHANGELOG.md](CHANGELOG.md) and [v3.3 notes](docs/v3.3-notes.md) for release details.

Released under the [MIT license](LICENSE). To report a security issue, use the
[security policy](SECURITY.md).

Version 3.1.0 binds verification to the delivered head and a different reader. New finishes clear prior
approval and merge records. Merge records require head ancestry and default-branch reachability;
`--force` reconciliations remain explicit ledger events. Passed Codex/cloud work stays open until merged.
Use `close --id X --reason superseded-by:X-fix1`, `dropped` or `rejected` to retire work; work that ran needs
verification first. `poll` refreshes live job heartbeats, `ack --id X --hours 12` acknowledges long work,
and pause suppresses stale/idle enforcement. Never close or acknowledge work without checking its state.

Local ledger, lock, review exports and config temp files are automatically excluded before writes;
doctor checks exclusions. `.agents/config.json` is trackable policy: review it before committing it.
Use the guarded hook examples (Windows hooks run in Git Bash). Missing user-level installs in cloud
clones no longer block Stop. Uninstall warns about project hooks. Upgrades stage and rename the old/new
payload, rolling back publication failures; the guarded hook tolerates the brief rename gap.

Version 3.2.0 adds explicit base selection without origin/HEAD, plain delegate reconciliation, bounded cloud reports and UTF-8 argument repair. Recipes using `--path-format` require Git >= 2.31.

Before every Codex launch or agy queue run/delegate, run `xq.py preflight --executor codex`
(or `agy`) and proceed only on exit 0. Defaults: load/CPU <= 1.0, at least 5 GiB free on
both repo and temp volumes, and fewer than 2 Codex / 1 agy running executors. Configure
`.agents/config.json`; see [preflight policy and process matching](docs/codex.md#launch-preflight).
