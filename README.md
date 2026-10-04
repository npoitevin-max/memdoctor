# memdoctor

**Memory corruption in AI agents is silent. The agent doesn't crash — it just starts remembering wrong.**

`memdoctor` checks an agent's long-term memory store for the failure modes that quietly degrade it, and repairs the ones that can be repaired safely.

```
$ memdoctor check ./memory/
memdoctor v0.1.0 — sqlite backend

  store        ./memory/
  memories     12480
  vectors      12482

  ✗ D1 orphaned vectors..................... 4 found
         ids: m09000, ghost-9912, ghost-9913
  ! D4 missing required fields.............. 2 found
         ids: m00210, m00444
  ✗ D5 degenerate vectors................... 1 found
         ids: m07741

  7 issue(s) found in 0.4s. Run `memdoctor fix ./memory/` to repair.
```

`fix` quarantines what it removes before removing it, snapshots the store first, and never
touches memory content:

```
$ memdoctor fix ./memory/
memdoctor fix — sqlite backend

  backup       ./memory/memories.db.memdoctor-backup-2026-10-04T124003661024Z
  quarantine   ./memory/memories.db.memdoctor-quarantine.jsonl

  removed orphaned_vector      rowid=99001 id=ghost-9912  vector row has no matching memory row
  removed degenerate_vector    rowid=7742  id=m07741      embedding is all-zero, non-finite, or has zero norm
  marked unembedded            id=m07741  no vector remains; needs re-embedding

  not automatically repairable:
    orphaned_vector id=m09000  memory marked as embedded but has no embedding
```

## The problem

Every agent framework now ships "long-term memory". Almost none of them ship a way to tell whether that memory is *correct*.

The failure modes are well documented in incident write-ups and are not the ones people expect:

- **Orphaned vectors.** The embedding write succeeds and the row write fails — or vice versa. Retrieval now returns memories that don't exist, or silently loses ones that do.
- **Degenerate embeddings.** A failed or rate-limited embed call persists a zero or NaN vector. That memory is now unreachable forever, and nothing warns you.
- **Index divergence.** The vector index and the source-of-truth store disagree, so `count` in your logs has stopped matching reality.
- **Silent field rot.** Rows missing content, timestamps or IDs that downstream code assumes are present.
- **Poisoned memory.** Adversarial or accidental writes that make the agent confident about things that are wrong.

None of these throw. The agent keeps answering. It just gets worse.

## What it checks (v0.1)

Narrow on purpose — five detection classes, two backends:

| # | Check | Severity |
|---|---|---|
| D1 | Orphaned vectors (vector row without a memory row, or the reverse) | error |
| D2 | Duplicate or conflicting IDs | error |
| D3 | Embedding dimension mismatch against the store's declared dimension | error |
| D4 | Missing required fields (content, id, created_at) | warn |
| D5 | Degenerate vectors (all-zero, NaN, or non-finite) | error |

## Usage

```bash
pip install memdoctor

memdoctor check ./memory/            # report only, never writes
memdoctor check ./memory/ --json     # machine-readable, for CI
memdoctor fix ./memory/              # repair, backs up first
memdoctor fix ./memory/ --dry-run    # show what would change
```

## Design principles

- **Report-only by default.** `check` never writes. `fix` is explicit and always takes a backup first.
- **Safe repairs only.** Orphans are removed, degenerate vectors are quarantined rather than deleted, and anything ambiguous is reported instead of guessed at.
- **No dashboard, no daemon, no cloud.** One command, one exit code — usable in a cron job or a CI pipeline.
- **Narrow beats universal.** Two backends done properly rather than eight done approximately.

## Backends

| Backend | Status |
|---|---|
| SQLite + `sqlite-vec` | v0.1 |
| JSON / JSONL memory directory | v0.1 |
| Chroma, Qdrant, LanceDB, Letta, Mem0 | roadmap — issues welcome |

## Status

v0.1, early. Built because running agent fleets in production makes this problem impossible to ignore — and because none of the memory frameworks diagnose or repair their own store.

MIT licensed.

## Contributing

If you have a corrupted memory store and a story about how it got that way, open an issue. The failure taxonomy is more useful than any single fix.
