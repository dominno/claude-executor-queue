# Changelog

## v3 — 3.0.0 — 2026-11-08

- Add repo/CLI/skill version markers and release history.
- Protect installed local edits with recorded hashes; add check/uninstall/force
  options to offline POSIX and Windows installers using one Python implementation.
- Add project examples and an Ubuntu/macOS Python 3.8/3.12 test matrix.
- Keep the installed skill self-contained; move longer recipes into repo docs.
- Expand installation, quickstart, executor requirements, safety and contribution docs.

## v2 — 2.0.0 — 2026-11-08

Summary of the 25 items in `docs/critic-review-2026-11-08.md` (item 25 was a
rejected objection, retained for context rather than claimed as a new fix):

1. Stop hook reads stdin, honors recursion guard, limits idle enforcement to real
   backlog/config, supports pause/resume and never invents work.
2. Resolve agy-bridge from installed plugin metadata, with version-sorted fallback;
   probe queue/verdict capabilities rather than trusting a version label.
3. Store absolute briefs and use absolute Codex prompt-file paths.
4. Track cloud briefs/status, ignore only local state, add local-only init mode.
5. Resolve Codex companion from plugin metadata or numeric version sorting; quote CC.
6. Record Codex job/worktree, query result/cancel explicitly, import terminal states
   with poll and flag stale running work.
7. Add executor-selective doctor checks for tools, auth, Node, Git and cloud origin;
   document installation/authentication and capability fallbacks.
8. Discover the base, use absolute worktrees and fresh retry branches, prepare real dependencies.
9. Inspect scope/artifacts/symlinks, stage named files and integrate only with authorization;
   record the real merge SHA and clean up worktrees.
10. Inspect untrusted scripts, hooks, instructions, lockfiles and tests before execution;
    run reviewed checks in a clean environment.
11. Treat all executor output as data; pin cloud instructions and flag sensitive changes.
12. Require explicit Codex read/write boundaries and blocked-on-key behavior.
13. Bound agy manifests/schema/context, export worktree diffs, assign before dispatch,
    use unique names, validate headers/exits, sync archives and require opt-in watching.
14. Validate pushed brief bytes/headings/IDs; support optional session branches,
    discovery/reprint and explicit remote cancellation instructions.
15. Validate all cloud report schemas/types/provenance before writing; diagnose
    missing/changed reports, base/head ancestry and malformed JSON.
16. Verify batches per task from pinned base; map commits, reject selectively and
    restrict failed/blocked tasks to their own status files.
17. Scan all outgoing commits/blobs/key patterns, consent per push, support protected
    branches and document cloud environment prerequisites.
18. Reset reassignment metadata, share ledger across worktrees, tolerate corrupt
    lines and enforce lifecycle/merge guards and transaction locking.
19. Expand cloud/session triggers while preferring project-owned queues.
20. Document effort defaults and omit model selection by default.
21. Document bounded fix loops with new IDs and fresh verification.
22. Document cloud prerequisites, small trials and stale cloud detection.
23. Add cross-platform locking, evidence bounds, UTF-8 and Windows hook guidance.
24. Validate branch names and use safe fetch argument separation.
25. Preserve the existing all-outgoing-commits scan; add the concrete scan command
    under item 17 (the original objection was rejected).

See `docs/v2-notes.md` for historical validation and follow-up reconciliation.

## v1 — 1.0.0 — 2026-11-08

- Initial imported Claude Code skill and stdlib xq helper: assignments, results,
  verification/merge records, doctor, Stop hook and cloud envelope/collection.
- Initial copy installer and offline temporary-repository test coverage.

The v1/v2 numbers label historical iterations; explicit version markers begin in v3.
