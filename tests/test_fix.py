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

from fixtures import memory, write_json_store, write_jsonl_store
from memdoctor.backends.json_dir import JsonDirStore
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


# -- embedded flag ------------------------------------------------------


def build_embedded_degenerate(root: Path) -> Path:
    """A memory marked embedded whose vector is degenerate (the phantom-D1 case)."""
    db = root / "memories.db"
    conn = sqlite3.connect(str(db))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.execute("CREATE VIRTUAL TABLE vec_memories USING vec0(id TEXT, embedding FLOAT[4])")
    conn.execute("CREATE TABLE memories (id TEXT, content TEXT, created_at TEXT, embedded INTEGER)")
    conn.execute(
        "INSERT INTO memories (id, content, created_at, embedded) VALUES (?, ?, ?, ?)",
        ("c", "degenerate memory", "2024-01-01T00:00:00Z", 1),
    )
    conn.execute(
        "INSERT INTO memories (id, content, created_at, embedded) VALUES (?, ?, ?, ?)",
        ("fine", "fine memory", "2024-01-02T00:00:00Z", 0),
    )
    conn.execute(
        "INSERT INTO vec_memories (id, embedding) VALUES (?, ?)",
        ("c", _vec_blob([0.0, 0.0, 0.0, 0.0])),
    )
    conn.execute(
        "INSERT INTO vec_memories (id, embedding) VALUES (?, ?)",
        ("fine", _vec_blob([1.0, 2.0, 3.0, 4.0])),
    )
    conn.commit()
    conn.close()
    return db


