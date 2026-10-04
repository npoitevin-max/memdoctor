"""Tests for the SQLite + sqlite-vec backend."""

from __future__ import annotations

import hashlib
import sqlite3
import struct
from pathlib import Path

import pytest

sqlite_vec = pytest.importorskip("sqlite_vec")

from memdoctor.backends.base import detect_backend
from memdoctor.backends.sqlite_vec import (
    SqliteVecStore,
    _declared_dimension,
    detect_sqlite,
)
from memdoctor.checks import run_checks
from memdoctor.cli import main
from memdoctor.model import Severity


def _vec_blob(vec: list[float]) -> bytes:
    return struct.pack("<%df" % len(vec), *vec)


def build_store(
    root: Path,
    *,
    db_name: str = "memories.db",
    vec_name: str = "vec_memories",
    mem_name: str = "memories",
    vec_type: str = "FLOAT[4]",
    memories: list[dict] | None = None,
    embeddings: dict[str, list[float]] | None = None,
) -> Path:
    db = root / db_name
    conn = sqlite3.connect(str(db))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.execute(f"CREATE VIRTUAL TABLE {vec_name} USING vec0(id TEXT, embedding {vec_type})")
    conn.execute(
        f"CREATE TABLE {mem_name} (id TEXT, content TEXT, created_at TEXT, embedded INTEGER)"
    )
    for m in memories or []:
        conn.execute(
            f"INSERT INTO {mem_name} (id, content, created_at, embedded) VALUES (?, ?, ?, ?)",
            (m.get("id"), m.get("content"), m.get("created_at"), int(bool(m.get("embedded")))),
        )
    for mid, vec in (embeddings or {}).items():
        conn.execute(f"INSERT INTO {vec_name} (id, embedding) VALUES (?, ?)", (mid, _vec_blob(vec)))
    conn.commit()
    conn.close()
    return db


def build_corrupted(root: Path) -> Path:
    return build_store(
        root,
        memories=[
            {
                "id": "orphan",
                "content": "flagged but no vector",
                "created_at": "2024-01-01T00:00:00Z",
                "embedded": True,
            },
            {
                "id": "dup",
                "content": "first dup",
                "created_at": "2024-01-02T00:00:00Z",
                "embedded": False,
            },
            {
                "id": "dup",
                "content": "second dup",
                "created_at": "2024-01-03T00:00:00Z",
                "embedded": False,
            },
            {"id": "missing", "content": None, "created_at": None, "embedded": False},
            {
                "id": "zero",
                "content": "all zero",
                "created_at": "2024-01-04T00:00:00Z",
                "embedded": False,
            },
            {
                "id": "fine",
                "content": "fine",
                "created_at": "2024-01-05T00:00:00Z",
                "embedded": False,
            },
        ],
        embeddings={
            "dup": [1.0, 2.0, 3.0, 4.0],
            "missing": [1.0, 2.0, 3.0, 4.0],
            "zero": [0.0, 0.0, 0.0, 0.0],
            "fine": [1.0, 2.0, 3.0, 4.0],
        },
    )


def build_clean(root: Path) -> Path:
    return build_store(
        root,
        memories=[
            {"id": "a", "content": "hello a", "created_at": "2024-01-01T00:00:00Z"},
            {"id": "b", "content": "hello b", "created_at": "2024-01-02T00:00:00Z"},
        ],
        embeddings={
            "a": [1.0, 2.0, 3.0, 4.0],
            "b": [5.0, 6.0, 7.0, 8.0],
        },
    )


@pytest.fixture
def corrupted(tmp_path) -> Path:
    return build_corrupted(tmp_path)


@pytest.fixture
def clean(tmp_path) -> Path:
    return build_clean(tmp_path)


def _ids(store: SqliteVecStore, code: str, dimension: int | None = None) -> list[str]:
    return [i.id for i in run_checks(store.iter_memories(), dimension=dimension) if i.code == code]


# -- detection classes --------------------------------------------------


def test_orphaned_vector_detected(corrupted):
    assert _ids(SqliteVecStore(corrupted), "orphaned_vector") == ["orphan"]


def test_duplicate_id_detected(corrupted):
    assert _ids(SqliteVecStore(corrupted), "duplicate_id") == ["dup"]


def test_dimension_mismatch_detected(corrupted):
    ids = _ids(SqliteVecStore(corrupted), "dimension_mismatch", dimension=3)
    assert "fine" in ids


def test_missing_field_detected(corrupted):
    assert "missing" in _ids(SqliteVecStore(corrupted), "missing_field")


def test_degenerate_vector_detected(corrupted):
    assert _ids(SqliteVecStore(corrupted), "degenerate_vector") == ["zero"]


def test_orphan_vector_without_memory_detected(tmp_path):
    db = build_store(
        tmp_path,
        memories=[{"id": "a", "content": "hello a", "created_at": "2024-01-01T00:00:00Z"}],
        embeddings={"a": [1.0, 2.0, 3.0, 4.0], "ghost": [5.0, 6.0, 7.0, 8.0]},
    )
    store = SqliteVecStore(db)
    assert store.orphan_vector_ids() == ["ghost"]
    issues = run_checks(store.iter_memories(), orphan_vector_ids=store.orphan_vector_ids())
    orphan = [i for i in issues if i.code == "orphaned_vector"]
    assert [i.id for i in orphan] == ["ghost"]
    assert orphan[0].severity == Severity.ERROR
    assert orphan[0].detail == "vector row has no matching memory row"


def test_stats_vectors_counts_true_vector_rows_with_orphan(tmp_path):
    db = build_store(
        tmp_path,
        memories=[{"id": "a", "content": "hello a", "created_at": "2024-01-01T00:00:00Z"}],
        embeddings={"a": [1.0, 2.0, 3.0, 4.0], "ghost": [5.0, 6.0, 7.0, 8.0]},
    )
    stats = SqliteVecStore(db).stats()
    assert stats["vectors"] == 2
    assert stats["orphan_vectors"] == 1


# -- clean store / false positives --------------------------------------


def test_clean_store_has_zero_issues(clean, capsys):
    code = main(["check", str(clean)])
    out = capsys.readouterr().out
    assert code == 0
    assert "no issues found" in out


# -- immutability -------------------------------------------------------


def test_check_does_not_modify_db(clean, capsys):
    def digest() -> str:
        return hashlib.sha256(clean.read_bytes()).hexdigest()

    before = digest()
    code = main(["check", str(clean)])
    after = digest()
    assert code == 0
    assert before == after


# -- detection ----------------------------------------------------------


def test_detect_sqlite_file(clean):
    assert detect_sqlite(clean) is True


def test_detect_backend_directory(tmp_path):
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    build_clean(store_dir)
    assert detect_sqlite(store_dir) is True
    assert detect_backend(store_dir) == "sqlite"


def test_backend_sqlite_flag(clean, capsys):
    code = main(["check", "--backend", "sqlite", str(clean)])
    out = capsys.readouterr().out
    assert code == 0
    assert "sqlite backend" in out


# -- declared dimension -------------------------------------------------


def test_declared_dimension_from_store(clean):
    assert SqliteVecStore(clean).dimension == 4


def test_declared_dimension_parser():
    vec = {"sql": "CREATE VIRTUAL TABLE v USING vec0(id TEXT, embedding FLOAT[4])"}
    assert _declared_dimension(vec) == 4
    nosize = {"sql": "CREATE VIRTUAL TABLE v USING vec0(id TEXT, embedding FLOAT)"}
    assert _declared_dimension(nosize) is None
