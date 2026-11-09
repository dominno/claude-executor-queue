# Task: parser-empty-input

Goal: make `src/parser.py` handle empty input with a documented empty result.
Replace these example paths/checks with the project's real ones before dispatch.

Read and write only in this worktree; do not open `.env*`, `~/.ssh`, `~/.config`,
or the main checkout. If a key is needed, report blocked.

- Inputs/outputs: `src/parser.py`, `tests/test_parser.py`, `docs/parser-empty-input-notes.md` only.
- Checks: `python3 -m pytest -q tests/test_parser.py`; add a meaningful empty-input regression.
- Forbidden: network, paid calls, secrets, dependency installs, weakening tests,
  commits, pushes, merges, modifications outside the allowed files.
- Delivery: describe changes, actual check results and limitations in the tracked
  notes path above; report blocked if the named inputs are missing.

The lead creates the isolated worktree, supplies an absolute prompt-file path,
records the returned jobId/worktree with xq and verifies the diff independently.
