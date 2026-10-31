# Disclosure inference

This service loads the selected Context-9B and Qwen3-Reranker-4B adapters, embeds
document groups and queries, and scores retrieval candidates. It does not generate
answers or read the application database.

`src/disclosure_inference/runtime.py` owns model loading, warmup, token limits,
embedding and reranking. `contracts.py` validates model identities and document
groups and defines the reranker prompt. `transform.py` applies the evaluated
embedding transform. `verification.py` checks adapter hashes and LoRA tensors.

The GPU service is excluded from the CPU uv workspace. Its dependencies are declared
in this directory's `pyproject.toml` and installed by the Modal image. CPU service
exports and Docker images do not install this project. Root pytest adds this source
directory for CPU tests; GPU imports occur only when a model is loaded or measured.

Modal images, GPU selection, snapshots, scaling, authenticated HTTP endpoints and
image-build weight downloads belong to
[infrastructure/inference/disclosure](../../infrastructure/inference/disclosure/README.md).
The deployment includes this package with `add_local_python_source(copy=True)`
before downloading weights into the model images.

Run CPU tests from the repository root:

```bash
./scripts/run-tests.sh services/disclosure-inference infrastructure/inference/disclosure
```
