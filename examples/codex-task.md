# Task: parser-empty-input

Result: `src/parser.py` returns the documented empty result for empty input, with a regression test.

Replace these example paths/checks with the project's real ones before dispatch.

Read and write only in this worktree; do not open `.env*`, `~/.ssh`, `~/.config`,
or the main checkout. If a key is needed, report blocked.

1. Add a failing regression test for empty input in `tests/test_parser.py`.
2. Make `src/parser.py` return the documented empty result.
3. Describe changes, actual check results and limitations in the notes file.

Forbidden: network, paid calls, secrets, dependency installs, weakening tests,
commits, pushes, merges, modifications outside the files below. Report blocked
if the named inputs are missing.

The lead creates the isolated worktree, supplies an absolute prompt-file path,
records the returned jobId/worktree with xq and verifies the diff independently.

## Acceptance

Files: src/parser.py, tests/test_parser.py, docs/parser-empty-input-notes.md
Checks: `python3 -m pytest -q tests/test_parser.py`
Notes: docs/parser-empty-input-notes.md
