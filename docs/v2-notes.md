# Executor queue v2 delivery

Source changes stay in `tools/executor-queue/skill/`; no user-level installation,
project commit/push, network request or live executor invocation was performed.
The lead installs the reviewed source with `install.sh`.

## Existing work and scope

Starting HEAD: `4b5905bf`, following source import `d9467f0f`.
`xq-tests` already had the v1 suite in its working tree at
`courses/scripts/tests/test_xq_skill.py` (branch HEAD `f7ad1902`). It imports the
installed v1 script, so it was left intact. The requested source-relative v2 suite
reuses its temporary-repository/bare-remote fixture approach and covers the new
contracts. No course content, project ledgers, `.env` or user media changed.

## Review coverage

| Critic items | Result |
| --- | --- |
| 1 | Hook reads stdin, honours stop_hook_active, flags only queued/configured idle executors, supports pause/resume; never invents work. |
| 2, 5 | Resolve installed plugin paths before version-sorted cache fallback; probe agy queue/verdict; print quoted CC/AGYB assignments. |
| 3 | Absolute brief stored by assign and absolute prompt-file in launch recipe; explains resolution relative to cwd. |
| 4 | Briefs/status are tracked cloud inputs/outputs; init ignores ledger/lock and agy state only. init --local excludes briefs/status; envelope refuses ignored paths. |
| 6 | Record job/worktree; poll explicit job status/result; document cancel/failure reconciliation and session/worktree scope; stale flag in hook/status. |
| 7 | doctor --need with SKIP, Node/auth, agy models, git HEAD/default branch/origin, install/auth and plugin fallbacks. |
| 8–10 | Discovered base, absolute worktree, retry IDs, real dependency preparation; review untrusted scripts/tests before execution; explicit staging and authorized merge/cleanup recipe. |
| 11–12 | Pinned-base rules, untrusted output, sensitive-diff flags; explicit read boundaries and blocked-on-key clause. |
| 13 | Full agy schema/scope/exits, manifest dry run, primary-repo diff export, explicit assign-before-queue, sync results/archives, unique names, headers/citation limits, opt-in watcher. |
| 14 | Exact pushed brief bytes and headings, validated IDs/branches, optional session branch, discover/reprint, cancellation instructions. |
| 15 | Object/schema/type checks, concrete reasons, base/head ancestry, NUL-delimited tree paths, MISSING/CHANGED, validate all reports before any finish write. |
| 16–17 | Per-task commits/head mapping/verdicts, rejection/cherry-pick procedure, verify-batch worktree, outgoing commit/blob/key scan, consent per push, protected-branch and cloud environment setup. |
| 18 | Reassign clears previous attempt metadata, shared git-common-dir root/XQ_ROOT, corrupt-line warnings, lifecycle guards/force, existing commit check, transaction lock and partial-last-line recovery. |
| 19 | Description includes cloud session, claude.ai/code, /goal batch, collect session branch; project-owned ledger takes precedence. |
| 20–25 | Effort/model defaults, fix-loop note, cloud prerequisites/trial, stale cloud tasks, locking/evidence cap/UTF-8/Windows hook variant, git branch validation and fetch option separator, concrete outgoing scan. Item 25 is context, not another implementation. |

No trustworthy numeric minimum for agy queue/verdict could be established from
local files: both the older cache and a newer development tree identify as
0.1.0, but only the latter implements these commands. SKILL.md records that fact
and requires actual capability probes rather than inventing a minimum version.

## TDD and independent review

The initial v2 tests ran against v1 before implementation: **35 failed, 5 passed**.
The failures covered the new hook/config, lifecycle, corruption/root, init/cloud
preflight, collection, doctor and polling contracts.

A separate read-only reader found two integration gaps in the first implementation:

- Real agy queue results have blank lines/title before exit metadata. New tests
  reproduced the bug using the actual header shape. The parser now stops at the
  answer boundary, distinguishes metadata from historical attempts, rejects omitted
  files and unsafe exits, and accepts successful recovered quota retries.
- Reusing agy IDs could import an old result. Assignment now rejects existing
  results or queue/archive artifacts; documentation assigns before bridge queueing.

Further failing tests preceded fixes for collecting one session without requiring
another session's results, clearer unpushed-brief diagnostics, and reused failed
cloud IDs. Cloud envelopes now require fresh IDs even after failed/blocked work;
reprint is idempotent and direct ledger reassign still resets its attempt.
A reprint test-helper bug (extra empty CLI argument) was corrected without
weakening its assertion. There are no xfail or skipped regression cases.

