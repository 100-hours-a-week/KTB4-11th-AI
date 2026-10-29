import json
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
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "embedding": {
                    "base_repo": "base",
                    "base_revision": "base-sha",
                    "adapter_repo": "adapter",
                    "adapter_revision": "adapter-sha",
                    "adapter_sha256": "weight-sha",
                }
            }
        )
    )
    weights.download_weights("embedding", str(manifest), str(tmp_path / "models"))
    assert calls == [
        ("base", "base-sha", str(tmp_path / "models/base")),
        ("adapter", "adapter-sha", str(tmp_path / "models/adapter")),
    ]
    assert (tmp_path / "models/identity.json").exists()
