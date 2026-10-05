"""Repair planner and executor for memdoctor.

``fix`` is the first code path that writes to a user's store, so safety is the
dominant concern:

* analyse with a read-only connection (identical guarantee to ``check``);
* snapshot the ``.db`` file before mutating anything;
* quarantine every removed vector row before deleting it;
* apply all mutations inside a single SQLite transaction that rolls back on
  any failure;
* never write a memory row's ``content``, ``id`` or ``created_at`` — the only
  permitted write to a memory row is clearing its ``embedded`` flag when
  ``fix`` removes that memory's vector.

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
from .backends.json_dir import JsonDirStore, _is_memdoctor_artifact
from .backends.sqlite_vec import (
    _MISSING_MSG,
    _candidate_memory_tables,
    _columns,
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


@dataclass
class Plan:
    """The vec-table name, memory-table shape, and safe removals for ``db``."""

    vec_table: str
    mem_table: str
    has_embedded: bool
    mutations: list[Mutation]


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


def _plan(db: Path) -> Plan:
    """Return the safe vector-row removals for ``db`` and its memory-table shape."""
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
        has_embedded = "embedded" in _columns(conn, mem_table)

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
        return Plan(
            vec_table=vec_name,
            mem_table=mem_table,
            has_embedded=has_embedded,
            mutations=mutations,
        )
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


def _delete_rows(
    db: Path,
    vec_name: str,
    mem_table: str,
    has_embedded: bool,
    mutations: list[Mutation],
) -> None:
    conn = _open_rw(db)
    try:
        conn.execute("BEGIN")
        try:
            for mutation in mutations:
                conn.execute(
                    f"DELETE FROM {_quote_ident(vec_name)} WHERE rowid = ?",
                    (mutation.rowid,),
                )
                if has_embedded and mutation.code == "degenerate_vector":
                    conn.execute(
                        f"UPDATE {_quote_ident(mem_table)} SET embedded = 0 WHERE id = ?",
                        (mutation.vec_id,),
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


def _is_repairable(issue: Issue, backend: str) -> bool:
    """Return True when ``issue`` is a class that ``fix`` can repair for ``backend``."""
    if issue.code == "degenerate_vector":
        return True
    if (
        backend == "sqlite"
        and issue.code == "orphaned_vector"
        and issue.detail == "vector row has no matching memory row"
    ):
        return True
    return False


def _print_result(
    db: Path,
    plan: Plan,
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

    if plan.mutations:
        for mutation in plan.mutations:
            verb = "would remove" if dry_run else "removed"
            print(
                f"  {verb} {mutation.code:<20} rowid={mutation.rowid} "
                f"id={mutation.vec_id}  {mutation.reason}"
            )
            if mutation.code == "degenerate_vector":
                if plan.has_embedded:
                    uverb = "would mark unembedded" if dry_run else "marked unembedded"
                    print(
                        f"  {uverb:<22} id={mutation.vec_id}  "
                        f"no vector remains; needs re-embedding"
                    )
                else:
                    print("  (memory table has no `embedded` column; flag not updated)")
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

    plan = _plan(db)

    if not plan.mutations:
        _print_result(db, plan, report_only, None, None, dry_run)
        return 0

    if dry_run:
        _print_result(db, plan, report_only, None, None, dry_run)
        return 0

    backup = _snapshot(db)
    quarantine = _quarantine_path(db, quarantine_dir)
    _write_quarantine(quarantine, plan.mutations)
    try:
        _delete_rows(
            db, plan.vec_table, plan.mem_table, plan.has_embedded, plan.mutations
        )
    except BaseException:
        _remove_if_exists(quarantine)
        raise

    _print_result(db, plan, report_only, backup, quarantine, dry_run)
    return 0


def _load_store(db: Path):
    from .backends.sqlite_vec import SqliteVecStore

    return SqliteVecStore(db)


# -- JSON / JSONL backend ------------------------------------------------
#
# In the JSON backend a "vector" is the ``embedding`` field on a memory object,
# so the only repairable class is ``degenerate_vector``. Everything else is
# report-only and never modified.


_DEGENERATE_REASON = "embedding is all-zero, non-finite, or has zero norm"


@dataclass
class JsonMutation:
    """A single planned embedding removal in the JSON backend."""

    code: str
    id: str | None
    embedding: list[float]
    reason: str

    def quarantine_line(self, ts: str) -> str:
        return json.dumps(
            {
                "ts": ts,
                "code": self.code,
                "id": self.id,
                "embedding": self.embedding,
                "reason": self.reason,
            },
            separators=(",", ":"),
        )


@dataclass
class JsonPlan:
    """Files to rewrite and the degenerate embeddings to remove."""

    files: list[Path]
    mutations: list[JsonMutation]


def _json_store_files(store: Path) -> list[Path]:
    if store.is_dir():
        return sorted(
            f
            for f in store.iterdir()
            if f.is_file()
            and f.suffix.lower() in {".json", ".jsonl"}
            and not _is_memdoctor_artifact(f.name)
        )
    if store.is_file():
        return [store]
    raise BackendError(f"not a directory or file: {store}")


def _degenerate_embedding(obj: dict) -> list[float] | None:
    """Return ``obj``'s embedding when it is degenerate, else ``None``."""
    value = obj.get("embedding")
    if not isinstance(value, (list, tuple)):
        return None
    try:
        embedding = [float(x) for x in value]
    except (TypeError, ValueError):
        return None
    if _is_degenerate(embedding):
        return embedding
    return None


def _rewrite_object(obj: dict) -> None:
    """Remove a degenerate ``embedding`` and mark the object unembedded."""
    obj.pop("embedding", None)
    for key in ("embedded", "has_embedding"):
        if key in obj and obj[key]:
            obj[key] = False


