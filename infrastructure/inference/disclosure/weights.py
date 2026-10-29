import hashlib
import json
import runpy
from pathlib import Path


def verify_adapter(directory: str, expected_sha256: str) -> None:
    import numpy as np
    from safetensors.numpy import load_file

    path = Path(directory) / "adapter_model.safetensors"
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != expected_sha256:
        raise RuntimeError("adapter SHA256 does not match the selected checkpoint")
    tensors = load_file(path)
    b_tensors = [value for name, value in tensors.items() if "lora_B" in name]
    if not b_tensors or any(not np.any(value) for value in b_tensors):
        raise RuntimeError("adapter has missing or all-zero LoRA B tensors")
    if any(not np.all(np.isfinite(value)) for value in tensors.values()):
        raise RuntimeError("adapter contains nonfinite values")


def download_weights(
    kind: str, config_path: str = "/root/model_config.py", model_root: str = "/models"
) -> None:
    from huggingface_hub import snapshot_download

    spec = runpy.run_path(config_path)["DISCLOSURE_MODELS"][kind]
    for part in ("base", "adapter"):
        snapshot_download(
            repo_id=spec[f"{part}_repo"],
            revision=spec[f"{part}_revision"],
            local_dir=str(Path(model_root) / part),
            ignore_patterns=["*.bin", "*.h5", "*.msgpack", "*.ot"],
        )
    verify_adapter(str(Path(model_root) / "adapter"), spec["adapter_sha256"])
    (Path(model_root) / "identity.json").write_text(json.dumps(spec, sort_keys=True))
