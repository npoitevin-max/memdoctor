"""Backend-agnostic data model for memdoctor.

These types carry no IO and know nothing about any particular backend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    ERROR = "error"
    WARN = "warn"


@dataclass
class Memory:
    """A single normalised memory row as seen by the detection checks."""

    id: str | None
    content: str | None
    created_at: str | None
    embedding: list[float] | None = None
    embedded: bool = False


@dataclass
class Issue:
    """A single detection result."""

    code: str
    severity: Severity
    id: str | None = None
    detail: str = ""


@dataclass
class Report:
    """The full result of a check run."""

    version: str
    backend: str
    store: str
    counts: dict[str, int]
    issues: list[Issue] = field(default_factory=list)
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "backend": self.backend,
            "store": self.store,
            "counts": dict(self.counts),
            "issues": [
                {
                    "code": i.code,
                    "severity": i.severity.value,
                    "id": i.id,
                    "detail": i.detail,
                }
                for i in self.issues
            ],
            "duration_ms": self.duration_ms,
        }
