import sys
from types import SimpleNamespace


def test_image_build_downloads_both_revisions_before_serving(tmp_path, monkeypatch):
    import weights

    calls = []

    def download(repo_id, revision, local_dir, **kwargs):
        calls.append((repo_id, revision, local_dir))
        from pathlib import Path

        Path(local_dir).mkdir(parents=True)
        return local_dir

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=download))
    monkeypatch.setattr(weights, "verify_adapter", lambda path, expected: None)
    manifest = tmp_path / "model_config.py"
    manifest.write_text(
        "DISCLOSURE_MODELS = "
        + repr(
            {
                "transform_version": "test",
                "max_tokens": 16384,
                "reranker": {},
                "embedding": {
                    "base_repo": "base",
                    "base_revision": "base-sha",
                    "adapter_repo": "adapter",
                    "adapter_revision": "adapter-sha",
                    "adapter_sha256": "weight-sha",
                },
            }
        )
    )
    weights.download_weights("embedding", str(manifest), str(tmp_path / "models"))
    assert calls == [
        ("base", "base-sha", str(tmp_path / "models/base")),
        ("adapter", "adapter-sha", str(tmp_path / "models/adapter")),
    ]
    assert (tmp_path / "models/identity.json").exists()


def test_image_build_uses_frozen_manifest_instead_of_worker_defaults(tmp_path, monkeypatch):
    import json

    import weights

    calls = []
    checks = []

    def download(repo_id, revision, local_dir, **kwargs):
        calls.append((repo_id, revision))
        from pathlib import Path

        Path(local_dir).mkdir(parents=True)

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=download))
    monkeypatch.setattr(
        weights, "verify_adapter", lambda directory, expected: checks.append(expected)
    )
    config = tmp_path / "model_config.py"
    config.write_text("DISCLOSURE_MODELS = {'embedding': {'base_repo': 'wrong-default'}}")
    selected = {
        "transform_version": "context-int8-1024-v1",
        "max_tokens": 16384,
        "embedding": {
            "base_repo": "selected-base",
            "base_revision": "selected-base-revision",
            "adapter_repo": "selected-adapter",
            "adapter_revision": "selected-adapter-revision",
            "adapter_sha256": "selected-hash",
        },
        "reranker": {},
    }
    monkeypatch.setenv("DISCLOSURE_MODEL_MANIFEST", json.dumps(selected))
    weights.download_weights("embedding", str(config), str(tmp_path / "models"))
    assert calls == [
        ("selected-base", "selected-base-revision"),
        ("selected-adapter", "selected-adapter-revision"),
    ]
    assert checks == ["selected-hash"]
    assert json.loads((tmp_path / "models/identity.json").read_text()) == selected["embedding"]
