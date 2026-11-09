# Code review of claude-executor-queue (v2), 2026-11-08 — findings to fix

All in `skill/executor-queue/scripts/xq.py` (line numbers at commit caa9ba8):
1. L706 cloud-collect: running cloud tasks without a recorded branch are reported MISSING on another branch's collect, and any MISSING
   blocks all ledger writes → one outstanding batch blocks every other batch. Scope MISSING to tasks of the collected branch/batch.
2. L571 `cloud-envelope --reprint` KeyError on `first['base']` for tasks recorded via plain `assign --executor cloud` (no base) → clean error.
3. L579 duplicate-id check `known[tid]['status']` KeyError for tasks created only by forced verify/merge → use .get.
4. L225 assign allows re-assigning a finished-but-unverified task, wiping the finish and silencing the hook → refuse unless `--force`
   (and keep the previous attempt visible).
5. L436 agy-sync/poll ignore `--queued` agy tasks even when the bridge ran the job → sync queued ones whose results exist.
6. L387 doctor aborts via default_branch() SystemExit (detached HEAD, no origin/HEAD) → report MISSING; ignore checks must use repo root, not cwd.
7. L580 XQ_ROOT: many git calls run in cwd instead of root → pass cwd=root everywhere.
8. L520 brief_path checks is_symlink() after resolve() → check before resolving; refuse symlinked briefs.
9. L716 progress summary re-parses the ledger per task, SENSITIVE diff repeated per task → compute once / cache per (base, ref).
10. L102 append_many reads the whole ledger to check the trailing newline and fsyncs on empty rows → seek end-1, early return.
Smaller: `status --open` hides fixes/reject verdicts; `fromisoformat` rejects valid timestamps on 3.8–3.10; agy-sync treats
`retained_exit_code` as failure; `merge` can be recorded twice; SKILL.md step-2 `git rev-parse --show-toplevel` gives the worktree path
in a linked worktree (use `git rev-parse --path-format=absolute --git-common-dir`/main checkout).
