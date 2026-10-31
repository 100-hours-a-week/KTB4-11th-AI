import json
import runpy
from pathlib import Path

from disclosure_inference.verification import verify_adapter


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
