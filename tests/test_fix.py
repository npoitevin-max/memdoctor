"""Tests for the `fix` subcommand (SQLite backend repairs)."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import struct
from pathlib import Path

import pytest

sqlite_vec = pytest.importorskip("sqlite_vec")

from fixtures import memory, write_json_store
from memdoctor.cli import main


def _vec_blob(vec: list[float]) -> bytes:
    return struct.pack("<%df" % len(vec), *vec)


def build_fixable(root: Path) -> Path:
    """A store with an orphaned vector and a degenerate vector, otherwise clean."""
    db = root / "memories.db"
    conn = sqlite3.connect(str(db))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.execute("CREATE VIRTUAL TABLE vec_memories USING vec0(id TEXT, embedding FLOAT[4])")
    conn.execute("CREATE TABLE memories (id TEXT, content TEXT, created_at TEXT, embedded INTEGER)")
    conn.execute(
        "INSERT INTO memories (id, content, created_at, embedded) VALUES (?, ?, ?, ?)",
        ("zero", "degenerate memory", "2024-01-01T00:00:00Z", 0),
    )
    conn.execute(
        "INSERT INTO memories (id, content, created_at, embedded) VALUES (?, ?, ?, ?)",
        ("fine", "fine memory", "2024-01-02T00:00:00Z", 0),
    )
    conn.execute(
        "INSERT INTO vec_memories (id, embedding) VALUES (?, ?)",
        ("ghost", _vec_blob([1.0, 2.0, 3.0, 4.0])),
    )
    conn.execute(
        "INSERT INTO vec_memories (id, embedding) VALUES (?, ?)",
        ("zero", _vec_blob([0.0, 0.0, 0.0, 0.0])),
    )
    conn.execute(
        "INSERT INTO vec_memories (id, embedding) VALUES (?, ?)",
        ("fine", _vec_blob([1.0, 2.0, 3.0, 4.0])),
    )
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def fixable(tmp_path) -> Path:
    return build_fixable(tmp_path)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _backups(root: Path) -> list[Path]:
    return sorted(root.glob("*.memdoctor-backup-*"))


def _quarantines(root: Path) -> list[Path]:
    return sorted(root.glob("*.memdoctor-quarantine.jsonl"))


def _memory_count(db: Path) -> int:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    finally:
        conn.close()


# -- dry run ------------------------------------------------------------


def test_dry_run_leaves_db_byte_identical_and_writes_nothing(fixable, capsys):
    before = _digest(fixable)
    code = main(["fix", "--dry-run", str(fixable)])
    after = _digest(fixable)
    assert code == 0
    assert before == after
    assert _backups(fixable.parent) == []
    assert _quarantines(fixable.parent) == []


# -- backup -------------------------------------------------------------


def test_fix_creates_backup_identical_to_pre_fix_store(fixable, capsys):
    before = _digest(fixable)
    code = main(["fix", str(fixable)])
    assert code == 0
    backups = _backups(fixable.parent)
    assert len(backups) == 1
    assert _digest(backups[0]) == before


# -- round trip ---------------------------------------------------------


def test_fix_removes_orphaned_and_degenerate_vectors(fixable, capsys):
    code = main(["fix", str(fixable)])
    assert code == 0

    check_code = main(["check", str(fixable)])
    out = capsys.readouterr().out
    assert check_code == 0
    assert "no issues found" in out


def test_fix_does_not_change_memory_row_count(fixable, capsys):
    before = _memory_count(fixable)
    code = main(["fix", str(fixable)])
    assert code == 0
    assert _memory_count(fixable) == before


def test_round_trip_directory_resolves_to_sqlite(tmp_path, capsys):
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    build_fixable(store_dir)

    code = main(["check", str(store_dir)])
    assert code == 1

    code = main(["fix", str(store_dir)])
    assert code == 0

    code = main(["check", str(store_dir)])
    out = capsys.readouterr().out
    assert code == 0
    assert "sqlite backend" in out
    assert "no issues found" in out


# -- quarantine ---------------------------------------------------------


def test_fix_writes_quarantine_with_one_entry_per_removed_vector(fixable, capsys):
    code = main(["fix", str(fixable)])
    assert code == 0

    quarantines = _quarantines(fixable.parent)
    assert len(quarantines) == 1
    lines = [json.loads(line) for line in quarantines[0].read_text().splitlines()]
    assert len(lines) == 2
    assert {entry["code"] for entry in lines} == {"orphaned_vector", "degenerate_vector"}
    for entry in lines:
        assert set(entry) == {"ts", "code", "id", "vector_rowid", "embedding_hex", "reason"}
        assert isinstance(entry["vector_rowid"], int)
        assert entry["embedding_hex"]


# -- snapshot failure ---------------------------------------------------


def test_fix_aborts_when_snapshot_cannot_be_written(fixable, capsys):
    before = _digest(fixable)
    os.chmod(fixable.parent, 0o555)
    try:
        code = main(["fix", str(fixable)])
    finally:
        os.chmod(fixable.parent, 0o755)
    after = _digest(fixable)
    assert code == 2
    assert before == after
    assert _backups(fixable.parent) == []


# -- integrity ----------------------------------------------------------


def test_fix_leaves_a_valid_sqlite_database(fixable, capsys):
    code = main(["fix", str(fixable)])
    assert code == 0
    conn = sqlite3.connect(str(fixable))
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


# -- json backend -------------------------------------------------------


def test_fix_json_reports_no_automatic_repairs(tmp_path, capsys):
    store = write_json_store(tmp_path, {"m.json": [memory("a", embedding=[1.0, 2.0])]})
    code = main(["fix", str(store)])
    captured = capsys.readouterr()
    assert code == 2
    assert "not available" in captured.err
