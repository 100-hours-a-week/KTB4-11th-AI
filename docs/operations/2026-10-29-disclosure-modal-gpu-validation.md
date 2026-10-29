# Modal redeployment and live GPU validation

Date: 2026-10-29 KST. Branch: `feat/346-disclosure-rag`.
App: [disclosure-inference](https://modal.com/apps/djdj1473/main/deployed/disclosure-inference).
Work continued from the October 28 handoff and the October 29 integration report.

## Outcome

The model images were deployed successfully, and authenticated document embedding, query
embedding and reranking calls completed on actual GPUs. Calibration retrieval reproduced
**Recall@5 1.0000, nDCG@5 0.978290, MRR 0.970588** on 34 answerable narrative questions.
This confirms compatibility with the frozen evaluation corpus, not the quality of the new
production parser, narrative classification or metadata/grouping policy.

No server deployment, production DB migration or production index cutover was performed.
The Modal endpoints are deployed; CPU service deployment remains a separate operation.

## Deployment and startup

The base/LoRA hashes remain those in `packages/core/src/ktb_core/disclosure_models.json`:
Context score10 `1f5d4367…`, Qwen last `7d43c402…`. GPU snapshots are enabled. Model
weights are downloaded during image construction and runtime loading is offline.

| Measurement | Seconds | Scope |
| --- | ---: | --- |
| First image build and deployment | 248.294 | Both model images, including pinned weight downloads |
| Cached redeployment with GPU fallback | 5.919 | Same weights/images; updated GPU preferences |
| First reranker SDK request | 329.821 | Startup and 50-candidate inference combined |
| Reranker inference within that request | 7.185 | Synthetic control passages, 50 candidates |
| First A10-requested embedding diagnostic | 147.206 | CPU=2, RAM=32GiB; startup and inference combined |
| Embedding inference within that diagnostic | 0.617 | Two chunks, 42 input tokens |
| Original L4-only embedding request | 1,047.005 | Request spanned an observed L4 capacity shortage and fallback redeployment |
| Inference within that delayed request | 0.440 | Scheduling delay must not be reported as model compute |

Modal explicitly logged that the embedder was waiting for a `GPU_L4` worker. The default
GPU preference list was changed from `L4` to `L4,A10`, two 24GB options. The A10 diagnostic
also changed CPU/RAM resources, so it is not a controlled GPU speed comparison. GPU names
were not successfully collected per calibration request; do not attribute all timings to L4.
The old delayed request is not a typical cold-start estimate or a service SLA.

The current deployment version captured after fallback was **v7**. All five HTTP endpoints
require Modal proxy authentication. A temporary proxy token was used for validation; its
secret is not included in the repository or these result files.

## Warm calls and maximum input

Three authenticated short-query requests took **1.190–1.827s** over HTTPS. Three controlled
50-candidate reranking HTTPS calls took **8.135–8.330s**; the designated correct control
passage ranked first in all three. These controls are separate from the 34-question results.

A **16,003-token** document completed in **8.634s** of encoding and **11.064s** SDK wall time.
Maximum allocated VRAM was **19.04GiB**, reserved VRAM **21.34GiB**, total **22.06GiB**, and
reported free memory **0.42GiB**. This supports one input at a time on a 24GB GPU, with little
memory margin. It does not validate batching or concurrent inputs.

The actual indexer client also sent two separate documents containing three chunks and
received all three 1024-dimensional vectors with matching IDs and identity in **9.928s**.

## Post-idle and snapshot-enabled calls

The later probe ran after the model containers had become idle. Its first embedder response
took **7.792s**, and the next two took **0.573s** and **0.559s**. Its first reranker response
took **11.774s**, and the next two took **4.161s** and **4.178s**.

Treat the first request of each sequence as a post-idle observation, and the next two as
warm requests. The call graph did not expose container IDs, so the attempted forced-stop
step was skipped. These are not six independent cold-restoration samples, and they do not
establish a snapshot hit rate or p95 restoration guarantee. Snapshots were enabled; exact
provider-side restore/allocation/initialization decomposition remains unmeasured.

## Retrieval data and method

The existing dataset contains 378 questions, 9 reports and 17,024 chunks. The calibration
subset used here contains **34 answerable narrative questions from 5 reports**, with **9,101
full-report chunks** eligible across those reports. Financial and designated questions,
unanswerable narrative questions and the final 178-question split were not scored here.

Document vectors were reused from the verified Drive cache:
[vector cache folder](https://drive.google.com/drive/folders/12inibSF3Bn_KwS4lvTMmG-9tBS_FGMEZ).
The corpus hash was checked against `embedding-identity.json` before reuse. The stored
policy is the original physical-document greedy 16,384-token window policy. The dataset
SHA256 is `5ca9d0217957d5aa96872f39b01d3b51be575c515bbbab4561cb510b610d01ed`.

Every query was re-embedded by the deployed GPU model. A cosine tolerance of **0.999**
against the cached query vector was checked for every question. The preliminary three-query
probe measured cosines **0.999902–0.999911**, with maximum coordinate differences around
0.00139. The outputs are numerically compatible within that stated tolerance, not identical.
Runtime uses Transformers 5.3.0 and the observed torch fallback kernels; the old cached run
recorded Transformers 5.4.0 and fast kernels. This difference is retained in the record.

The vectors were loaded into a temporary `halfvec(1024)` table in the dedicated `ktb_test`
database. Exact cosine search scored the full requested report and selected 50 candidates.
The deployed Qwen reranker scored each candidate using the evaluation's metadata, preceding
context and financial yes/no chat template. Top-5 ordering used logit margins and stable
chunk IDs. IDs, cosine values, reranker scores and margins are saved for all 170 returned
positions in [calibration-top5.csv](results/2026-10-29-disclosure-modal/calibration-top5.csv).

| Metric | Previous calibration | This live run |
| --- | ---: | ---: |
| Recall@5 | 1.0000 | 1.0000 |
| nDCG@5 | 0.9783 | 0.978290 |
| MRR | 0.9706 | 0.970588 |

The old numbers and this run refer to the same 34-question calibration category. The new
production indexer excludes tables/unknown topics and uses bounded byte-based context groups,
while this compatibility test intentionally retained the original full-report cache. The
production reranking text also differs from the evaluation's preceding-context input. Do not
present these scores as an evaluation of the entire new indexing-to-retrieval pipeline.
Question labels remain source-linked/programmatically checked, without completed SME review.
No generated-answer F1 or LLM-judged RAGAS scores were collected; retrieval generates no answer.

## Retrieval timing

Measurements include authenticated network calls from this workstation and SQL on the local
dedicated test DB. They exclude document indexing, model image builds and answer generation.
The first query included a post-idle delay; it is retained. p95 uses nearest-rank, n=34.

| Stage | p50 seconds | p95 seconds |
| --- | ---: | ---: |
| GPU query embedding, including HTTPS | 1.277 | 1.536 |
| PostgreSQL exact Top-50 | 0.0176 | 0.0471 |
| GPU reranking of 50 candidates, including HTTPS | 13.229 | 16.924 |
| Combined measured retrieval stages | 14.540 | 19.656 |

This is a sequential calibration workload, not the planned random 100-requests/day workload.
The DB is local and temporary, so these SQL timings are not deployed DB latency estimates.

## Issues reproduced and fixed

1. **L4 capacity shortage:** provider logs confirmed allocation waits. GPU fallback was
   deployed; the original wait is kept as an availability observation.
2. **HTTP 303 result polling:** the original urllib client repeated result redirects and
   failed after **753.558s** with an infinite-redirect error. Shared generic HTTP transport
   now follows same-origin result URLs with GET, without resubmitting inference, and budgets
   the remaining redirect time. A different-origin redirect is rejected before credentials
   can be forwarded. Unit regressions cover all three behaviors. Socket I/O timeouts and
   post-read deadline checks do not provide hard cancellation of arbitrary streaming bodies.
3. **Parser-version rollback:** the newest parser row previously hid a preserved legacy
   index from the same original source. Selection now allows older-parser indexes only when
   their source hash matches the latest document. Regression tests cover both rollback and
   refusal to read a replaced source.

Independent review found no remaining confirmed P0/P1/P2 blockers after those fixes.
After merging current `dev` (`0f61ead`), the final dedicated-DB suite passed:
**1,327 passed, 49 skipped, two subtests passed** in 37.08 seconds. Ruff lint/format,
module/dependency boundaries and whitespace checks passed. Both CPU images were rebuilt
and verified with networking disabled; no Modal SDK or torch dependency is installed.

## Evidence and reproduction

Raw result files are in [results/2026-10-29-disclosure-modal](results/2026-10-29-disclosure-modal).
The standalone `infrastructure/inference/disclosure/benchmark.py` tool records per-question
results, Top-5 scores, timings and model identities. It requires cached vectors, matching
corpus identity, a local proxy-token file and the disposable `ktb_test` database. Resume
refuses conflicting run identities. It is not imported or executed by the services.

Before production cutover, evaluate actual parser/classification/context groups, gather a
larger controlled snapshot/allocation sample and measure end-to-end traffic at 100 random
requests/day. Cost was not measured from billing in this run.

Official references: [GPU preferences](https://modal.com/docs/guide/gpu),
[GPU snapshots](https://modal.com/docs/guide/memory-snapshots),
[HTTP timeouts and 303 result URLs](https://modal.com/docs/guide/webhook-timeouts),
[CPU/RAM guarantees](https://modal.com/docs/guide/resources).
