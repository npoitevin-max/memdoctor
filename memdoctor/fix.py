"""Repair planner and executor for memdoctor.

``fix`` is the first code path that writes to a user's store, so safety is the
dominant concern:

* analyse with a read-only connection (identical guarantee to ``check``);
* snapshot the ``.db`` file before mutating anything;
* quarantine every removed vector row before deleting it;
* apply all mutations inside a single SQLite transaction that rolls back on
  any failure;
* never touch a memory row.

Only the SQLite backend is supported in v0.1, and only two repairs are safe:

* **orphaned_vector** — a vector row whose id has no matching memory row;
* **degenerate_vector** — an all-zero / NaN / Inf / zero-norm vector row.

Everything else is reported, never repaired.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .backends.base import BackendError
from .backends.sqlite_vec import (
    _MISSING_MSG,
    _candidate_memory_tables,
    _decode_blob,
    _find_vec_table,
    _id_str,
    _quote_ident,
    _read_master,
    _resolve_db,
    _select_memory_table,
    _open_readonly,
    sqlite_vec,
)
from .checks import _is_degenerate, run_checks
from .model import Issue


@dataclass
class Mutation:
    """A single planned vector-row removal."""

    code: str
    vec_id: str | None
    rowid: int
    embedding_hex: str
    reason: str

    def quarantine_line(self, ts: str) -> str:
        return json.dumps(
            {
                "ts": ts,
                "code": self.code,
                "id": self.vec_id,
                "vector_rowid": self.rowid,
                "embedding_hex": self.embedding_hex,
                "reason": self.reason,
            },
            separators=(",", ":"),
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _backup_stamp() -> str:
    return _utc_now().strftime("%Y-%m-%dT%H%M%S%fZ")


def _blob_hex(blob: object) -> str:
    if isinstance(blob, (bytes, bytearray, memoryview)):
        return bytes(blob).hex()
    return ""


def _open_rw(db: Path) -> sqlite3.Connection:
    if sqlite_vec is None:
        raise BackendError(_MISSING_MSG)
    try:
        conn = sqlite3.connect(str(db), isolation_level=None)
    except sqlite3.Error as exc:
        raise BackendError(f"cannot open {db}: {exc}") from exc
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
    except Exception as exc:
        conn.close()
        raise BackendError(f"failed to load sqlite-vec extension: {exc}") from exc
    return conn


def _plan(db: Path) -> tuple[str, list[Mutation]]:
    """Return the vec-table name and the safe vector-row removals for ``db``."""
    conn = _open_readonly(db)
    try:
        rows = _read_master(conn, db)
        vec_table = _find_vec_table(rows)
        if vec_table is None:
            names = [r["name"] for r in rows if r["name"]]
            raise BackendError(
                f"no vec0 virtual table found in {db}; tables found: {', '.join(names) or '(none)'}"
            )
        vec_name = vec_table["name"]
        candidates = _candidate_memory_tables(conn, rows, vec_name)
        mem_table = _select_memory_table(candidates, db)

        mem_ids = {
            mid
            for mid in (
                _id_str(row[0])
                for row in conn.execute(
                    f"SELECT id FROM {_quote_ident(mem_table)}"
                ).fetchall()
            )
            if mid is not None
        }

        mutations: list[Mutation] = []
        for rowid, vid, blob in conn.execute(
            f"SELECT rowid, id, embedding FROM {_quote_ident(vec_name)}"
        ).fetchall():
            key = _id_str(vid)
            if key is None:
                continue
            if key not in mem_ids:
                mutations.append(
                    Mutation(
                        code="orphaned_vector",
                        vec_id=key,
                        rowid=int(rowid),
                        embedding_hex=_blob_hex(blob),
                        reason="vector row has no matching memory row",
                    )
                )
                continue
            embedding = _decode_blob(blob)
            if embedding is not None and _is_degenerate(embedding):
                mutations.append(
                    Mutation(
                        code="degenerate_vector",
                        vec_id=key,
                        rowid=int(rowid),
                        embedding_hex=_blob_hex(blob),
                        reason="embedding is all-zero, non-finite, or has zero norm",
                    )
                )
        return vec_name, mutations
    finally:
        conn.close()


def _snapshot(db: Path) -> Path:
    backup = db.with_name(f"{db.name}.memdoctor-backup-{_backup_stamp()}")
    try:
        shutil.copyfile(db, backup)
    except OSError as exc:
        raise BackendError(f"cannot write backup snapshot {backup}: {exc}") from exc
    return backup


def _quarantine_path(db: Path, quarantine_dir: str | None) -> Path:
    name = f"{db.name}.memdoctor-quarantine.jsonl"
    if quarantine_dir:
        return Path(quarantine_dir) / name
    return db.with_name(name)


def _write_quarantine(path: Path, mutations: list[Mutation]) -> None:
    ts = _utc_now().isoformat(timespec="microseconds")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for mutation in mutations:
                fh.write(mutation.quarantine_line(ts) + "\n")
    except OSError as exc:
        raise BackendError(f"cannot write quarantine file {path}: {exc}") from exc


def _delete_rows(db: Path, vec_name: str, mutations: list[Mutation]) -> None:
    conn = _open_rw(db)
    try:
        conn.execute("BEGIN")
        try:
            for mutation in mutations:
                conn.execute(
                    f"DELETE FROM {_quote_ident(vec_name)} WHERE rowid = ?",
                    (mutation.rowid,),
                )
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.close()


def _remove_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _report_only_issues(issues: list[Issue]) -> list[Issue]:
    out: list[Issue] = []
    for issue in issues:
        if issue.code == "degenerate_vector":
            continue
        if issue.code == "orphaned_vector" and issue.detail == "vector row has no matching memory row":
            continue
        out.append(issue)
    return out


def _print_result(
    db: Path,
    mutations: list[Mutation],
    report_only: list[Issue],
    backup: Path | None,
    quarantine: Path | None,
    dry_run: bool,
) -> None:
    print("memdoctor fix — sqlite backend")
    print(f"  store        {db}")
    if dry_run:
        print("  (dry run — no writes performed)")
    else:
        if backup is not None:
            print(f"  backup       {backup}")
        if quarantine is not None:
            print(f"  quarantine   {quarantine}")
    print()

    if mutations:
        for mutation in mutations:
            verb = "would remove" if dry_run else "removed"
            print(
                f"  {verb} {mutation.code:<20} rowid={mutation.rowid} "
                f"id={mutation.vec_id}  {mutation.reason}"
            )
    else:
        print("  nothing to repair")
    print()

    if report_only:
        print("  not automatically repairable:")
        for issue in report_only:
            print(f"    {issue.code} id={issue.id}  {issue.detail}")
    else:
        print("  no other issues found")


def repair_sqlite(
    path: str | Path,
    *,
    dry_run: bool = False,
    quarantine_dir: str | None = None,
    dimension: int | None = None,
) -> int:
    db = _resolve_db(Path(path)).resolve()

    store = _load_store(db)
    issues = run_checks(
        store.iter_memories(),
        dimension=dimension,
        orphan_vector_ids=store.orphan_vector_ids(),
    )
    report_only = _report_only_issues(issues)

    vec_name, mutations = _plan(db)

    if not mutations:
        _print_result(db, mutations, report_only, None, None, dry_run)
        return 0

    if dry_run:
        _print_result(db, mutations, report_only, None, None, dry_run)
        return 0

    backup = _snapshot(db)
    quarantine = _quarantine_path(db, quarantine_dir)
    _write_quarantine(quarantine, mutations)
    try:
        _delete_rows(db, vec_name, mutations)
    except BaseException:
        _remove_if_exists(quarantine)
        raise

    _print_result(db, mutations, report_only, backup, quarantine, dry_run)
    return 0


def _load_store(db: Path):
    from .backends.sqlite_vec import SqliteVecStore

    return SqliteVecStore(db)
