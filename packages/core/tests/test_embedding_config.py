import importlib
import subprocess
import sys

import pytest
from ktb_core.embedding import config

DISCLOSURE_OVERRIDES = (
    ("DISCLOSURE_EMBEDDING_BASE_REPO", "embedding", "base_repo", "other/base", "other/base"),
    ("DISCLOSURE_EMBEDDING_BASE_REVISION", "embedding", "base_revision", "base-v2", "base-v2"),
    ("DISCLOSURE_EMBEDDING_ADAPTER_REPO", "embedding", "adapter_repo", "other/lora", "other/lora"),
    (
        "DISCLOSURE_EMBEDDING_ADAPTER_REVISION",
        "embedding",
        "adapter_revision",
        "lora-v2",
        "lora-v2",
    ),
    ("DISCLOSURE_EMBEDDING_ADAPTER_SHA256", "embedding", "adapter_sha256", "a" * 64, "a" * 64),
    ("DISCLOSURE_EMBEDDING_DIMENSIONS", "embedding", "dimensions", "512", 512),
    ("DISCLOSURE_EMBEDDING_MAX_TOKENS", "embedding", "max_tokens", "8192", 8192),
    ("DISCLOSURE_RERANKER_BASE_REPO", "reranker", "base_repo", "other/ranker", "other/ranker"),
    ("DISCLOSURE_RERANKER_BASE_REVISION", "reranker", "base_revision", "base-v2", "base-v2"),
    ("DISCLOSURE_RERANKER_ADAPTER_REPO", "reranker", "adapter_repo", "other/lora", "other/lora"),
    ("DISCLOSURE_RERANKER_ADAPTER_REVISION", "reranker", "adapter_revision", "lora-v2", "lora-v2"),
    ("DISCLOSURE_RERANKER_ADAPTER_SHA256", "reranker", "adapter_sha256", "b" * 64, "b" * 64),
)
NAMES = (
    "KTB_EMBEDDING_MODEL",
    "KTB_EMBEDDING_DIMENSIONS",
    "KTB_EMBEDDING_MAX_TOKENS",
    *(item[0] for item in DISCLOSURE_OVERRIDES),
)


@pytest.fixture
def reloaded(monkeypatch):
    def reload(**env: str):
        for name in NAMES:
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        return importlib.reload(config)

    yield reload
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    importlib.reload(config)


def test_defaults_when_the_environment_is_unset(reloaded):
    module = reloaded()

    assert module.EMBEDDING_MODEL == "mlx-community/Qwen3-Embedding-4B-4bit-DWQ"
    assert module.EMBEDDING_DIMENSIONS == 2000
    assert module.EMBEDDING_MAX_TOKENS == 16384


def test_the_environment_overrides_the_defaults(reloaded):
    module = reloaded(
        KTB_EMBEDDING_MODEL="other-model",
        KTB_EMBEDDING_DIMENSIONS="1024",
        KTB_EMBEDDING_MAX_TOKENS="8192",
    )

    assert module.EMBEDDING_MODEL == "other-model"
    assert module.EMBEDDING_DIMENSIONS == 1024
    assert module.EMBEDDING_MAX_TOKENS == 8192


def test_importing_the_config_pulls_in_no_third_party_package():
    source = (
        "import sys; import ktb_core.embedding.config; "
        "print(sorted(name for name in sys.modules if name in ('httpx', 'urllib.request')))"
    )

    result = subprocess.run([sys.executable, "-c", source], capture_output=True, text=True)

    assert result.stdout.strip() == "[]", result.stdout


def test_disclosure_identity_keeps_the_selected_adapter_separate_from_news(reloaded):
    module = reloaded(KTB_EMBEDDING_MODEL="news-model", KTB_EMBEDDING_DIMENSIONS="2000")
    identity = module.model_identity("embedding")
    assert identity["base_repo"] == "perplexity-ai/pplx-embed-v2-context-9b-preview"
    assert (
        identity["adapter_sha256"]
        == "1f5d43671826dab233cc32942cedb31bd0f8564f42a4b8f359a4a100d66c60e2"
    )
    assert identity["dimensions"] == 1024
    assert identity["max_tokens"] == 16384
    identity["dimensions"] = 1
    assert module.model_identity("embedding")["dimensions"] == 1024


@pytest.mark.parametrize(("name", "kind", "field", "value", "expected"), DISCLOSURE_OVERRIDES)
def test_disclosure_environment_overrides_the_inference_identity(
    reloaded, name, kind, field, value, expected
):
    module = reloaded(**{name: value})

    assert module.model_identity(kind)[field] == expected
    assert module.EMBEDDING_DIMENSIONS == 2000
    assert module.EMBEDDING_MAX_TOKENS == 16384


@pytest.mark.parametrize(
    ("name", "kind", "field", "value", "expected"),
    [item for item in DISCLOSURE_OVERRIDES if item[1] == "embedding"],
)
def test_changing_embedding_metadata_retires_the_previous_index_space(
    reloaded, name, kind, field, value, expected
):
    previous_space = reloaded().embedding_space()

    changed_space = reloaded(**{name: value}).embedding_space()

    assert changed_space != previous_space
    assert reloaded(**{name: value}).embedding_space() == changed_space


def test_news_and_reranker_changes_keep_the_embedding_index_space(reloaded):
    previous_space = reloaded().embedding_space()

    module = reloaded(
        KTB_EMBEDDING_MODEL="other-news-model",
        KTB_EMBEDDING_DIMENSIONS="512",
        DISCLOSURE_RERANKER_ADAPTER_SHA256="b" * 64,
    )

    assert module.embedding_space() == previous_space
