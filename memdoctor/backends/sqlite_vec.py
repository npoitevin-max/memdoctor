"""SQLite + sqlite-vec memory store backend.

A store is either a ``.db``/``.sqlite`` file or a directory containing exactly
one such file. The layout is discovered by finding the ``vec0`` virtual table
and joining its embedding blobs to the memory table on ``id``.

``sqlite-vec`` is an optional dependency: the module imports fine without it,
and only raises an actionable ``BackendError`` when a store is actually opened.
"""

from __future__ import annotations

import os
import re
import sqlite3
import struct
from pathlib import Path
from typing import Iterator

from ..model import Memory
from .base import BackendError, MemoryStore

try:
    import sqlite_vec
except ImportError:  # pragma: no cover - depends on the environment
    sqlite_vec = None

_MISSING_MSG = (
    "the SQLite backend requires the 'sqlite-vec' package; install it with "
    "`pip install memdoctor[sqlite-vec]` (or `pip install sqlite-vec`)"
)

_DB_SUFFIXES = {".db", ".sqlite"}

_FLOAT_DIM_RE = re.compile(r"FLOAT\s*\[\s*(\d+)\s*\]", re.IGNORECASE)
_VEC0_RE = re.compile(r"USING\s+vec0\b", re.IGNORECASE)


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _id_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _decode_blob(blob: object) -> list[float] | None:
    if not blob:
        return None
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        return None
    data = bytes(blob)
    if len(data) % 4 != 0:
        return None
    return list(struct.unpack("<%df" % (len(data) // 4), data))


def _resolve_db(path: Path) -> Path:
    if path.is_file():
        return path
    if path.is_dir():
        dbs = sorted(
            f for f in path.iterdir() if f.is_file() and f.suffix.lower() in _DB_SUFFIXES
        )
        if len(dbs) == 1:
            return dbs[0]
        if len(dbs) == 0:
            raise BackendError(f"no database file (*.db/*.sqlite) found in {path}")
        raise BackendError(
            f"multiple database files found in {path}: {', '.join(d.name for d in dbs)}"
        )
    raise BackendError(f"not a directory or file: {path}")


def _open_readonly(db: Path) -> sqlite3.Connection:
    if sqlite_vec is None:
        raise BackendError(_MISSING_MSG)
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise BackendError(f"cannot open {db}: {exc}") from exc
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
    except Exception as exc:
        conn.close()
        raise BackendError(f"failed to load sqlite-vec extension: {exc}") from exc
    return conn


def _read_master(conn: sqlite3.Connection, db: Path) -> list[dict]:
    try:
        rows = conn.execute("SELECT type, name, sql FROM sqlite_master").fetchall()
    except sqlite3.Error as exc:
        raise BackendError(f"cannot read {db}: {exc}") from exc
    return [{"type": t, "name": n, "sql": s} for t, n, s in rows]


def _find_vec_table(rows: list[dict]) -> dict | None:
    for row in rows:
        sql = row["sql"] or ""
        if _VEC0_RE.search(sql):
            return row
    return None


def _declared_dimension(vec_table: dict) -> int | None:
    sql = vec_table["sql"] or ""
    m = _FLOAT_DIM_RE.search(sql)
    if m:
        return int(m.group(1))
    return None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        row[1]
        for row in conn.execute(f"PRAGMA table_info({_quote_ident(table)})").fetchall()
    }


def _candidate_memory_tables(
    conn: sqlite3.Connection, rows: list[dict], vec_table_name: str
) -> list[str]:
    candidates: list[str] = []
    for row in rows:
        if row["type"] != "table":
            continue
        name = row["name"]
        if not name or name.startswith("sqlite_"):
            continue
        if name == vec_table_name:
            continue
        if name.startswith(vec_table_name + "_"):
            continue
        if "id" in _columns(conn, name):
            candidates.append(name)
    return candidates


def _select_memory_table(candidates: list[str], db: Path) -> str:
    if len(candidates) == 1:
        return candidates[0]
    if "memories" in candidates:
        return "memories"
    if not candidates:
        raise BackendError(f"no memory table (with an `id` column) found in {db}")
    raise BackendError(
        f"ambiguous memory table in {db}: candidates {', '.join(sorted(candidates))}; "
        f"rename one to `memories`"
    )


class SqliteVecStore(MemoryStore):
    backend = "sqlite"

    def __init__(self, path: str | os.PathLike[str]) -> None:
        super().__init__(path)
        self._memories: list[Memory] = []
        self._db_path: Path | None = None
        self._vec_table = ""
        self._memory_table = ""
        self.dimension: int | None = None
        self._table_count = 0
        self._vector_count = 0
        self._orphan_vector_ids: list[str] = []
        self._load()

    def _load(self) -> None:
        db = _resolve_db(self.path).resolve()
        self._db_path = db
        conn = _open_readonly(db)
        try:
            rows = _read_master(conn, db)
            self._table_count = sum(1 for r in rows if r["type"] == "table")
            vec_table = _find_vec_table(rows)
            if vec_table is None:
                names = [r["name"] for r in rows if r["name"]]
                raise BackendError(
                    f"no vec0 virtual table found in {db}; "
                    f"tables found: {', '.join(names) or '(none)'}"
                )
            self._vec_table = vec_table["name"]
            self.dimension = _declared_dimension(vec_table)
            candidates = _candidate_memory_tables(conn, rows, self._vec_table)
            self._memory_table = _select_memory_table(candidates, db)
            self._read_memories(conn)
        finally:
            conn.close()

    def _read_memories(self, conn: sqlite3.Connection) -> None:
        mem_cols = _columns(conn, self._memory_table)
        select = ["id"]
        for col in ("content", "created_at", "embedded"):
            if col in mem_cols:
                select.append(col)
        col_sql = ", ".join(_quote_ident(c) for c in select)
        mem_rows = conn.execute(
            f"SELECT {col_sql} FROM {_quote_ident(self._memory_table)} ORDER BY id"
        ).fetchall()

        memory_ids = {
            mem_id for mem_id in (_id_str(row[0]) for row in mem_rows) if mem_id is not None
        }

        embeddings: dict[str, list[float] | None] = {}
        vector_ids: set[str] = set()
        try:
            vec_rows = conn.execute(
                f"SELECT id, embedding FROM {_quote_ident(self._vec_table)}"
            ).fetchall()
        except sqlite3.Error as exc:
            raise BackendError(f"cannot read vector table {self._vec_table}: {exc}") from exc
        self._vector_count = len(vec_rows)
        for vid, blob in vec_rows:
            key = _id_str(vid)
            if key is None:
                continue
            vector_ids.add(key)
            embeddings[key] = _decode_blob(blob)

        self._orphan_vector_ids = sorted(vector_ids - memory_ids)

        for row in mem_rows:
            values = dict(zip(select, row))
            mem_id = _id_str(values.get("id"))
            content = _text(values.get("content"))
            created_at = _text(values.get("created_at"))
            embedded_col = values.get("embedded")
            embedding = embeddings.get(mem_id) if mem_id is not None else None
            embedded = bool(embedded_col) or embedding is not None
            self._memories.append(
                Memory(
                    id=mem_id,
                    content=content,
                    created_at=created_at,
                    embedding=embedding,
                    embedded=embedded,
                )
            )

    def iter_memories(self) -> Iterator[Memory]:
        yield from self._memories

    def orphan_vector_ids(self) -> list[str]:
        return list(self._orphan_vector_ids)

    def stats(self) -> dict[str, int]:
        return {
            "memories": len(self._memories),
            "vectors": self._vector_count,
            "orphan_vectors": len(self._orphan_vector_ids),
            "tables": self._table_count,
        }


def detect_sqlite(path: str | os.PathLike[str]) -> bool:
    """Return True when ``path`` is (or contains) a sqlite-vec store."""
    p = Path(path)
    if not p.exists():
        return False
    if sqlite_vec is None:
        raise BackendError(_MISSING_MSG)
    try:
        db = _resolve_db(p)
    except BackendError:
        return False
    try:
        conn = _open_readonly(db)
    except BackendError:
        return False
    try:
        rows = _read_master(conn, db)
    finally:
        conn.close()
    return _find_vec_table(rows) is not None
