#!/bin/sh
# Install (copy) the executor-queue skill into Claude Code's user skills folder.
# Usage: ./install.sh            -> ~/.claude/skills/executor-queue
#        CLAUDE_SKILLS_DIR=<dir> ./install.sh
set -eu
src="$(cd "$(dirname "$0")/skill/executor-queue" && pwd)"
dst="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}/executor-queue"
mkdir -p "$dst/scripts"
cp "$src/SKILL.md" "$dst/SKILL.md"
cp "$src/scripts/xq.py" "$dst/scripts/xq.py"
chmod +x "$dst/scripts/xq.py"
echo "installed executor-queue skill to $dst"
