# Disclosure Inference on Modal: First Measurements

Date: 2026-10-28

Deployment: Modal app `disclosure-inference`, workspace `djdj1473`, region `us-east`. One container per model, GPU **L4 (24GB)**, scale to zero after 120 idle seconds.

Every number below was measured against the deployed app. Sample counts are given. Nothing here is an advertised figure.

## 1. Artifacts Loaded

Verified at load time, and separately from a workstation before deploying:

| | Repo @ revision | SHA256 | LoRA |
| --- | --- | --- | --- |
| Embedding adapter | `Dongju-00/stockspoon-pplx-embed-v2-context-9b` @ `6949b167` | `1f5d4367…` | 152 A / 152 B, none all-zero, all finite |
| Reranker adapter | `Dongju-00/stockspoon-qwen3-reranker-4b-lora` @ `8cfbbb0c` | `7d43c402…` | 144 A / 144 B, none all-zero, all finite |

Both match the selected artifacts in the serving design. The reranker's 144 LoRA B tensors are the count the design recorded as all-zero in the rejected `adapter-best`; all 144 are nonzero here, so the repository holds the evaluated `adapter-last` weights.

Base weights are 33.6GB on disk (the checkpoint is stored in float32) and load in bf16.

## 2. Embedder

GPU reports **22.06 GiB** usable on the L4.

| Run | Tokens | Chunks out | Encode | Wall | VRAM peak | VRAM reserved | Free |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Cold start, 2 documents | 37 | 4 | 4.32s | **187.9s** | 15.74 GiB | 15.80 GiB | 6.02 GiB |
| Warm, 2 documents | 37 | 4 | 0.34s | 0.61s | 15.74 GiB | 15.80 GiB | 6.02 GiB |
| Warm, 1 query | 8 | 1 | 0.50s | 0.76s | 15.73 GiB | 15.81 GiB | 6.01 GiB |
| Warm, 1 document, 20 chunks | 15,740 | 20 | 13.21s | 15.49s | **18.96 GiB** | **21.28 GiB** | **0.54 GiB** |
| Warm, 1 document, 20 chunks (repeat) | 15,740 | 20 | 13.01s | 13.63s | 18.96 GiB | 21.28 GiB | 0.54 GiB |
| Warm, 1 document, 24 chunks | 15,360 | 24 | 12.90s | 13.27s | 18.88 GiB | 21.28 GiB | 0.54 GiB |

One sample per row except the 15,740-token case, which is two.

The cold start includes the one-time 33.6GB download into the Modal volume plus adapter verification and load. Later cold starts read from the volume and were not measured separately yet.

Output width is **1024** in every run, and a 2-document request with 1 and 3 chunks returned 4 vectors, so truncation from the checkpoint's 2048 dimensions and per-document grouping both behave as contracted.

### 24GB verdict

The embedder fits, with almost no margin. At 15,740 tokens it reserves **21.28 of 22.06 GiB — 96%**, leaving 0.54 GiB free. The 16,384-token contract is therefore right at the edge of an L4.

Consequences to accept or change:

- One request at a time per container. There is no room for a second concurrent encode, so concurrency has to come from more containers, not a bigger batch.
- No headroom to raise the token limit on this card.
- Both models could never share one 24GB card: the embedder alone holds 15.7 GiB at idle.

Moving to 40/48GB would buy margin and allow batching. That is a cost decision, not a correctness one — the current configuration works within the contract.

### Token limit

Over-budget input is refused, not truncated, and the count is in real tokenizer tokens as the model will see them (after the `[D] ` prefix and `<|chunk_sep|>` separators):

```
24 chunks → ValueError: input is 21240 tokens, over the 16384-token limit
32 chunks → ValueError: input is 37728 tokens, over the 16384-token limit
```

## 3. Reranker

Pending — measurement in progress at the time of writing. To record: cold start including the base download, warm latency for 5 and 50 candidates, VRAM peak under a 50-candidate pool.

## 4. Not Yet Measured

- Second cold start from a warm volume, and Modal memory-snapshot restore versus a fresh start after a restore miss.
- End-to-end search latency through `disclosure-retrieval` including PostgreSQL.
- Parity: identical input through the evaluation path versus the service, with a recorded tolerance, and the effect of halfvec storage on Top-5.
- Cost for 100 randomly timed requests a day.

## 5. Operational Notes

- All five HTTP endpoints require Modal proxy auth. The first deploy published them unauthenticated; that is fixed, and a proxy auth token must be minted in the Modal dashboard for the services to call them.
- The `measure` methods are Modal methods, not web endpoints, so the measurement surface is reachable from the SDK only.
- A cold path that needs both an embedding and a rerank pays two cold starts, because the models are in separate containers. The embedder's 188s first start drops substantially once the volume is populated, but that number is still unmeasured.
