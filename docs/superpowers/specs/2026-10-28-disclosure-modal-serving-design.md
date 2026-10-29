# Disclosure Model Serving on Modal Design

Date: 2026-10-28

Updated implementation and integration status: [October 29 report](../../operations/2026-10-29-disclosure-modal-integration.md). The status and scope statements below describe the original design decision.

Status: Artifacts verified against Hugging Face on 2026-10-28 (§2). Provider decided: Modal. Runtime code, endpoints, and measurements are not implemented. Extends `2026-10-28-disclosure-rag-serving-design.md`, which still holds for everything not restated here.

## 1. What This Settles

The serving design left two things open: where the weights live and who runs the GPU. Both are now decided — the adapters are on Hugging Face under the project account, and serving runs on Modal.

It also left an explicit warning open: one uploaded adapter had 144 all-zero LoRA B tensors, and the design refused to let it be chosen by filename. §2 closes that by inspecting the actual files.

Out of scope and unchanged: the 2000-dimensional vector contract currently in the database, the chunking and grouping rules, and the retrieval SQL. Moving the index to 1024 dimensions is a separate migration and cutover.

## 2. Verified Artifacts

Measured, not assumed. `adapter_model.safetensors` downloaded and inspected directly.

| | Embedding | Reranker |
| --- | --- | --- |
| Base | `perplexity-ai/pplx-embed-v2-context-9b-preview` | `Qwen/Qwen3-Reranker-4B` |
| Base revision | `e2c06a779d45e8db53066735be1b98ffe3412ca3` | `22e683669bc0f0bd69640a1354a6d0aebcfeede5` |
| Adapter repo | `Dongju-00/stockspoon-pplx-embed-v2-context-9b` | `Dongju-00/stockspoon-qwen3-reranker-4b-lora` |
| Adapter revision | `6949b167a2cf9caa92a2e765814a1db008a7bc7e` | `8cfbbb0c1ca7502bcd4eeb549f1bb9f20ee643dd` |
| Weight SHA256 | `1f5d43671826dab233cc32942cedb31bd0f8564f42a4b8f359a4a100d66c60e2` | `7d43c402f5061930e5e9fbc6e8c9e7618662814181f50a8c1b7a48371f947d24` |
| LoRA tensors | 152 A / 152 B, none all-zero, all finite | 144 A / 144 B, none all-zero, all finite |
| Visibility | public | public |

Both hashes match the selected artifacts in the serving design. Both adapter configs name the pinned base models, both carry `adapter_model.safetensors` and `adapter_config.json`, and both are rank 8, alpha 16.

The reranker has exactly 144 LoRA B tensors — the count the earlier design recorded as all-zero in the rejected `adapter-best`. All 144 are nonzero here, so the repository holds the evaluated `adapter-last` weights. The open replacement warning is closed.

Both repositories are public, so the runtime needs no Hugging Face token to load them. If either is made private later, a token must arrive through a Modal secret and never through an image layer or a commit.

## 3. Provider and Hardware

Modal, workspace `djdj1473`. Serverless, scale to zero: the search load is roughly 100 randomly timed requests a day, so idle time dominates and paying for an always-running pod is not justified until measurement says cold starts are intolerable.

One model per container on an L4 (24GB). The 9B embedding model is about 18GB in bf16 and the NF4 reranker about 3GB; 24GB cannot hold both and still run a 16,384-token forward pass. Quantizing the embedding weights is not an option — the evaluated vectors come from bf16 weights with an INT8 *output* transform, and quantized weights would leave the evaluation's vector space. Splitting workers is what the serving design permits when one card is insufficient.

Consequences accepted: a search that needs both an embedding and a rerank pays two cold starts on a cold path. Measurement (§7) decides whether that is acceptable, whether one card is enough per model at full input, and whether to move up to 40/48GB.

Cost ceiling: no scheduled or looped GPU execution. Measurement runs are explicit, bounded, and recorded. Both containers scale to zero when idle.

## 4. Runtime Layout

GPU code lives in `infrastructure/inference/disclosure/` and never enters a service or application image. Its dependencies (torch, transformers, peft, bitsandbytes, accelerate) are declared only in the Modal image definition.

- `model_manifest.json` — the single place repository IDs, revisions, hashes, dimensions, transform version and token limit are written. Nothing else hardcodes them.
- `transform.py` — the evaluated output transform, pure and testable without a GPU.
- `contracts.py` — request and response shapes, and the identity comparison.
- `app.py` — the Modal app: one class per model, the four endpoints.
- `tests/` — contract tests that run on CPU against fake encoders.

## 5. Endpoints

Unchanged from the serving design §5, served as Modal web endpoints.

