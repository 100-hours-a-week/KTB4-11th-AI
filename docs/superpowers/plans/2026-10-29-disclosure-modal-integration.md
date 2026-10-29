# Modal Disclosure Integration Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan.

**Goal:** Finish the handoff by connecting the CPU services to authenticated Modal GPU snapshot endpoints.
**Spec:** ../specs/2026-10-28-disclosure-modal-serving-design.md and ../specs/2026-10-28-disclosure-rag-serving-design.md.
**Architecture:** Separate L4 embedder and reranker containers, GPU snapshots and image-baked weights. Docker services call HTTPS using proxy tokens. No generation. Existing 2000-dimensional indexes remain available for rollback.

## Global Constraints

- Use the verified score10 embedding and last-7d43c4 reranker; pin revisions and hashes.
- No production DB, deployment or paid GPU work during local implementation.
- Direct section and fact reads never call Modal.
- Separate 1024-dimensional narrative storage; preserve legacy vectors.
- No GPU/Modal SDK dependencies in CPU images; environment-only credentials.

## Review Focus

- A valid shape from a different checkpoint must be rejected.
- Missing, duplicate or nonfinite vectors/scores must fail rather than partially activate an index.
- ZIP attachments must remain distinct context groups; financial tables remain direct reads.
- Empty searches must not consume inference; model failures must return 503, budget failures 422.
- Snapshot misses must not download weights or exceed an unbounded client wait.

## Task 1: Snapshot runtime and authenticated clients

- [x] Add failing transport tests for identity mismatch, mapping completeness, proxy headers, invalid values and budgets.
- [x] Share the pinned manifest as core package data; add service-owned HTTP clients.
- [x] Bake both pinned models into their respective Modal images, verify adapters, load offline and warm up before GPU snapshot.
- [x] Run contract and transport tests.

## Task 2: Narrative indexing and safe storage

- [x] Add failing DB tests for 1024 indexing, attachment separation, retries and legacy preservation.
- [x] Add migration 0016 for narrative chunks and index identity, preserving 2000 vectors.
- [x] Connect incremental indexing with physical-document context groups and atomic activation.
- [x] Run migrated disposable DB tests.

## Task 3: Top-50 reranking and Docker wiring

- [x] Add failing service tests for reverse reranking, Top-5, invalid scores, empty results and company/date isolation.
- [x] Connect query embedding, exact cosine Top-50 and reranking while keeping latest/history separate.
- [x] Wire Modal URLs, proxy tokens and bounded cold-start timeouts through both Compose files; align builder timeout.
- [x] Update English deployment/runbook docs with official Modal sources and rollback steps.
- [x] Run full tests, lint, format, dependency boundaries, migration roundtrip and Compose checks; record unavailable live GPU checks.
