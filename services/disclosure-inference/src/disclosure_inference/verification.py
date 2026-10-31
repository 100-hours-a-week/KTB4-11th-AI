import hashlib
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
