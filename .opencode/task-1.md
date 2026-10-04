Implement ONLY step 1 of memdoctor v0.1. Do NOT implement the SQLite backend in this run.

=== ENVIRONMENT FACTS: ALREADY ESTABLISHED. DO NOT RE-DERIVE. ===

A previous run wasted its entire session rediscovering these. They are settled:

- The repo ALREADY HAS a working virtualenv at `.venv`, built from `/opt/homebrew/opt/python@3.13` (Python 3.13.15), with `pytest` and `sqlite_vec` 0.1.9 installed. Use `.venv/bin/python` for everything. Do not create or install anything.
- The **python.org framework build's `sqlite3` module does NOT expose `Connection.enable_load_extension`**. Do not try to use it. Do not investigate it.
- (For step 2 only, recorded so you do not re-probe it: `CREATE VIRTUAL TABLE v USING vec0(id TEXT, embedding FLOAT[4])` works; shadow tables are v_info, v_chunks, v_rowids, v_vector_chunks00, v_metadatachunks00, v_metadatatext00; `PRAGMA table_info(v)` returns rowid,id,embedding; vectors are little-endian float32 blobs; a read-only `file:...?mode=ro` URI connection loads the extension fine.)

**STEP 1 TOUCHES SQLITE ZERO TIMES.** If you find yourself writing or running any sqlite code, you have gone out of scope. Stop and write the files below instead.

=== STEP 1 SCOPE: build exactly this, nothing more ===

1. `memdoctor/__init__.py` — `__version__ = "0.1.0"`.
2. `memdoctor/model.py` — dataclasses `Severity` (enum: ERROR, WARN), `Issue`, `Report`. Backend-agnostic, no IO.
3. `memdoctor/backends/base.py` — abstract `MemoryStore` exposing a normalised view: `iter_memories()` yielding objects with `id`, `content`, `created_at`, `embedding` (or None); plus `stats` (counts). Plus a `detect_backend(path)` helper that returns `"json"` for a directory of `.json`/`.jsonl` and raises a clear error otherwise (import the sqlite detector lazily and return "sqlite" — but do not implement it yet).
4. `memdoctor/backends/json_dir.py` — backend for a directory of `.json` (a list of memory objects) and `.jsonl` (one object per line). Malformed JSON is reported as an issue, never an unhandled crash.
5. `memdoctor/checks.py` — D1–D5 over the normalised view, backend-agnostic:
   - D1 `orphaned_vector` (ERROR) — a memory marked as embedded whose embedding is missing, or an embedding present with no memory. (In the JSON backend the second shape may be unreachable; handle what applies and no more.)
   - D2 `duplicate_id` (ERROR) — two memories sharing an id.
   - D3 `dimension_mismatch` (ERROR) — embedding length differs from the declared dimension (from `--dim`, else the modal length).
   - D4 `missing_field` (WARN) — empty/None `content`, `id` or `created_at`.
   - D5 `degenerate_vector` (ERROR) — all-zero, NaN/Inf, or zero L2 norm.
6. `memdoctor/cli.py` — argparse. Subcommand `check PATH [--json] [--backend auto|json|sqlite] [--dim N]`. Leave a `fix` subcommand wired to a clear "not implemented in this step" message. Exit codes: 0 clean, 1 issues found, 2 error. Human output: header (path, backend, counts), one line per detection class with count and up to 3 example ids, a total, a hint. Colour only when stdout is a TTY. `--json` prints exactly ONE JSON object on stdout and nothing else.
7. `pyproject.toml` — metadata, `requires-python = ">=3.10"`, `console_scripts` entry point `memdoctor = memdoctor.cli:main`, and `sqlite-vec` declared as an OPTIONAL extra so the JSON backend works without it.
8. `tests/test_json_backend.py` + `tests/fixtures.py` — a fixture builder generating corrupted and clean JSON/JSONL stores in `tmp_path`, one test per detection class, a clean-store test asserting ZERO false positives, a `--json` output-validity test, and a test that `check` does not modify the store (hash before/after).

=== WORK ORDER ===

Write the code FIRST. Then `cd /Users/nicolas/projects/memdoctor && .venv/bin/python -m pytest -q`, fix until green.

Then COMMIT:
`git add -A && git commit -m "feat: core model, JSON backend, detection checks and CLI"`

**A run that ends with no commits is a failed run.** Commit before you finish.

Do NOT: run exploratory shell probes, create a venv, install packages, write SQLite code, touch README.md, or use `find`/`ls -R` on the repo (it contains a venv and node_modules).

=== FINAL MESSAGE MUST REPORT ===
- the exact `pytest -q` output
- `git log --oneline`
- the list of files created
- anything you did not implement, stated plainly
