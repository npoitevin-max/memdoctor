"""Command-line interface for memdoctor."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__
from .backends.base import BackendError, MemoryStore, detect_backend
from .backends.json_dir import JsonDirStore
from .checks import run_checks
from .model import Issue, Report, Severity

_CODE_META: dict[str, tuple[str, str]] = {
    "orphaned_vector": ("D1", "orphaned vectors"),
    "duplicate_id": ("D2", "duplicate ids"),
    "dimension_mismatch": ("D3", "dimension mismatches"),
    "missing_field": ("D4", "missing required fields"),
    "degenerate_vector": ("D5", "degenerate vectors"),
    "malformed_json": ("", "malformed input"),
}
_ORDER = list(_CODE_META)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memdoctor",
        description="Check and repair AI agent long-term memory stores.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="report store integrity (never writes)")
    check.add_argument("path", help="path to the memory store")
    check.add_argument("--json", action="store_true", dest="json_output", help="emit a single JSON object")
    check.add_argument("--backend", choices=["auto", "json", "sqlite"], default="auto")
    check.add_argument("--dim", type=int, default=None, metavar="N", help="expected embedding dimension")

    fix = sub.add_parser("fix", help="repair the store (not implemented in this step)")
    fix.add_argument("path")
    fix.add_argument("--dry-run", action="store_true")
    fix.add_argument("--quarantine-dir", default=None)
    fix.add_argument("--backend", choices=["auto", "json", "sqlite"], default="auto")
    fix.add_argument("--dim", type=int, default=None, metavar="N")

    sub.add_parser("version", help="print the version and exit")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "check":
            return _cmd_check(args)
        if args.command == "fix":
            return _cmd_fix(args)
        if args.command == "version":
            print(f"memdoctor {__version__}")
            return 0
        return 2
    except BackendError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


def _cmd_fix(args: argparse.Namespace) -> int:
    print("error: `fix` is not implemented in this step; only `check` is available.", file=sys.stderr)
    return 2


def _resolve_backend(path: str, backend: str) -> str:
    if backend == "auto":
        return detect_backend(path)
    return backend


def _build_store(path: str, backend: str) -> MemoryStore:
    if backend == "json":
        return JsonDirStore(path)
    if backend == "sqlite":
        raise BackendError("the SQLite backend is not implemented in this step; use a JSON/JSONL store")
    raise BackendError(f"unknown backend: {backend}")


def _cmd_check(args: argparse.Namespace) -> int:
    start = time.perf_counter()
    backend = _resolve_backend(args.path, args.backend)
    store = _build_store(args.path, backend)

    issues = list(store.backend_issues())
    issues.extend(run_checks(store.iter_memories(), dimension=args.dim))
    duration_ms = (time.perf_counter() - start) * 1000.0

    report = Report(
        version=__version__,
        backend=backend,
        store=str(Path(args.path)),
        counts=store.stats(),
        issues=issues,
        duration_ms=duration_ms,
    )

    if args.json_output:
        print(json.dumps(report.to_dict()))
    else:
        _print_human(report)

    return 1 if issues else 0


def _use_color() -> bool:
    try:
        return sys.stdout.isatty()
    except (AttributeError, OSError):
        return False


def _print_human(report: Report) -> None:
    color = _use_color()
    out = sys.stdout

    def emit(line: str = "") -> None:
        out.write(line + "\n")

    def c(text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if color else text

    emit(f"memdoctor v{report.version} — {report.backend} backend")
    emit()
    emit(f"  {'store':<12} {report.store}")
    for key, label in (("memories", "memories"), ("vectors", "vectors"), ("files", "files")):
        if key in report.counts:
            emit(f"  {label:<12} {report.counts[key]}")
    if report.counts.get("malformed"):
        emit(f"  {'malformed':<12} {report.counts['malformed']}")
    emit()

    groups: dict[str, list[Issue]] = {}
    for issue in report.issues:
        groups.setdefault(issue.code, []).append(issue)

    for code in _ORDER:
        issues = groups.get(code)
        if not issues:
            continue
        dcode, label = _CODE_META[code]
        prefix = f"{dcode} {label}" if dcode else label
        mark = "✗" if issues[0].severity == Severity.ERROR else "!"
        colour = "31" if issues[0].severity == Severity.ERROR else "33"
        line = f"  {mark} {prefix}"
        pad = max(2, 44 - len(line))
        emit(c(line, colour) + ("." * pad) + f" {len(issues)} found")
        ids = [i.id for i in issues if i.id]
        if ids:
            emit(f"         ids: {', '.join(ids[:3])}")

    emit()
    total = len(report.issues)
    if total == 0:
        emit("  no issues found")
    else:
        emit(
            f"  {total} issue(s) found in {report.duration_ms / 1000.0:.1f}s. "
            f"Run `memdoctor fix {report.store}/` to repair."
        )
