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

## Code review fixes

All 10 numbered and 5 smaller findings in `code-review-2026-11-08.md` have
regressions in `tests/test_xq.py`. Each new regression was run against the broken
behavior first, observed failing, then rerun green after its fix. Existing test
assertions were left intact. No dependencies were added; xq remains stdlib-only
with Python 3.8 syntax and timestamp parsing that does not depend on newer
`fromisoformat` behavior.

| Finding | Regression test(s) | Fix |
| --- | --- | --- |
| 1: unrelated unbound cloud assignments block collection | `test_review_01_collect_ignores_unbound_other_batch` | Scope missing reports and progress to collected branches, seen tasks, and explicitly selected or represented batches. Unreported peers in the same batch still block atomic import. |
| 2: reprint of a plain cloud assignment crashes | `test_review_02_reprint_plain_cloud_assignment_has_clean_error` | Require recorded envelope base/brief before accessing them; report a clean error without appending. |
| 3: duplicate ID with only forced verify/merge crashes | `test_review_03_envelope_refuses_id_with_only_forced_event` | Use a safe status lookup and reject the reused ID atomically. |
| 4: reassignment hides an unverified finish | `test_review_04_unverified_attempt_requires_force_and_preserves_history` | Refuse done/failed/blocked unverified reassignment unless `assign --force`; retain earlier attempt events in the append-only ledger. |
| 5: queued agy results are ignored | `test_review_05_sync_queued_agy_results` | Sync running and queued assignments via agy-sync/poll; leave pending jobs queued and repeated sync idempotent. |
| 6: doctor aborts or checks ignores in the wrong directory | `test_review_06_doctor_reports_missing_default_branch`, `test_review_06_doctor_checks_ignores_from_root` | Report MISSING default branch and continue; run root-relative ignore/tracked-output checks from the repo root. |
| 7: Git ignores XQ_ROOT | `test_review_07_xq_root_controls_git_from_unrelated_cwd`, `test_review_07_linked_worktree_uses_primary_checkout_git` | Pass the selected root through branch/default/fetch/ancestry/report helpers and all Git calls after root discovery. |
| 8: resolving a brief masks symlinks | `test_review_08_envelope_refuses_symlinked_brief` | Check the supplied path and its parent components for symlinks before resolving. |
| 9: collection repeats ledger parsing and sensitive diffs | `test_review_09_collect_parses_ledger_once`, `test_review_09_collect_caches_sensitive_diff_per_base_ref` | Reuse the initial state, apply successful imports to summary state, cache diffs by `(base, ref)`, and print each sensitive-path warning once. Tests count ledger parses for successful/failed imports and diff calls across shared/distinct bases. |
| 10: append reads the ledger and fsyncs empty batches | `test_review_10_empty_append_does_no_io`, `test_review_10_append_reads_only_last_byte_and_recovers_partial_line` | Return before locking or file I/O for empty lists/iterators; use a binary end-minus-one seek and one-byte read for nonempty ledgers. Tests preserve Unicode and partial-line recovery while checking actual I/O. |
| Smaller 1: open status hides fixes/reject | `test_review_small_01_open_status_keeps_unresolved_verdicts` | Keep unresolved verdicts visible; completed pass tasks retain the existing closed behavior. |
| Smaller 2: timestamps fail on Python 3.8–3.10 | `test_review_small_02_iso_timestamps_on_python38` | Share a strptime-based zoned ISO parser between report validation and staleness checks, supporting Z/offsets, basic/calendar/week dates, comma and arbitrary-length fractions (microsecond precision). Tests emulate the older fromisoformat restrictions. |
| Smaller 3: retained_exit_code is treated as delivery failure | `test_review_small_03_agy_retained_exit_is_diagnostic`, `test_review_small_03_agy_retained_exit_alone_is_not_delivery` | Use actual top-level exit_code for delivery; retained diagnostics alone cannot establish delivery. Unsafe attempt exits and omitted-file checks remain enforced. |
| Smaller 4: merge can be recorded twice | `test_review_small_04_duplicate_merge_requires_force` | Refuse an already merged task before appending unless explicit `--force` reconciliation. |
| Smaller 5: step-2 root snippet selects a linked worktree | `test_review_small_05_root_snippet_resolves_primary_checkout` | Use the parent of the absolute Git common directory in SKILL.md and the step-2 recipe now located in docs/codex.md. Execute both snippets from inside a linked worktree in the regression. |

Targeted red/green checks used the existing local pytest installation without
downloads. Full-suite validation uses the same offline command documented above;
fixture Git commits/pushes target temporary local bare remotes only. No worktree
commit/push or live executor/network call was performed. Both pre-existing
untracked `.agents-brief-*.md` files were left unchanged.

Full validation: **175 passed in 119.18s**, including all 135 existing cases and
40 new regression cases; no failures, skips, or xfails. Command:

```sh
PYTHONPATH=/usr/local/lib/python3.9/site-packages PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests
```

Python 3.8 syntax/stdlib checks and SKILL.md's length limit passed in that suite;
older timestamp parsing behavior is emulated by the new regression. No Python
3.8 runtime was available locally. `git diff --check` also passed.
