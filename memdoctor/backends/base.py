"""Backend-agnostic interface for memory stores."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterator

from ..model import Issue, Memory


class BackendError(Exception):
    """Raised when a store cannot be recognised or opened."""


class MemoryStore(ABC):
    """A normalised, backend-agnostic view over a memory store."""

    backend: str = ""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    @abstractmethod
    def iter_memories(self) -> Iterator[Memory]:
        """Yield every memory in the store, in a stable order."""

    @abstractmethod
    def stats(self) -> dict[str, int]:
        """Return counts for the store (e.g. memories, vectors, files)."""

    def orphan_vector_ids(self) -> list[str]:
        """Vector ids with no matching memory row.

        Backends whose vectors are stored separately from memories override this.
        """
        return []

    def backend_issues(self) -> list[Issue]:
        """Backend-specific structural issues (e.g. malformed input)."""
        return []


def _has_json_files(p: Path) -> bool:
    """Return True when ``p`` is a directory containing ``.json``/``.jsonl`` files."""
    if not p.is_dir():
        return False
    suffixes = {f.suffix.lower() for f in p.iterdir() if f.is_file()}
    return bool(suffixes & {".json", ".jsonl"})


def detect_backend(path: str | os.PathLike[str]) -> str:
    """Infer the backend from the path.

    SQLite is preferred: a directory is first offered to the SQLite detector and
    only falls back to the JSON backend when no sqlite-vec store is present.
    ``detect_sqlite`` is imported lazily so the JSON backend keeps working when
    ``sqlite-vec`` is not installed.
    """
    p = Path(path)
    if not p.exists():
        raise BackendError(f"path does not exist: {p}")

    try:
        from .sqlite_vec import detect_sqlite
    except ImportError:  # pragma: no cover - the module handles its own import
        detect_sqlite = None

    if detect_sqlite is not None:
        try:
            if detect_sqlite(p):
                return "sqlite"
        except BackendError:
            pass

    if _has_json_files(p):
        return "json"

    if detect_sqlite is None:
        raise BackendError(
            f"cannot detect backend for {p}: no .json/.jsonl memory files found "
            f"and the SQLite backend is not available"
        )
    raise BackendError(f"cannot detect backend for {p}: unrecognised store layout")
