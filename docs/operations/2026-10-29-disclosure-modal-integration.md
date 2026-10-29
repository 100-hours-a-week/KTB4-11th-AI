# Disclosure Modal integration

Date: 2026-10-29. Worktree: `.claude/worktrees/346-disclosure-rag`.
Base: `3307362`. This continues the October 28 handoff, not the older divergent worktrees.

## Implemented

- Pinned weights are baked into separate Modal images. Offline loading and a sample
  forward pass run before the GPU snapshot. Adapter hashes and LoRA tensors are checked.
- Authenticated service-owned HTTP clients verify response identities and every chunk ID,
  vector dimension, norm and score. Missing/mismatched responses cannot activate an index.
- Migration 0016 adds a separate 1024-dimensional narrative index; legacy 2000 vectors stay
  intact. Activation is atomic and scoped by dimension. Schema rollback refuses data loss.
- Paragraph-derived chunks are grouped within one physical XML file and a bounded context
  window. Tables and unknown topics stay in direct section storage. Parser version changed
  so existing archives are reparsed instead of silently treated as classified.
- Query embedding uses the same checkpoint/adapter/transform as indexing. Exact cosine
  selects 50 candidates per scope; Qwen reranks and returns five by default. Latest and
  history remain separate; API results expose cosine, reranker score/margin and provenance.
- GPU failures return 503; upstream input-budget errors return 422. Empty indexed corpora
  and direct SQL reads do not invoke inference. Transactions are ended before GPU waits.
- Both Compose files pass Modal URLs and workspace proxy tokens. CPU images use no new
  third-party dependency. Daily indexing runs at 05:00 KST with the hourly news collection.

## Corrections found during integration

The earlier Modal runtime used a simplified reranker prompt rather than the training chat
format, did not enable NF4 double quantization, and requested logits for every token. The
new runtime matches the evaluation chat format, double quantization and float32 preparation,
and computes only last-token logits. The reranker identity was changed to prevent accepting
an older deployment as equivalent. This is not a claim of measured score parity.

An independent review found two integration gaps: a ready index containing no narrative
chunks still triggered inference, and local Compose left the builder at a 40-second wait.
Both now have fixes. Scoped chunk counts run before query embedding, including requested
history, and local/production Compose allow 480 seconds for the entire tool call.

The pinned Context source was downloaded without weights for inspection. `prepare_inputs`
sets `truncation=False` for both tasks, applies its own query/document prefixes and pools
chunks using character spans. Its own limit and the serving limit both reject oversized
inputs. The source was not copied into the service or executed locally.

## Validation

Before changes: 1,000 passed, 300 skipped without database configuration. Sandbox socket
restrictions caused three initial unrelated failures; the unrestricted baseline passed.
A dedicated PostgreSQL test container `ktb-test-pg`, port 55432, database `ktb_test`, was used
for destructive integration tests. No development or production database was used.

Intermediate full DB suite: 1,270 passed, 48 skipped, two subtests passed.
Focused suite after independent review fixes: 133 passed.
Final full suite: **1,272 passed, 48 skipped, two subtests passed** in 26.16 seconds.
The skipped checks require QuestDB, Valkey, tracing or SSM resources; disclosure DB tests ran.
Ruff lint/format, dependency boundary checks and git whitespace checks passed.
Migration 0016 downgraded to 0015 and upgraded to the single 0016 head successfully.
Both disclosure CPU Docker images built; isolated containers verified the manifest is present
and Modal/torch/Transformers/PEFT are absent. Both Compose files were rendered with the jobs
profile and fake environment values; Modal URLs/auth/1024 dimensions and the 480-second
caller timeout were verified. Service env-file resolution was disabled because `ai.env`
is not present in this checkout; deployment secrets were not read or validated.

Modal SDK 1.6.3 successfully imports and validates the app definition without deploying.
The ktb-core wheel includes the pinned JSON manifest. No dependency changes were made to
workspace packages, so lock and requirements exports did not need regeneration.

## Subsequent live verification

The Modal deployment and live GPU checks were subsequently completed. See the
[GPU validation report](2026-10-29-disclosure-modal-gpu-validation.md) for measured results,
remaining limits and errors fixed during deployment. The paragraph below records the
state at the end of the initial local integration.

## Initially not performed

No new Modal deployment, remote image build, paid GPU execution, live endpoint call,
calibration/final quality evaluation or cost benchmark was performed in this change.
Existing October 28 startup measurements remain historical. GPU snapshots are alpha and
misses remain possible. A deployment and maximum-input/parity tests are required before
claiming production readiness.

See the [runtime runbook](../../infrastructure/inference/disclosure/README.md) for exact
configuration, deployment, cutover, rollback and links to the official Modal documentation.
