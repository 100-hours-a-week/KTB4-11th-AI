import importlib
import subprocess
import sys

import pytest
from ktb_core.embedding import config

NAMES = ("KTB_EMBEDDING_MODEL", "KTB_EMBEDDING_DIMENSIONS", "KTB_EMBEDDING_MAX_TOKENS")


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


def test_importing_the_config_does_not_import_the_client_or_urllib():
    source = (
        "import sys; import ktb_core.embedding.config; "
        "print(sorted(name for name in sys.modules "
        "if name.startswith('ktb_core.embedding.embed') or name == 'urllib.request'))"
    )

    result = subprocess.run([sys.executable, "-c", source], capture_output=True, text=True)

    assert result.stdout.strip() == "[]", result.stdout
