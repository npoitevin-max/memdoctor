"""Fixture builders for the JSON / JSONL memory store tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def memory(
    id: str | None = "m1",
    content: str | None = "hello",
    created_at: str | None = "2024-01-01T00:00:00Z",
    *,
    embedding: list[float] | None = None,
    embedded: bool = False,
) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    if id is not None:
        obj["id"] = id
    if content is not None:
        obj["content"] = content
    if created_at is not None:
        obj["created_at"] = created_at
    if embedding is not None:
        obj["embedding"] = embedding
    if embedded:
        obj["embedded"] = True
    return obj


def write_json_store(root: Path, files: dict[str, list[dict[str, Any]]] | None = None) -> Path:
    store = root / "memory"
    store.mkdir(parents=True, exist_ok=True)
    for name, items in (files or {}).items():
        (store / name).write_text(json.dumps(items), encoding="utf-8")
    return store


def write_jsonl_store(root: Path, files: dict[str, list[dict[str, Any]]] | None = None) -> Path:
    store = root / "memory"
    store.mkdir(parents=True, exist_ok=True)
    for name, items in (files or {}).items():
        text = "".join(json.dumps(obj) + "\n" for obj in items)
        (store / name).write_text(text, encoding="utf-8")
    return store
