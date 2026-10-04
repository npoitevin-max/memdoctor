"""Tests for the JSON / JSONL backend and the detection checks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fixtures import memory, write_json_store, write_jsonl_store
from memdoctor.backends.json_dir import JsonDirStore
from memdoctor.checks import run_checks
from memdoctor.cli import main


def run_cli(capsys, *argv):
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def codes_for(store: JsonDirStore) -> set[str]:
    return {i.code for i in run_checks(store.iter_memories())}


def issues_for(store: JsonDirStore, code: str):
    return [i for i in run_checks(store.iter_memories()) if i.code == code]


# -- detection classes --------------------------------------------------


def test_orphaned_vector_detected(tmp_path):
    store = write_json_store(tmp_path, {"m.json": [memory("a", embedded=True), memory("b", embedding=[1.0, 2.0])]})
    issues = issues_for(JsonDirStore(store), "orphaned_vector")
    assert len(issues) == 1
    assert issues[0].id == "a"


def test_duplicate_id_detected(tmp_path):
    store = write_json_store(tmp_path, {"m.json": [memory("dup"), memory("dup"), memory("other")]})
    issues = issues_for(JsonDirStore(store), "duplicate_id")
    assert len(issues) == 1
    assert issues[0].id == "dup"


def test_dimension_mismatch_detected(tmp_path):
    store = write_json_store(
        tmp_path,
        {
            "m.json": [
                memory("a", embedding=[1.0, 2.0, 3.0]),
                memory("b", embedding=[4.0, 5.0, 6.0]),
                memory("c", embedding=[1.0, 2.0]),
            ]
        },
    )
    issues = issues_for(JsonDirStore(store), "dimension_mismatch")
    assert len(issues) == 1
    assert issues[0].id == "c"


def test_missing_field_detected(tmp_path):
    store = write_json_store(tmp_path, {"m.json": [memory("a", content=None), memory("b")]})
    issues = issues_for(JsonDirStore(store), "missing_field")
    assert len(issues) == 1
    assert issues[0].id == "a"


def test_degenerate_vector_detected(tmp_path):
    store = write_json_store(
        tmp_path,
        {"m.json": [memory("a", embedding=[0.0, 0.0, 0.0]), memory("b", embedding=[1.0, 2.0, 3.0])]},
    )
    issues = issues_for(JsonDirStore(store), "degenerate_vector")
    assert len(issues) == 1
    assert issues[0].id == "a"


def test_jsonl_backend_parses(tmp_path):
    store = write_jsonl_store(
        tmp_path,
        {"m.jsonl": [memory("a", embedding=[1.0, 2.0]), memory("dup"), memory("dup")]},
    )
    codes = codes_for(JsonDirStore(store))
    assert "duplicate_id" in codes
    assert "orphaned_vector" not in codes


def test_json_backend_ignores_memdoctor_artifacts(tmp_path):
    store = write_json_store(tmp_path, {"memories.json": [memory("a", embedding=[1.0, 2.0])]})
    (store / "memories.memdoctor-quarantine.jsonl").write_text(
        json.dumps({"id": "x"}) + "\n", encoding="utf-8"
    )
    (store / "memories.memdoctor-backup-2024-01-01T000000000000Z.jsonl").write_text(
        json.dumps({"id": "y"}) + "\n", encoding="utf-8"
    )
    s = JsonDirStore(store)
    assert s.stats()["memories"] == 1
    assert run_checks(s.iter_memories()) == []


def test_explicit_json_backend_ignores_db(tmp_path, capsys):
    store = write_json_store(tmp_path, {"m.json": [memory("a", embedding=[1.0, 2.0])]})
    (store / "data.db").write_bytes(b"")
    code, out, _ = run_cli(capsys, "check", "--backend", "json", str(store))
    assert code == 0
    assert "json backend" in out


# -- clean store / false positives --------------------------------------


def test_clean_store_has_zero_issues(tmp_path, capsys):
    store = write_json_store(
        tmp_path,
        {"m.json": [memory("a", embedding=[1.0, 2.0, 3.0]), memory("b", embedding=[4.0, 5.0, 6.0])]},
    )
    code, out, _ = run_cli(capsys, "check", str(store))
    assert code == 0
    assert "no issues found" in out


def test_issues_exit_code_one(tmp_path, capsys):
    store = write_json_store(tmp_path, {"m.json": [memory("dup"), memory("dup")]})
    code, out, _ = run_cli(capsys, "check", str(store))
    assert code == 1
    assert "D2" in out


# -- JSON output --------------------------------------------------------


def test_json_output_is_a_single_valid_object(tmp_path, capsys):
    store = write_json_store(tmp_path, {"m.json": [memory("a", embedding=[1.0, 2.0])]})
    code, out, _ = run_cli(capsys, "check", str(store), "--json")
    parsed = json.loads(out)
    assert code == 0
    assert set(parsed) == {"version", "backend", "store", "counts", "issues", "duration_ms"}
    assert parsed["backend"] == "json"
    assert parsed["counts"]["memories"] == 1
    assert isinstance(parsed["issues"], list)
    assert isinstance(parsed["duration_ms"], float)


# -- immutability -------------------------------------------------------


def test_check_does_not_modify_store(tmp_path, capsys):
    store = write_json_store(tmp_path, {"m.json": [memory("a", embedding=[1.0, 2.0])]})

    def digest() -> str:
        h = hashlib.sha256()
        for f in sorted(store.iterdir()):
            h.update(f.name.encode())
            h.update(f.read_bytes())
        return h.hexdigest()

    before = digest()
    code, _, _ = run_cli(capsys, "check", str(store))
    after = digest()
    assert code == 0
    assert before == after


# -- malformed input ----------------------------------------------------


def test_malformed_json_is_reported_not_a_crash(tmp_path, capsys):
    store = tmp_path / "memory"
    store.mkdir()
    (store / "bad.json").write_text("{not valid json", encoding="utf-8")
    (store / "good.json").write_text(json.dumps([memory("a")]), encoding="utf-8")
    code, out, _ = run_cli(capsys, "check", str(store))
    assert code == 1
    assert "malformed" in out
