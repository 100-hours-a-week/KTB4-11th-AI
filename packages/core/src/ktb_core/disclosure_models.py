import hashlib
import json
from importlib.resources import files


def model_identity(kind: str) -> dict:
    manifest = json.loads(files("ktb_core").joinpath("disclosure_models.json").read_text())
    identity = {**manifest[kind], "max_tokens": manifest["max_tokens"]}
    if kind == "embedding":
        identity["transform_version"] = manifest["transform_version"]
    return identity


def embedding_space() -> str:
    identity = json.dumps(model_identity("embedding"), sort_keys=True).encode()
    return "modal:" + hashlib.sha256(identity).hexdigest()
