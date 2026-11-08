# v3 delivery — 2026-11-08

Changes are confined to this worktree. No network requests, live executor calls,
user-level installation, project commits or pushes were performed. Test Git
commits/pushes are disposable fixture operations against local bare remotes.
The pre-existing untracked `.agents-brief-v3.md` was left unchanged.

## Packaging and release markers

Current version is `3.0.0`: root VERSION, embedded xq VERSION/`--version`, and a
`version:` line in the SKILL.md body. Frontmatter keeps only the existing name
and description; no custom Claude Code metadata field is introduced. The script
embeds its version because a standalone install has no repo-level VERSION file.
Tests check the three markers together. CHANGELOG records v1/v2 on 2026-11-08,
all 25 review items (including rejected item 25), and the v3 packaging work.
Historical version numbers label iterations; they do not claim historical tags.

SKILL.md is 101 lines. Essential decision rules, safety constraints and operational
commands remain inline; long existing recipes were moved to docs/codex.md,
docs/agy.md and docs/cloud.md. Repo docs are explicitly optional expansions.
This chooses the brief's inline-command alternative: the installed skill remains
self-contained with exactly SKILL.md and scripts/xq.py, without reference/ or a
checkout dependency. Examples live only at repo level, including the hidden
examples/.agents/config.json and two platform-specific Stop-hook snippets.

## Installers

install.sh and install.ps1 forward the same flags to scripts/install.py (stdlib,
Python 3.8+). Sharing the implementation prevents platform behavior drift and
reuses the runtime already required by xq. Neither installer downloads anything.

- Install/check/uninstall support CLAUDE_SKILLS_DIR; default is the user's Claude
  skills directory. Paths with spaces are tested.
- --check compares SHA-256 file hashes and directory entries with the repo,
  reports both versions and drift paths, and changes nothing. It never executes
  potentially modified installed Python. Missing/different returns 1; equal is 0.
- A hash receipt beside the skill records the last installed payload. A clean
  older install can update or uninstall even when the repo has changed.
- Changed/missing files, extra files/directories and symlinks count as local
  changes. Install/uninstall refuse them unless --force explicitly discards them.
  Missing/invalid receipts protect unmanaged differing copies; identical copies
  can be adopted. A symlink destination/receipt is refused even with --force.
- Only the two payload files are copied, excluding test-generated source caches.
  Replacement is staged before the old directory is removed; this is not a
  crash-atomic multi-file transaction. Keep backups of valuable customizations.

README covers install/check/uninstall, new-project quickstart, executor needs,
safety, contributing and the exact owner-choice license placeholder. No LICENSE
file was added. The CI matrix runs Python 3.8/3.12 on Ubuntu/macOS with read-only
repository permissions and no secrets. CI provisioning downloads pytest/Python;
the tests use only fake executors and local Git remotes.

## Validation

The system's default Python 3.12.8 had no pytest module. An existing local
pytest installation was reused without installing or downloading packages:

```sh
PYTHONPATH=/usr/local/lib/python3.9/site-packages PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests
```

Installer integration suite: **18 passed**. Full suite: **135 passed in 119.10s**,
with no failures, skips or xfails (Python 3.12.8).
Additional checks: Python 3.8 syntax parsing for xq, the installer and its tests;
`sh -n install.sh`; `git diff --check`. Installer tests isolate HOME and exercise
CLAUDE_SKILLS_DIR, clean updates, version reporting, hash drift, edit protection,
forced removal/replacement, missing installs, unmanaged copies, symlinks, invalid
flags and independent execution of the installed helper without its source repo.

No PowerShell runtime is available here, so install.ps1 has been inspected but
not executed on Windows. Its shared Python implementation is covered by the POSIX
integration suite. Python 3.8 and Linux runtime checks are defined in CI and were
not run locally. External authentication, real executor APIs and cloud hosting
are outside this offline validation.