The suite uses only disposable Git repositories and a local bare origin, with
HOME/config/hooks isolated and `GIT_ALLOW_PROTOCOL=file`. Fake node/codex/agy
scripts on PATH exercise command arguments, auth and plugin failures. A pair of
real local Python processes verifies assignment locking. No live executor runs.

## Validation and limits

Run the source suite with:

```sh
/usr/local/bin/pytest tools/executor-queue/tests/test_xq.py -q
```

Final independent reader run (separate context):
`PYTHONDONTWRITEBYTECODE=1 /usr/local/bin/pytest -q -p no:cacheprovider tools/executor-queue/tests/test_xq.py`
→ **67 passed in 50.68 seconds**. The reader rechecked the corrected source and
found no remaining concrete requirement gaps.

Skill-creator's `quick_validate.py` returned **Skill is valid!**; compilation of
both Python files and `git diff --check` passed. The tests parse xq.py with Python 3.8's
syntax grammar and restrict its imports to stdlib modules. Runtime testing uses
local Python 3.9; no Python 3.8 interpreter or Windows runtime was available.

Live authentication, cloud hosting, real model delivery and installed plugin
execution were intentionally not tested. The script handles recognized bridge
queue headers; plain delegate output without exit metadata needs manual finish.
Cloud collection validates provenance/schema, not correctness: code review,
per-task checks, independent verification and merge authorization remain required.
Browser, application build and course tests do not apply to this standalone CLI.

## Reconcile the older suite with v2 (2026-11-08)

The v2 implementation and its 67-test native suite already existed; this follow-up
changes only `courses/scripts/tests/test_xq_skill.py` and appends these notes.
No additional `xq.py` or skill changes were needed.

Baseline on HEAD `ede8d7ed` with the existing uncommitted v2 source:
**30 failed, 16 passed, 4 xfailed** (one of the failures was a strict XPASS for
the now-fixed reassignment bug). Adding brief headings and removing the xfail
decorators exposed **11 failed, 39 passed**, making the remaining differences
visible without masking them behind cloud-envelope preflight.

Root causes and reconciliation, keyed to `critic-review-2026-11-08.md`:

- **14 — pushed brief headings:** both batch fixtures now contain the exact
  `## <id>` sections. The unpublished-brief negative fixture also has a valid
  heading so it isolates publication rather than section validation.
- **1 — idle hooks:** completed, verified work alone no longer implies an idle
  executor needs more work. The older hook test now asserts quiet first, then
  supplies an actual queued task and checks the v2 backlog message.
- **11, 12, 17 — envelope rules:** the old single-sentence assertion now checks
  each expanded prohibition (pushing main, opening PRs/pushing other branches,
  using secrets/network services/paid APIs). The restrictions remain asserted.
- **15 — report diagnostics and atomic import:** match the explicit base-mismatch
  reason with its current separator. Invalid JSON and non-object reports keep
  both tasks running; the tests now assert the entire ledger remains unchanged.
  This is the intentional validate-all-before-write contract, not a partial
  import regression. Non-object cases also require the JSON-object diagnostic.
- **15 — second-batch provenance:** the fixture's second report now uses its
  newer pinned base as its head instead of the first batch's older head. The
  batch-progress and cross-batch isolation assertions remain unchanged.
- **18 — fresh verification:** the reassignment regression already passes under
  v2. Its existing hook, verdict and merge assertions remain unchanged.

All five former strict-xfail cases are normal tests: three non-object JSON cases,
one batch isolation case and one reassignment case. No skips or xfails were added.
An independent read-only context reviewed the final test diff against the source
and critic items and reported no findings; it did not edit files or run tests.

Validation (both suites in one serial pytest run):

```sh
PYTHONDONTWRITEBYTECODE=1 /usr/local/bin/pytest -q -p no:cacheprovider courses/scripts/tests/test_xq_skill.py tools/executor-queue/tests
```

Result: **117 passed in 87.64 seconds** (older suite 50, native v2 suite 67),
with no failures, skips or xfails. `git diff --check` also passed. No browser,
application build, live executor tasks, paid work, network, commit or push was run
for this reconciliation; all test Git operations used disposable local fixtures.
