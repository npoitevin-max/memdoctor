"""Detection checks D1-D5, run over a normalised MemoryStore view.

Each check is backend-agnostic: it consumes ``Memory`` objects and returns
``Issue`` objects. No IO happens here.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Iterable

from .model import Issue, Memory, Severity


def _is_degenerate(vec: list[float]) -> bool:
    if any(math.isnan(x) or math.isinf(x) for x in vec):
        return True
    return math.sqrt(sum(x * x for x in vec)) == 0.0


def modal_dimension(memories: list[Memory]) -> int | None:
    lengths = Counter(len(m.embedding) for m in memories if m.embedding is not None)
    if not lengths:
        return None
    return lengths.most_common(1)[0][0]


def check_orphaned_vectors(
    memories: list[Memory], orphan_vector_ids: Iterable[str] = ()
) -> list[Issue]:
    issues: list[Issue] = []
    for m in memories:
        if m.embedded and m.embedding is None:
            issues.append(
                Issue("orphaned_vector", Severity.ERROR, m.id, "memory marked as embedded but has no embedding")
            )
    for orphan_id in orphan_vector_ids:
        issues.append(
            Issue("orphaned_vector", Severity.ERROR, orphan_id, "vector row has no matching memory row")
        )
    return issues


def check_duplicate_ids(memories: list[Memory]) -> list[Issue]:
    counts: dict[str, int] = defaultdict(int)
    for m in memories:
        if m.id:
            counts[m.id] += 1
    issues: list[Issue] = []
    for mem_id, n in sorted(counts.items()):
        if n > 1:
            issues.append(Issue("duplicate_id", Severity.ERROR, mem_id, f"{n} memories share this id"))
    return issues


def check_dimensions(memories: list[Memory], dimension: int | None) -> list[Issue]:
    issues: list[Issue] = []
    for m in memories:
        if m.embedding is None or dimension is None:
            continue
        if len(m.embedding) != dimension:
            issues.append(
                Issue(
                    "dimension_mismatch",
                    Severity.ERROR,
                    m.id,
                    f"embedding has length {len(m.embedding)}, expected {dimension}",
                )
            )
    return issues


def check_missing_fields(memories: list[Memory]) -> list[Issue]:
    issues: list[Issue] = []
    for m in memories:
        missing = []
        if not m.id:
            missing.append("id")
        if not m.content:
            missing.append("content")
        if not m.created_at:
            missing.append("created_at")
        if missing:
            issues.append(Issue("missing_field", Severity.WARN, m.id, "missing field(s): " + ", ".join(missing)))
    return issues


def check_degenerate_vectors(memories: list[Memory]) -> list[Issue]:
    issues: list[Issue] = []
    for m in memories:
        if m.embedding is None:
            continue
        if _is_degenerate(m.embedding):
            issues.append(
                Issue("degenerate_vector", Severity.ERROR, m.id, "embedding is all-zero, non-finite, or has zero norm")
            )
    return issues


def run_checks(
    memories: Iterable[Memory],
    dimension: int | None = None,
    orphan_vector_ids: Iterable[str] = (),
) -> list[Issue]:
    """Run all detection checks, returning every issue found."""
    mems = list(memories)
    if dimension is None:
        dimension = modal_dimension(mems)

    issues: list[Issue] = []
    issues.extend(check_orphaned_vectors(mems, orphan_vector_ids))
    issues.extend(check_duplicate_ids(mems))
    issues.extend(check_dimensions(mems, dimension))
    issues.extend(check_missing_fields(mems))
    issues.extend(check_degenerate_vectors(mems))
    return issues
