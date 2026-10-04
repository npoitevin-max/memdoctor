Implement memdoctor v0.1 exactly as specified.

READ FIRST, in this order:
1. `.opencode/rules.md` — project rules, non-negotiable.
2. `docs/SPEC.md` — authoritative scope. Anything not listed there is out of scope.

Do NOT rewrite, restructure or "improve" README.md. Leave it exactly as it is.

Build the complete file layout listed under "Deliverables" in docs/SPEC.md, working end to end.

CORRECTNESS REQUIREMENTS (these are the ones that matter most):
- `check` must open SQLite read-only via a `file:...?mode=ro` URI and must not write a single byte. A test asserts the store hash is unchanged after a check.
- `fix` must write a timestamped snapshot before mutating anything, and must abort with a clear error if the snapshot cannot be written.
- Never delete a memory row. Vectors may be deleted or quarantined; memories are reported only.
- `sqlite-vec` must be an OPTIONAL import. The JSON backend and the JSON tests must work with it absent. If it is missing, the SQLite backend emits a clear, actionable error rather than a traceback.
- Backends auto-detect layout from `sqlite_master`. An unrecognised layout produces a clear error listing the tables found — never a guess.
- Exit codes exactly as specified: 0 clean, 1 issues found (check only), 2 error.
- `--json` prints one JSON object on stdout and nothing else (no logs, no colour, no hints).

TESTS:
- Build a fixture module that programmatically generates, in tmp_path, a corrupted SQLite+sqlite-vec store AND a corrupted JSON directory covering EVERY detection class (D1-D5), plus a clean store.
- One test per detection class, on both backends where the class applies.
- A clean-store test asserting zero false positives.
- A round-trip test: corrupt -> fix -> check returns clean.
- A test asserting `check` does not modify the store (hash before/after).
- A test asserting `fix` aborts when the backup destination is unwritable.
- A test asserting `--dry-run` writes nothing.

Work in logical steps and commit as you go with conventional-commit messages (`feat:`, `test:`, `chore:`, `fix:`).

Run `pytest -q` until it passes. Do not report success without having actually run it.

In your final message report, plainly:
- the exact `pytest -q` output
- `git log --oneline`
- the list of files created
- anything in the spec you could NOT implement, and why
