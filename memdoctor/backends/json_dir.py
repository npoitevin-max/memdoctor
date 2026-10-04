"""JSON / JSONL memory directory backend.

A store is a directory of ``.json`` files (each a list of memory objects) and
``.jsonl`` files (one memory object per line). Malformed input is reported as an
``Issue`` rather than raised, so a single bad file never aborts a check.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterator

from ..model import Issue, Memory, Severity
from .base import BackendError, MemoryStore


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


class JsonDirStore(MemoryStore):
    backend = "json"

    def __init__(self, path: str | os.PathLike[str]) -> None:
        super().__init__(path)
        self._memories: list[Memory] = []
        self._issues: list[Issue] = []
        self._files = 0
        self._malformed = 0
        self._load()

    # -- loading ---------------------------------------------------------

    def _load(self) -> None:
        p = self.path
        if p.is_dir():
            files = sorted(
                f
                for f in p.iterdir()
                if f.is_file() and f.suffix.lower() in {".json", ".jsonl"}
            )
        elif p.is_file():
            files = [p]
        else:
            raise BackendError(f"not a directory or file: {p}")
        self._files = len(files)
        for f in files:
            self._load_file(f)

    def _load_file(self, file: Path) -> None:
        try:
            raw = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            self._malformed += 1
            self._issues.append(Issue("malformed_json", Severity.ERROR, None, f"{file}: {exc}"))
            return

        if file.suffix.lower() == ".jsonl":
            for lineno, line in enumerate(raw.splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    self._malformed += 1
                    self._issues.append(
                        Issue("malformed_json", Severity.ERROR, None, f"{file}:{lineno}: {exc.msg}")
                    )
                    continue
                self._add_object(obj, f"{file}:{lineno}")
            return

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._malformed += 1
            self._issues.append(Issue("malformed_json", Severity.ERROR, None, f"{file}: {exc.msg}"))
            return
        if not isinstance(data, list):
            self._malformed += 1
            self._issues.append(
                Issue("malformed_json", Severity.ERROR, None, f"{file}: expected a list of memory objects")
            )
            return
        for obj in data:
            self._add_object(obj, str(file))

    def _add_object(self, obj: object, location: str) -> None:
        if not isinstance(obj, dict):
            self._malformed += 1
            self._issues.append(
                Issue("malformed_json", Severity.ERROR, None, f"{location}: expected a memory object")
            )
            return
        self._memories.append(self._normalise(obj, location))

    def _normalise(self, obj: dict, location: str) -> Memory:
        memory_id = _id_str(obj.get("id"))
        content = _text(obj.get("content"))
        created_at = _text(obj.get("created_at"))
        embedded = bool(obj.get("embedded") or obj.get("has_embedding"))
        embedding = self._coerce_embedding(obj.get("embedding"), memory_id, location)
        return Memory(
            id=memory_id,
            content=content,
            created_at=created_at,
            embedding=embedding,
            embedded=embedded,
        )

    def _coerce_embedding(self, value: object, memory_id: str | None, location: str) -> list[float] | None:
        if value is None:
            return None
        if not isinstance(value, (list, tuple)):
            self._malformed += 1
            self._issues.append(
                Issue("malformed_json", Severity.ERROR, memory_id, f"{location}: embedding is not a list of numbers")
            )
            return None
        try:
            return [float(x) for x in value]
        except (TypeError, ValueError):
            self._malformed += 1
            self._issues.append(
                Issue("malformed_json", Severity.ERROR, memory_id, f"{location}: embedding contains non-numeric values")
            )
            return None

    # -- public interface ------------------------------------------------

    def iter_memories(self) -> Iterator[Memory]:
        yield from self._memories

    def stats(self) -> dict[str, int]:
        return {
            "files": self._files,
            "memories": len(self._memories),
            "vectors": sum(1 for m in self._memories if m.embedding is not None),
            "malformed": self._malformed,
        }

    def backend_issues(self) -> list[Issue]:
        return list(self._issues)