def _embedded_value(db: Path, mem_id: str) -> object:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(
            "SELECT embedded FROM memories WHERE id = ?", (mem_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def test_fix_clears_embedded_flag_and_check_reports_no_d1(tmp_path, capsys):
    db = build_embedded_degenerate(tmp_path)
    code = main(["fix", str(db)])
    assert code == 0
    assert _embedded_value(db, "c") == 0

    check_code = main(["check", str(db)])
    out = capsys.readouterr().out
    assert check_code == 0
    assert "no issues found" in out


def test_fix_preserves_content_id_created_at(tmp_path, capsys):
    db = build_embedded_degenerate(tmp_path)
    conn = sqlite3.connect(str(db))
    before = conn.execute(
        "SELECT id, content, created_at FROM memories WHERE id = 'c'"
    ).fetchone()
    conn.close()

    code = main(["fix", str(db)])
    assert code == 0

    conn = sqlite3.connect(str(db))
    after = conn.execute(
        "SELECT id, content, created_at FROM memories WHERE id = 'c'"
    ).fetchone()
    conn.close()
    assert after == ("c", "degenerate memory", "2024-01-01T00:00:00Z")
    assert before == after


def test_fix_embedded_degenerate_keeps_memory_row_count(tmp_path, capsys):
    db = build_embedded_degenerate(tmp_path)
    before = _memory_count(db)
    code = main(["fix", str(db)])
    assert code == 0
    assert _memory_count(db) == before


def test_dry_run_reports_planned_unembed_and_writes_nothing(tmp_path, capsys):
    db = build_embedded_degenerate(tmp_path)
    before = _digest(db)
    code = main(["fix", "--dry-run", str(db)])
    out = capsys.readouterr().out
    after = _digest(db)
    assert code == 0
    assert before == after
    assert _backups(db.parent) == []
    assert _quarantines(db.parent) == []
    assert "would mark unembedded" in out
    assert "id=c" in out


def test_fix_failed_transaction_rolls_back_delete_and_flag(tmp_path, capsys):
    db = build_embedded_degenerate(tmp_path)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TRIGGER fail_unembed BEFORE UPDATE OF embedded ON memories "
        "BEGIN SELECT RAISE(FAIL, 'boom'); END"
    )
    conn.commit()
    conn.close()
    before = _digest(db)

    with pytest.raises(sqlite3.Error):
        main(["fix", str(db)])

    assert _digest(db) == before


def test_fix_without_embedded_column_still_fixes(tmp_path, capsys):
    db = tmp_path / "memories.db"
    conn = sqlite3.connect(str(db))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.execute("CREATE VIRTUAL TABLE vec_memories USING vec0(id TEXT, embedding FLOAT[4])")
    conn.execute("CREATE TABLE memories (id TEXT, content TEXT, created_at TEXT)")
    conn.execute(
        "INSERT INTO memories (id, content, created_at) VALUES (?, ?, ?)",
        ("c", "degenerate memory", "2024-01-01T00:00:00Z"),
    )
    conn.execute(
        "INSERT INTO vec_memories (id, embedding) VALUES (?, ?)",
        ("c", _vec_blob([0.0, 0.0, 0.0, 0.0])),
    )
    conn.commit()
    conn.close()

    code = main(["fix", str(db)])
    out = capsys.readouterr().out
    assert code == 0
    assert "no `embedded` column" in out

    check_code = main(["check", str(db)])
    assert check_code == 0


# -- multiple vector rows per id ----------------------------------------


def build_mixed_vector_rows(root: Path) -> Path:
    """A memory with one valid and one degenerate vector row (same id)."""
    db = root / "memories.db"
    conn = sqlite3.connect(str(db))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.execute("CREATE VIRTUAL TABLE vec_memories USING vec0(id TEXT, embedding FLOAT[4])")
    conn.execute("CREATE TABLE memories (id TEXT, content TEXT, created_at TEXT, embedded INTEGER)")
    conn.execute(
        "INSERT INTO memories (id, content, created_at, embedded) VALUES (?, ?, ?, ?)",
        ("m2", "mixed memory", "2024-01-01T00:00:00Z", 1),
    )
    conn.execute(
        "INSERT INTO vec_memories (id, embedding) VALUES (?, ?)",
        ("m2", _vec_blob([1.0, 2.0, 3.0, 4.0])),
    )
    conn.execute(
        "INSERT INTO vec_memories (id, embedding) VALUES (?, ?)",
        ("m2", _vec_blob([0.0, 0.0, 0.0, 0.0])),
    )
    conn.commit()
    conn.close()
    return db


def build_single_degenerate(root: Path) -> Path:
    """A memory whose only vector row is degenerate (embedded = 1)."""
    db = root / "memories.db"
    conn = sqlite3.connect(str(db))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.execute("CREATE VIRTUAL TABLE vec_memories USING vec0(id TEXT, embedding FLOAT[4])")
    conn.execute("CREATE TABLE memories (id TEXT, content TEXT, created_at TEXT, embedded INTEGER)")
    conn.execute(
        "INSERT INTO memories (id, content, created_at, embedded) VALUES (?, ?, ?, ?)",
        ("c", "degenerate memory", "2024-01-01T00:00:00Z", 1),
    )
    conn.execute(
        "INSERT INTO vec_memories (id, embedding) VALUES (?, ?)",
        ("c", _vec_blob([0.0, 0.0, 0.0, 0.0])),
    )
    conn.commit()
    conn.close()
    return db


def _assert_self_consistent(db: Path) -> None:
    conn = sqlite3.connect(str(db))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    try:
        vec_ids = {row[0] for row in conn.execute("SELECT id FROM vec_memories").fetchall()}
        for mem_id, embedded in conn.execute("SELECT id, embedded FROM memories").fetchall():
            has_vector = mem_id in vec_ids
            assert bool(embedded) == has_vector, (mem_id, embedded, has_vector)
    finally:
        conn.close()


def test_fix_keeps_valid_vector_and_embedded_flag_when_other_vector_remains(
    tmp_path, capsys
):
    db = build_mixed_vector_rows(tmp_path)
    code = main(["fix", str(db)])
    out = capsys.readouterr().out
    assert code == 0
    assert "embedded flag left alone" in out

    conn = sqlite3.connect(str(db))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    rows = conn.execute("SELECT embedding FROM vec_memories WHERE id = 'm2'").fetchall()
    conn.close()
    assert len(rows) == 1
    assert list(struct.unpack("<4f", rows[0][0])) == [1.0, 2.0, 3.0, 4.0]

    assert _embedded_value(db, "m2") == 1
    _assert_self_consistent(db)

    check_code = main(["check", str(db)])
    out = capsys.readouterr().out
    assert check_code == 0
    assert "no issues found" in out


def test_fix_single_degenerate_vector_removes_row_and_clears_embedded(tmp_path, capsys):
    db = build_single_degenerate(tmp_path)
    code = main(["fix", str(db)])
    out = capsys.readouterr().out
    assert code == 0
    assert "marked unembedded" in out

    conn = sqlite3.connect(str(db))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    count = conn.execute(
        "SELECT COUNT(*) FROM vec_memories WHERE id = 'c'"
    ).fetchone()[0]
    conn.close()
    assert count == 0
    assert _embedded_value(db, "c") == 0
    _assert_self_consistent(db)


# -- json backend -------------------------------------------------------


def test_fix_json_round_trip_and_convergence(tmp_path, capsys):
    store = write_json_store(
        tmp_path,
        {
            "m.json": [
                memory("a", embedding=[0.0, 0.0, 0.0]),
                memory("b", embedding=[1.0, 2.0, 3.0]),
            ]
        },
    )
    code = main(["fix", str(store)])
    assert code == 0

    check_code = main(["check", str(store)])
    out = capsys.readouterr().out
    assert check_code == 0
    assert "no issues found" in out

    mems = {m.id: m for m in JsonDirStore(store).iter_memories()}
    assert mems["a"].embedding is None
    assert mems["a"].embedded is False
    assert mems["b"].embedding == [1.0, 2.0, 3.0]

    code = main(["fix", str(store)])
    out = capsys.readouterr().out
    assert code == 0
    assert "nothing to repair" in out


def test_fix_json_marks_embedded_degenerate_unembedded(tmp_path, capsys):
    store = write_json_store(
        tmp_path,
        {
            "m.json": [
                memory("c", embedding=[0.0, 0.0], embedded=True),
                memory("fine", embedding=[1.0, 2.0]),
            ]
        },
    )
    code = main(["fix", str(store)])
    assert code == 0

    objs = json.loads((store / "m.json").read_text())
    assert "embedding" not in objs[0]
    assert objs[0]["embedded"] is False
    assert objs[0]["id"] == "c"
    assert objs[0]["content"] == "hello"

    check_code = main(["check", str(store)])
    out = capsys.readouterr().out
    assert check_code == 0
    assert "no issues found" in out


def test_fix_json_writes_quarantine(tmp_path, capsys):
    store = write_json_store(tmp_path, {"m.json": [memory("a", embedding=[0.0, 0.0])]})
    code = main(["fix", str(store)])
    assert code == 0

    q = store.parent / f"{store.name}.memdoctor-quarantine.jsonl"
    lines = [json.loads(line) for line in q.read_text().splitlines()]
    assert len(lines) == 1
    entry = lines[0]
    assert set(entry) == {"ts", "code", "id", "embedding", "reason"}
    assert entry["code"] == "degenerate_vector"
    assert entry["id"] == "a"
    assert entry["embedding"] == [0.0, 0.0]


def test_fix_json_creates_backup(tmp_path, capsys):
    store = write_json_store(tmp_path, {"m.json": [memory("a", embedding=[0.0, 0.0])]})
    before = (store / "m.json").read_bytes()
    code = main(["fix", str(store)])
    assert code == 0
    backups = sorted(store.glob("*.memdoctor-backup-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == before


def test_fix_json_dry_run_leaves_store_byte_identical(tmp_path, capsys):
    store = write_json_store(
        tmp_path,
        {
            "m.json": [
                memory("a", embedding=[0.0, 0.0]),
                memory("b", embedding=[1.0, 2.0]),
            ]
        },
    )

    def digest() -> str:
        h = hashlib.sha256()
        for f in sorted(store.iterdir()):
            h.update(f.name.encode())
            h.update(f.read_bytes())
        return h.hexdigest()

    before = digest()
    code = main(["fix", "--dry-run", str(store)])
    out = capsys.readouterr().out
    assert code == 0
    assert "would remove" in out
    assert digest() == before
    assert list(store.glob("*.memdoctor-backup-*")) == []
    assert not (store.parent / f"{store.name}.memdoctor-quarantine.jsonl").exists()


def test_fix_json_aborts_when_snapshot_cannot_be_written(tmp_path, capsys):
    store = write_json_store(tmp_path, {"m.json": [memory("a", embedding=[0.0, 0.0])]})

    def digest() -> str:
        h = hashlib.sha256()
        for f in sorted(store.iterdir()):
            h.update(f.name.encode())
            h.update(f.read_bytes())
        return h.hexdigest()

    before = digest()
    os.chmod(store, 0o555)
    try:
        code = main(["fix", str(store)])
    finally:
        os.chmod(store, 0o755)
    assert code == 2
    assert digest() == before
    assert list(store.glob("*.memdoctor-backup-*")) == []


def test_fix_json_report_only_issues_never_modified(tmp_path, capsys):
    store = write_json_store(
        tmp_path,
        {
            "m.json": [
                memory("dup", content="first"),
                memory("dup", content="second"),
                memory("missing", content=None),
            ]
        },
    )
    before = (store / "m.json").read_text()
    code = main(["fix", str(store)])
    out = capsys.readouterr().out
    assert code == 0
    assert "nothing to repair" in out
    assert "not automatically repairable" in out
    assert "duplicate_id" in out
    assert "missing_field" in out
    assert (store / "m.json").read_text() == before


def test_fix_jsonl_rewrites_only_affected_lines(tmp_path, capsys):
    store = write_jsonl_store(
        tmp_path,
        {
            "m.jsonl": [
                memory("a", embedding=[0.0, 0.0]),
                memory("b", embedding=[1.0, 2.0]),
                memory("c"),
            ]
        },
    )
    path = store / "m.jsonl"
    before_lines = path.read_bytes().splitlines(keepends=True)
    code = main(["fix", str(store)])
    assert code == 0
    after_lines = path.read_bytes().splitlines(keepends=True)
    assert after_lines[0] != before_lines[0]
    assert b"embedding" not in after_lines[0]
    assert after_lines[1] == before_lines[1]
    assert after_lines[2] == before_lines[2]


def test_check_hint_suppressed_for_report_only_json(tmp_path, capsys):
    store = write_json_store(tmp_path, {"m.json": [memory("dup"), memory("dup")]})
    code = main(["check", str(store)])
    out = capsys.readouterr().out
    assert code == 1
    assert "Run `memdoctor fix" not in out
    assert "None of these are automatically repairable" in out


def test_check_hint_present_for_repairable_json(tmp_path, capsys):
    store = write_json_store(tmp_path, {"m.json": [memory("a", embedding=[0.0, 0.0])]})
    code = main(["check", str(store)])
    out = capsys.readouterr().out
    assert code == 1
    assert "Run `memdoctor fix" in out