def _json_mutation(obj: dict, embedding: list[float]) -> JsonMutation:
    return JsonMutation(
        code="degenerate_vector",
        id=_id_str(obj.get("id")),
        embedding=embedding,
        reason=_DEGENERATE_REASON,
    )


def _scan_json(raw: str) -> list[JsonMutation]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    mutations: list[JsonMutation] = []
    for obj in data:
        if not isinstance(obj, dict):
            continue
        embedding = _degenerate_embedding(obj)
        if embedding is not None:
            mutations.append(_json_mutation(obj, embedding))
    return mutations


def _scan_jsonl(raw: str) -> list[JsonMutation]:
    mutations: list[JsonMutation] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        embedding = _degenerate_embedding(obj)
        if embedding is not None:
            mutations.append(_json_mutation(obj, embedding))
    return mutations


def _plan_json(store: Path) -> JsonPlan:
    files: list[Path] = []
    mutations: list[JsonMutation] = []
    for f in _json_store_files(store):
        try:
            raw = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        found = _scan_jsonl(raw) if f.suffix.lower() == ".jsonl" else _scan_json(raw)
        if found:
            files.append(f)
            mutations.extend(found)
    return JsonPlan(files=files, mutations=mutations)


def _snapshot_json_files(files: list[Path]) -> list[Path]:
    """Copy every file about to change; abort and clean up on any failure."""
    stamp = _backup_stamp()
    backups: list[Path] = []
    try:
        for f in files:
            backup = f.with_name(f"{f.name}.memdoctor-backup-{stamp}")
            shutil.copyfile(f, backup)
            backups.append(backup)
    except OSError as exc:
        for b in backups:
            _remove_if_exists(b)
        raise BackendError(f"cannot write backup snapshot: {exc}") from exc
    return backups


def _quarantine_json_path(store: Path, quarantine_dir: str | None) -> Path:
    name = f"{store.name}.memdoctor-quarantine.jsonl"
    if quarantine_dir:
        return Path(quarantine_dir) / name
    return store.with_name(name)


def _write_json_quarantine(path: Path, mutations: list[JsonMutation]) -> None:
    ts = _utc_now().isoformat(timespec="microseconds")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for mutation in mutations:
                fh.write(mutation.quarantine_line(ts) + "\n")
    except OSError as exc:
        raise BackendError(f"cannot write quarantine file {path}: {exc}") from exc


def _rewrite_json(raw: str) -> str:
    data = json.loads(raw)
    for obj in data:
        if isinstance(obj, dict) and _degenerate_embedding(obj) is not None:
            _rewrite_object(obj)
    return json.dumps(data, ensure_ascii=False)


def _rewrite_jsonl(data: bytes) -> bytes:
    out: list[bytes] = []
    for line in data.splitlines(keepends=True):
        content = line.rstrip(b"\r\n")
        eol = line[len(content) :]
        try:
            obj = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            out.append(line)
            continue
        if not isinstance(obj, dict) or _degenerate_embedding(obj) is None:
            out.append(line)
            continue
        _rewrite_object(obj)
        new_content = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        out.append(new_content + eol)
    return b"".join(out)


def _apply_json_files(files: list[Path]) -> None:
    for f in files:
        if f.suffix.lower() == ".jsonl":
            f.write_bytes(_rewrite_jsonl(f.read_bytes()))
        else:
            f.write_text(_rewrite_json(f.read_text(encoding="utf-8")), encoding="utf-8")


def _print_json_result(
    store: Path,
    plan: JsonPlan,
    report_only: list[Issue],
    backups: list[Path] | None,
    quarantine: Path | None,
    dry_run: bool,
) -> None:
    print("memdoctor fix — json backend")
    print(f"  store        {store}")
    if dry_run:
        print("  (dry run — no writes performed)")
    else:
        if backups:
            for backup in backups:
                print(f"  backup       {backup}")
        if quarantine is not None:
            print(f"  quarantine   {quarantine}")
    print()

    if plan.mutations:
        for mutation in plan.mutations:
            verb = "would remove" if dry_run else "removed"
            print(f"  {verb} {mutation.code:<20} id={mutation.id}  {mutation.reason}")
            uverb = "would mark unembedded" if dry_run else "marked unembedded"
            print(f"  {uverb:<22} id={mutation.id}  no vector remains; needs re-embedding")
    else:
        print("  nothing to repair")
    print()

    if report_only:
        print("  not automatically repairable:")
        for issue in report_only:
            print(f"    {issue.code} id={issue.id}  {issue.detail}")
    else:
        print("  no other issues found")


def repair_json(
    path: str | Path,
    *,
    dry_run: bool = False,
    quarantine_dir: str | None = None,
    dimension: int | None = None,
) -> int:
    store = Path(path).resolve()
    json_store = JsonDirStore(store)
    issues = run_checks(
        json_store.iter_memories(),
        dimension=dimension,
        orphan_vector_ids=json_store.orphan_vector_ids(),
    )
    report_only = _report_only_issues(issues)

    plan = _plan_json(store)

    if not plan.mutations:
        _print_json_result(store, plan, report_only, None, None, dry_run)
        return 0

    if dry_run:
        _print_json_result(store, plan, report_only, None, None, dry_run)
        return 0

    backups = _snapshot_json_files(plan.files)
    quarantine = _quarantine_json_path(store, quarantine_dir)
    _write_json_quarantine(quarantine, plan.mutations)
    try:
        _apply_json_files(plan.files)
    except BaseException:
        _remove_if_exists(quarantine)
        raise

    _print_json_result(store, plan, report_only, backups, quarantine, dry_run)
    return 0
