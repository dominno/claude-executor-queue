# executor-queue

[Skill instructions](SKILL.md) · [Installation](references/installation.md) · [Examples](assets/README.md)

This directory is the whole skill. `scripts/xq.py` is the queue helper (Python 3.8+, standard library only);
`references/` holds the executor guides that `SKILL.md` links to, and `assets/` holds brief, job, config and Stop-hook
templates. Nothing outside this directory is read at runtime.

Invoke it with `/executor-queue` in Claude Code or `$executor-queue` in Codex. To run the helper directly from the
project where the skill is installed:

```sh
python3 .claude/skills/executor-queue/scripts/xq.py init    # Codex-only install: .agents/skills/executor-queue
python3 .claude/skills/executor-queue/scripts/xq.py doctor --offline
python3 .claude/skills/executor-queue/scripts/xq.py status --open
```

| Guide | Covers |
| --- | --- |
| [Codex](references/codex.md) | setup, worktree tasks, preflight, brief lint, declared scope, stale base, context preamble |
| [agy-bridge](references/agy.md) | queued and delegated file reviews, manifests, exit codes |
| [Cloud batches](references/cloud.md) | Claude Code on the web: pushed briefs, envelopes and report collection |
| [Task status](references/job-status.md) | states, `status` filters and JSON, explicit refresh |
| [Configuration](references/configuration.md) | every `.agents/config.json` key |
| [Adopting](references/adopting.md) | projects with an existing ledger |

Released under the [MIT license](LICENSE).
