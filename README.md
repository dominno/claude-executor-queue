# executor-queue — a Claude Code skill for Codex, agy and cloud-session work

A user-level [Claude Code](https://claude.com/claude-code) skill that lets Claude hand bounded work to helper executors and keep one
append-only ledger per project:

- **Codex** (OpenAI) through the Codex plugin, in an isolated git worktree;
- **agy** (Google Antigravity CLI) through the [agy-bridge](https://github.com/dominno/claude-agy-bridge) plugin, for read-only reviews of named files;
- **cloud Claude sessions** (claude.ai/code), started by a pasted `/goal` batch and collected from their pushed session branch.

The skill folder contains only `SKILL.md` and one standard-library Python script (`scripts/xq.py`). Tests and docs live outside it.

## Install
```sh
git clone <this repo> && cd claude-executor-queue && ./install.sh
```
Then, in any project: ask Claude to "use executor-queue", or run `python3 ~/.claude/skills/executor-queue/scripts/xq.py doctor`.
`doctor` checks agy, the agy-bridge plugin, the Codex CLI and plugin, logins and the git setup, and prints how to fix what is missing.

## Layout
```
skill/executor-queue/SKILL.md          the skill (what Claude reads)
skill/executor-queue/scripts/xq.py     ledger, doctor, Stop hook, cloud envelope/collect (Python 3.8+, stdlib only)
tests/                                 pytest suites (temp git repos, fake agy/codex binaries); not installed
docs/                                  design review and release notes
install.sh                             copies the skill into ~/.claude/skills/
```

## Test
```sh
python3 -m pytest -q tests
```

## Safety
Files you give agy are sent to Google; cloud sessions see only what you push. The skill never stores secrets, never pushes or merges
without the user's consent, and treats every executor's output as untrusted data.
