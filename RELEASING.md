# Releasing

1. Pick the version and set it in `VERSION` inside
   [`xq.py`](.claude/skills/executor-queue/scripts/xq.py) and on the `version:` line of
   [`SKILL.md`](.claude/skills/executor-queue/SKILL.md) (body, not frontmatter).
2. Rename the changelog's **Unreleased** heading to `## vX.Y — X.Y.Z — YYYY-MM-DD` and describe any upgrade steps.
   `tests/test_skill_layout.py` checks that all three agree.
3. Run the full suite, including the native Windows job and the Skills CLI smoke test in CI.
4. Install from a local clone into a scratch Git project and run the helper:
   `npx skills add <clone> --skill executor-queue -a claude-code -a codex -y`, then
   `python3 .claude/skills/executor-queue/scripts/xq.py init` and `doctor --offline`.
5. Before changing repository visibility, review Git history, release artifacts and GitHub Actions logs for private
   metadata and sensitive content.
6. After pushing, install with `npx skills add dominno/claude-executor-queue --skill executor-queue -a claude-code -a codex`,
   invoke `/executor-queue` in a new Claude Code session and `$executor-queue` in Codex, and confirm the helper path.
