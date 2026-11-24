# executor-queue

**Track work you delegate from Claude Code.** Use Codex for code changes, agy for a review of selected files, or Claude Code on the web for a task based on a pushed brief. The `xq` helper keeps a local task log of assignments, results and verification.

If your project already has a queue, the skill follows that project's workflow.

## Quickstart in a new project

Start in the root of a Git checkout with an initial commit. You need Claude Code, Python 3.8+, Git 2.31+, and Node.js 22.20+ with npm for Skills CLI.

### Install with Skills CLI

```sh
npx skills add dominno/claude-executor-queue --skill executor-queue -a claude-code
```

Start a new Claude Code session in that checkout. Invoke the skill manually with a local setup request.

```text
/executor-queue Initialize this project's task ledger and show open tasks.
```

A fresh ledger reports `no tasks`. Once you see it, choose an executor below and try one small task. Add `-g` to the install command to make the skill available across projects. For authenticated repositories, give Git read access or replace the GitHub name with the path to your local clone. [Direct CLI and PowerShell commands](docs/installation.md#run-xq-directly) · [Updates and removal](docs/installation.md#update-or-remove-a-skills-cli-installation)

### Offline installation

The repository includes installers for macOS and Linux (`install.sh`) and Windows PowerShell (`install.ps1`). The guide covers project and user destinations, removing a copy, and moving an older installation to a custom Claude Code profile. [Offline installation guide](docs/installation.md#offline-installation)

## How it works

![You invoke the executor-queue skill in Claude Code. The executor handles the work. xq reads recorded status and imports authorized results into a local ledger shared by Git worktrees.](docs/images/architecture.svg)

The skill coordinates the work you request. The executor produces the result. `xq` records the evidence on demand in `.agents/ledger.jsonl`, an append-only log in the main checkout. A separate Git worktree shares that log. [Using the helper from a worktree](docs/installation.md#run-xq-directly)

## Choose an executor

![Codex works in a Git worktree from a task brief. agy-bridge reviews approved files. Claude Code on the web works from a pushed brief. Refresh the xq ledger explicitly with poll, agy-sync or cloud-collect.](docs/images/executor-paths.svg)

| If you need | Executor | Before you start |
| --- | --- | --- |
| A code change in a separate Git worktree | [Codex](docs/codex.md) | Node.js, an authenticated Codex CLI and the Claude Code Codex plugin. |
| A review of specific files | [agy-bridge](docs/agy.md) | macOS or Linux, Python 3.9+, an authenticated Google Antigravity CLI and the [agy-bridge plugin](https://github.com/dominno/claude-agy-bridge) with queue and verdict support. |
| A task in a remote Claude Code session | [Claude Code on the web](docs/cloud.md) | A connected repository, a reviewed brief pushed to the approved branch, and the required build tools in the remote environment. |

Ask the skill to check local prerequisites for the executor you choose. If you run the installed helper directly, use `python3 "$xq" doctor --offline --need codex`, replacing `codex` with `agy` or `cloud` as needed. Run preflight immediately before launching Codex or agy. [Helper path](docs/installation.md#run-xq-directly) · [Preflight commands and limits](docs/codex.md#launch-preflight)

## Follow one task

![Authorize the scope and write a brief, start the executor and record the assignment, collect the actual result, then verify independently. Integration is optional and requires approval.](docs/images/task-lifecycle.svg)

A **brief** describes the goal, allowed files and checks. A **queued** task is waiting for dispatch. After the executor returns, inspect the full delivery before applying changes or running tests, then record the verdict with `verify`. For Codex, verify the reviewed commit before integrating it. [Example briefs, jobs and optional Stop hooks](examples/)

## Check progress

Ask `/executor-queue` to show open tasks, or run `python3 "$xq" status --open`. Filter by task ID, executor or recorded state and add `--json` for scripting. To update the ledger, request an explicit refresh with `poll` for Codex or agy, or use `cloud-collect` for reports published by Claude Code on the web. [Helper path](docs/installation.md#run-xq-directly) · [Status filters, JSON and refresh commands](docs/job-status.md)

## What gets shared

- **Local state.** Keep the ledger, lock, review exports and `.agy-bridge/` output out of Git. `xq init` adds local exclusions. Review `.agents/config.json` before tracking it. [Local configuration and preflight](docs/codex.md#launch-preflight)
- **Codex.** Use a separate worktree and keep sensitive files out of scope. The brief's read boundary relies on instructions. [Worktree setup](docs/codex.md#tasks)
- **agy.** The bridge sends the approved nonconfidential files and task to Google. Inspect the manifest before sending and use a trusted host. [Manifest and result checks](docs/agy.md)
- **Web sessions.** Track the brief and result status files in Git. Review outgoing commits and confirm the push destination before authorizing publication. [Web session setup and push scope](docs/cloud.md)

## Contributing and tests

The skill installs three files from [`skills/executor-queue/`](skills/executor-queue/). Keep the helper compatible with Python 3.8 and the standard library. Run the offline test suite from this repository with `pytest` and Git available.

```sh
python3 -m pytest -q tests
git diff --check
```

See the [changelog](CHANGELOG.md) and [3.6 release notes](docs/v3.6-notes.md) for version changes. The project is released under the [MIT license](LICENSE). Use the [security policy](SECURITY.md) to report a vulnerability.
