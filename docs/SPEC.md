# memdoctor — v0.1 Specification

Status: authoritative scope for v0.1. Anything not listed here is out of scope.

## Goal

A single-command integrity checker and repairer for AI agent memory stores. The 60-second demo is: corrupt a store, run `memdoctor check`, watch it name exactly what is wrong, run `memdoctor fix`, re-run and see it clean.

## Constraints

- **Two backends only**: (1) SQLite with a `sqlite-vec` virtual table, (2) a JSON/JSONL memory directory.
- **Five detection classes only** (D1–D5 below). Resist adding more.
- **No daemon, no dashboard, no server, no cloud, no telemetry.**
- Python 3.10+. Standard library preferred; `sqlite-vec` is the only hard third-party dependency and only for the SQLite backend.
- Zero writes during `check`. `fix` must snapshot before it touches anything.
- Must run offline.

## CLI surface

```
memdoctor check   PATH [--json] [--backend auto|sqlite|json] [--dim N]
memdoctor fix     PATH [--dry-run] [--quarantine-dir DIR] [--backend ...] [--dim N]
memdoctor version
```

Exit codes: `0` clean, `1` issues found (check only), `2` error running. `--json` prints a single JSON object to stdout and nothing else.

`--backend auto` infers from the path: a directory containing `*.db`/`*.sqlite` → sqlite; a directory of `*.json`/`*.jsonl` → json.

## Detection classes

| ID | Name | Definition | Severity | Safe repair |
|---|---|---|---|---|
| D1 | `orphaned_vector` | A vector row whose id has no matching memory row; or a memory row marked embedded with no vector row | error | With `--fix`: delete the orphaned vector. Report the orphaned-memory case, do not delete memory. |
| D2 | `duplicate_id` | Two memory rows sharing an id, or two vector rows for one id | error | Report only. Never auto-merge. |
| D3 | `dimension_mismatch` | Stored vector length ≠ declared dimension (from `--dim`, else the modal length in the store) | error | Report only. |
| D4 | `missing_field` | A memory row with NULL/empty `content`, `id`, or `created_at` | warn | Report only by default. |
| D5 | `degenerate_vector` | A vector that is all-zero, contains NaN/Inf, or has zero L2 norm | error | Quarantine the vector row to `--quarantine-dir` (or a `_quarantine` table) and delete it from the live table. Never delete the memory row. |

## SQLite backend

Auto-detect table names rather than hardcoding: find the `vec0` virtual table via `sqlite_master`, and find the memory table as the non-virtual table sharing an id column with it. Support the common `rowid`-linked shape. If the shape is unrecognised, emit a clear `unrecognised store layout` error listing the tables found — do not guess.

## JSON backend

Support a directory of `.json` (list of memory objects) and `.jsonl` (one object per line). Expected fields: `id`, `content`, `created_at`, and optionally `embedding`. D1/D2/D4 apply; D3/D5 apply when `embedding` is present.

## Repair safety rules

1. `fix` takes the path, resolves it, and writes a timestamped snapshot next to the store before any mutation (`<store>.memdoctor-backup-<UTC ISO8601>`). If the snapshot fails, abort without modifying anything.
2. Never delete a memory row. Ever.
3. Quarantine rather than delete where a vector could be regenerated.
4. `--dry-run` prints the exact planned mutations and exits without writing.
5. Every mutation is logged with the row id and reason, and the log is written to stdout in a summary.

## Output

Human output: a header with store path, row counts and backend; one line per detection class with count and a few example ids; a total; a hint line. Colour only when stdout is a TTY. JSON output: a single object with `version`, `backend`, `store`, `counts`, `issues[]` (each with `code`, `severity`, `id`, `detail`), `duration_ms`.

## Testing

- `pytest`, with an in-repo fixture builder that generates a deliberately corrupted SQLite+sqlite-vec store and a corrupted JSON directory covering **every** detection class.
- One test per detection class asserting it is found, plus a test asserting a **clean** store reports zero issues (no false positives).
- A round-trip test: corrupt → `fix` → `check` returns clean.
- A test asserting `check` does not modify the store's bytes (hash before/after).
- A test asserting `fix` refuses to run if the backup cannot be written.

## Deliverables

```
pyproject.toml          # console_scripts: memdoctor = memdoctor.cli:main
memdoctor/__init__.py   # __version__
memdoctor/cli.py
memdoctor/model.py      # Issue, Severity, Report dataclasses
memdoctor/backends/     # base.py, sqlite_vec.py, json_dir.py
memdoctor/checks.py     # D1-D5, backend-agnostic over a normalised MemoryStore view
memdoctor/fix.py        # repair planner + executor + backup
tests/                  # as above, plus fixtures builder
README.md               # already written - do not rewrite
LICENSE                 # MIT
```

## Out of scope for v0.1

Adding backends, an LLM-based "is this memory correct" judgement, embedding regeneration, a web UI, a daemon, Prometheus metrics, multi-store aggregation, anything requiring network access.
