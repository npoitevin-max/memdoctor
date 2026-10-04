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


def detect_backend(path: str | os.PathLike[str]) -> str:
    """Infer the backend from the path.

    A directory containing ``.json``/``.jsonl`` files is a JSON store. Anything
    else is handed to the SQLite detector, which is imported lazily so that the
    JSON backend keeps working when ``sqlite-vec`` is not installed.
    """
    p = Path(path)
    if not p.exists():
        raise BackendError(f"path does not exist: {p}")
    if p.is_dir():
        suffixes = {f.suffix.lower() for f in p.iterdir() if f.is_file()}
        if suffixes & {".json", ".jsonl"}:
            return "json"
    try:
        from .sqlite_vec import detect_sqlite
    except ImportError as exc:
        raise BackendError(
            f"cannot detect backend for {p}: no .json/.jsonl memory files found "
            f"and the SQLite backend is not available ({exc})"
        ) from exc
    if detect_sqlite(p):
        return "sqlite"
    raise BackendError(f"cannot detect backend for {p}: unrecognised store layout")
