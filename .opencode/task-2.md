Implement STEP 2 of memdoctor v0.1: the SQLite + sqlite-vec backend.

=== HARD RULES FOR THIS RUN ===

- Do NOT use any web tool. No WebFetch. No context7. No grep_app. No web search. Everything you need is below.
- Do NOT read the sqlite_vec package source or documentation. You do not need it.
- Do NOT run exploratory shell probes to "check how it works".
- **Write the code FIRST.** Then run the tests. Research is forbidden in this run.

=== EXACT FACTS — verified by a human. Do not re-verify. ===

- Use `.venv/bin/python` for everything.
- `sqlite_vec` is installed in the venv. Load with:
  `conn.enable_load_extension(True)` then `sqlite_vec.load(conn)`.
  (This works on the venv python. It does NOT work on the python.org framework build — never use that interpreter.)
- `CREATE VIRTUAL TABLE <name> USING vec0(id TEXT, embedding FLOAT[N])` creates the virtual table plus shadow tables: `<name>_info`, `<name>_chunks`, `<name>_rowids`, `<name>_vector_chunks00`, `<name>_metadatachunks00`, `<name>_metadatatext00`. All are of type `table` in `sqlite_master` and they all have `<name>_` prefixes.
- `PRAGMA table_info(<name>)` on the virtual table returns exactly: `rowid`, `id`, `embedding`.
- The embedding column is a little-endian float32 blob. Decode:
  `struct.unpack("<%df" % (len(blob) // 4), blob)`
- The `sql` text in `sqlite_master` for the virtual table looks like:
  `CREATE VIRTUAL TABLE v USING vec0(id TEXT, embedding FLOAT[4])`
  Parse `N` out of the `FLOAT[N]` token to get the declared dimension. If the token is just `FLOAT` with no size, the dimension is `None`.
- `sqlite3.connect(f"file:{path}?mode=ro", uri=True)` loads the extension fine and reads data.

=== LAYOUT DETECTION — implement exactly this, do not invent another scheme ===

1. Read all rows from `sqlite_master`.
2. `vec_table` = the row whose `sql` contains `USING vec0` (case-insensitive).
3. If there is no `vec_table`, raise `BackendError` listing the tables found.
4. Candidate memory tables = rows of `type='table'` whose name does not start with `sqlite_` and is not `<vec_table>_`-prefixed, and which has an `id` column (check via `PRAGMA table_info`).
5. If exactly one candidate, use it. If several, prefer the one named `memories`. If still ambiguous, raise `BackendError` listing the candidates — never guess.
6. Read `content`, `created_at` and `embedded` columns if they exist; use `None` when absent.
7. Join on `id`: a memory row is `embedded=True` when the adjacent vec row has a non-empty blob.

=== BUILD ===

- `memdoctor/backends/sqlite_vec.py` implementing the `MemoryStore` interface in `memdoctor/backends/base.py`.
- Wire it into `detect_backend`: a directory or file containing `*.db`/`*.sqlite` resolves to `sqlite`; `--backend sqlite` works.
- `sqlite-vec` is an OPTIONAL import. If missing, raise a clear, actionable `BackendError` — never a raw traceback.
- `check` MUST open the store read-only via a `mode=ro` URI and MUST NOT write a single byte.
- Also add `memdoctor/__main__.py` containing exactly:
  `from .cli import main` / `raise SystemExit(main())`
  so `python -m memdoctor` works (it currently does not).

=== TESTS ===

Add `tests/test_sqlite_backend.py` with a fixture builder that creates in `tmp_path`:
- one corrupted sqlite+sqlite-vec store covering every detection class that applies (D1–D5),
- one clean store.
Include: one test per detection class, a clean-store test asserting ZERO false positives, and a hash test asserting `check` does not modify the `.db` file.
Do not break the existing 11 tests.

=== WORK ORDER ===

Write the code first. Then `.venv/bin/python -m pytest -q` until green. Then:
`git add -A && git commit -m "feat: sqlite-vec backend"`

**A run that ends with no commits is a failed run.**

Do NOT use `find` or `ls -R` on the repo (it contains a venv and node_modules).

=== FINAL MESSAGE MUST REPORT ===
- exact `pytest -q` output
- `git log --oneline`
- files created
- anything not implemented, stated plainly