- `GET /v1/identity` — base and adapter repository IDs, revisions, weight hashes, dimensions, transform version, token limit.
- `POST /v1/disclosure/document-embeddings` — `{identity, documents: [{document_id, chunks: [{chunk_id, text}]}]}` returns 1024-dimensional vectors keyed by `chunk_id`, order preserved. Grouping is per document; a request never mixes documents in one context group.
- `POST /v1/disclosure/query-embedding` — `{identity, query}` returns one 1024-dimensional vector.
- `POST /v1/disclosure/rerank` — `{identity, query, candidates: [{chunk_id, text, metadata}]}` returns, per candidate, the yes/no logit difference and its sigmoid.

Every request carries the caller's `identity`. A mismatch against the loaded manifest is rejected with 409 and never silently served, because a vector from the wrong adapter is indistinguishable from a correct one once stored.

Input beyond 16,384 tokenizer tokens is rejected with 422, counted in actual tokenizer tokens rather than bytes or characters. Truncation is never silent.

## 6. Output Transform and the Checkpoint's Own API

Read from the checkpoint's `modeling_pplx_contextual.py` at the pinned revision, not inferred.

The model is **not** a generic encoder. `PplxContextualModel.encode(documents, task=...)` takes `list[list[str]]` — a list of documents, each a list of chunks — joins each document's chunks with the configured `document_prefix` (`"[D] "`) and `boundary_marker` (`"<|chunk_sep|>"`), encodes the whole document as one sequence, and pools each chunk back out of it by character span. Queries go through `task="query"`, which requires exactly one string per row and prepends `query_prefix` (`"[Q] "`).

So the grouped context encoding the serving design asks for is the checkpoint's own contract. Hand-rolled mean pooling over `last_hidden_state` would produce different vectors and must not be used.

`encode` already performs part of the transform:

```python
vectors = torch.round(torch.tanh(projected) * 127).clamp(-128, 127)
```

Two corrections to what the earlier design recorded:

- The clip range is **[-128, 127]**, not [-127, 127]. After `tanh * 127` the lower bound never binds, but it is kept identical because a difference here is a difference in every stored vector.
- The checkpoint's `embedding_dim` is **2048**, so "the first 1024 dimensions" is a truncation of the model's output. Truncation must happen **before** normalization — `encode` is therefore called with `normalize_embeddings=False`, and the service truncates to 1024 and then normalizes. Normalizing 2048 first would scale every stored vector by a factor the evaluation never applied.

The full order, with the split between model and service marked:

```
tanh → ×127 → round → clip [-128, 127]      (the checkpoint's encode)
→ first 1024 dimensions → L2 normalize       (the service)
```

This is an output transform, not weight quantization. It is versioned as `transform_version` in the manifest and travels in `/v1/identity`, because a change to it changes every vector and must invalidate stored indexes.

The checkpoint's own `document_length` and `query_length` are 262,144 tokens. The 16,384-token limit is this project's contract from its evaluation, not the model's, and is enforced against `prepare_inputs`, which is what the model actually sees once the prefix and separators are added.

Library versions are forced by the checkpoint: it was saved with transformers 5.3.0 and its remote code imports `Qwen3_5Model`, which transformers 4.x does not have. torch 2.14.1, transformers 5.3.0, peft 0.21.2, accelerate 1.15.0, bitsandbytes 0.50.2 and huggingface_hub 1.33.0 resolve together.

The base weights are 33.6GB on disk because the checkpoint is stored in float32; loaded in bf16 that is roughly 18GB of VRAM. The download happens once into a Modal volume.

The reranker keeps its evaluated prompt template, preceding context and instruction, and scores from the yes/no logits. Its quantization is NF4, which is weight quantization and was part of the evaluation.

## 7. Measurement

Recorded in a dated operations note, with sample counts beside every number. Advertised figures are not measurements.

1. Startup: first start, snapshot restore, and a fresh start after a restore miss, separately for each container.
2. Memory: peak VRAM and host RAM during load, at maximum input, and while reranking 50 candidates. On OOM, record it and compare 40/48GB rather than quietly shrinking the input.
3. Latency: p50 and p95 for query embedding, document embedding, and 50-candidate reranking, cold and warm, plus the end-to-end search path including the database.
4. Parity: identical input through the evaluation path and the service must produce vectors and scores within a recorded tolerance. Record dtype differences and the effect of halfvec storage on Top-5.
5. Cost: GPU execution, idle, loading, storage, and the CPU and database share, for 100 randomly timed requests a day.

## 8. Acceptance

1. Actual Hugging Face hashes match the selected artifacts and both LoRAs are applied — **met, §2**.
2. `/v1/identity` reports the manifest, and a mismatched identity is rejected rather than served.
3. Document embeddings are grouped per document and never across documents.
4. Over-budget input is rejected in tokenizer tokens, never truncated silently.
5. The transform matches the evaluated order, proven on CPU without a GPU.
6. Measurements in §7 are recorded with sample counts.
7. No GPU dependency appears in any service or application image.
