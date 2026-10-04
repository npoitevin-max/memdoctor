# memdoctor — project rules

Read `docs/SPEC.md` before implementing anything. It is the authoritative scope.

## Non-negotiables

1. **`check` never writes.** Any code path that opens a store in `check` mode must be read-only (SQLite: open with `mode=ro` URI). There is a test for this — do not break it.
2. **`fix` snapshots first.** If the snapshot cannot be written, abort before mutating anything.
3. **Never delete a memory row.** Vectors may be deleted or quarantined; memories are only ever reported.
4. **No network access, no telemetry, no daemon.**
5. **Five detection classes only.** Do not add D6+ without an explicit instruction.

## Style

- Python 3.10+, `from __future__ import annotations`, type hints everywhere.
- Standard library first. The only third-party runtime dependency is `sqlite-vec` (SQLite backend only) — and it must be an optional import so the JSON backend works without it.
- Dataclasses for `Issue`, `Severity`, `Report`. No ORM, no framework.
- Small functions. Docstrings on anything non-obvious.
- Do not print anywhere except `cli.py` — everything else returns data.

## Testing

- `pytest -q` must pass with no network.
- Every detection class needs a positive test and a clean-store negative test.
- Tests build their own fixtures in `tmp_path`; never rely on a real user store.
- No `sleep`, no flaky timing assertions.

## Commits

One logical change per commit. Conventional-commit prefixes (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).

## Explicitly do not

- Rewrite or restructure `README.md` — it is written for the audience, not for the code.
- Add a web UI, metrics endpoint, config file, or plugin system.
- Rename anything in `docs/SPEC.md` without being asked.
