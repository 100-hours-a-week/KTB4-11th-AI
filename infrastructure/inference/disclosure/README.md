# Disclosure inference runtime

Embedding and reranking for `disclosure-indexer` and `disclosure-retrieval`, served on
Modal. Design: [`2026-10-28-disclosure-modal-serving-design.md`](../../../docs/superpowers/specs/2026-10-28-disclosure-modal-serving-design.md).

This directory is **not** a workspace package. Its dependencies (torch, transformers, peft,
bitsandbytes) are declared only in `app.py`'s Modal image and must never enter a service or
application image.

## Layout

| File | Responsibility |
| --- | --- |
| `model_manifest.json` | The only place repository IDs, revisions, weight hashes, dimensions, transform version and token limit are written |
| `transform.py` | The evaluated output transform, pure and CPU-testable |
| `contracts.py` | Request shapes, document grouping, and the identity check |
| `app.py` | The Modal app: one container per model, four endpoints |
| `tests/` | Contract tests; run on CPU with no GPU and no Modal account |

## Tests

```bash
./scripts/run-tests.sh infrastructure/inference
```

## Deploy

Needs a Modal account; the client is a local tool and deliberately not a workspace
dependency.

```bash
python3 -m venv /tmp/modalenv && /tmp/modalenv/bin/pip install modal
/tmp/modalenv/bin/modal deploy infrastructure/inference/disclosure/app.py
```

Both containers scale to zero after 120 idle seconds. `DISCLOSURE_INFERENCE_GPU` overrides
the GPU at deploy time; the default is `L4` (24GB).

## Why one model per container

The 9B embedding model is about 18GB in bf16 and the NF4 reranker about 3GB. 24GB cannot
hold both and still run a 16,384-token forward pass. The embedding weights stay in bf16
deliberately: the evaluated vectors come from bf16 weights with an INT8 *output* transform,
so quantizing the weights would leave the vector space the evaluation measured.

The cost is two cold starts on a fully cold search. Whether that is acceptable is a
measurement, not an assumption — see the design's §7.

## Identity

Every inference request carries the identity it expects, and a mismatch returns 409. A
vector produced by the wrong adapter is indistinguishable from a correct one once stored,
so the check is refused loudly rather than served quietly.

At load time each adapter's SHA256 is compared against the manifest and every LoRA B tensor
is checked for being nonzero and finite. A LoRA with zero B matrices loads cleanly and
silently does nothing; one such upload is already on record for this project.
