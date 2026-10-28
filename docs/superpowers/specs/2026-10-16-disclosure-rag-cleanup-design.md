# Disclosure RAG service boundaries

## Approved intent

News embeddings remain unchanged. Disclosure documents and search queries use the same finetuned Qwen3-Embedding-8B checkpoint and 2000-dimensional vector space. Shared DISCLOSURE_EMBEDDING_BASE_URI, MODEL and API_KEY settings are independent of KTB_EMBEDDING_* used for news. MODEL is required because the finetuned serving name is not known yet. Query instruction defaults to empty and can be set to the training format.

## Services

Use two independent workspace services: disclosure-indexer is a scheduled job that collects periodic OpenDART reports, preserves originals, chunks sections, embeds and publishes ready indexes; disclosure-retrieval is a long-running HTTP API that embeds queries and reads published chunks. Both call one external model server. Remove the collector service after moving its collection code and tests into indexer. Remove disclosure endpoints and embedding settings from news-http. Keep market-syncer DART corporate-code metadata lookup: it does not collect report bodies.

An indexing run must continue indexing previously collected documents after a collection failure; aggregate failure counts and exit unsuccessfully after both stages. Existing active indexes remain searchable until replacements succeed. Retrieval checks model identity before comparing vectors. Shared core embedding code contains transport and vector validation only; no disclosure settings or policy. News core configuration is unchanged.

## Portfolio integration

A dedicated RetrievalClient calls the new service. search_disclosures remains an agent tool. RequireDisclosures blocks submit_portfolio until a valid search response completes in an earlier model turn, in addition to existing RequireTechnicals. A valid empty result completes the check; transport errors, tool errors, and malformed results do not. Track calls, successes, failures and empty results. State and counters reset every run. This is a run-wide prerequisite, matching technical analysis, not per-holding coverage.

## Operations and checks

The morning portfolio pipeline invokes indexer before news clustering, graph building and portfolio building. Existing market-syncer scheduling prepares company metadata. A failed collection/index run fails the pipeline. Empty optional BUSINESS_YEAR from Compose uses the default scope. OpenDART error envelopes must not become reusable document archives.

Update service manifests, Dockerfiles, production image, requirements exports, Compose, CI, tach, env examples and README together. Dedicated test databases only. Evaluation tools, data and duplicate variants remain outside Git at /private/tmp/ktb-disclosure-rag-local-20261016; do not add ignore rules for them. No commits, pushes or deployments are required.

## Second-review clarifications

Archive recovery includes pending as well as failed extractions, including interrupted current-parser upgrades. Embedding input formatting preserves each full chunk body and bounds the metadata/context prefix by the remaining UTF-8 byte budget; original stored content is preserved. Input format metadata-byte-budget-v1 participates in the signature. Compose forwards indexer and retrieval tuning settings explicitly. The agent prompt accepts retrieved disclosure passages as stored-reason evidence without fabricating news cluster citations. Current limitations and review handoff are documented in docs/operations/2026-10-16-disclosure-rag-review-handoff.md.
