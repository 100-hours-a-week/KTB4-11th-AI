import pytest
from news_graph_builder.graph.settings import LlmSettings
from pydantic import ValidationError

REQUIRED = {
    "NEWS_GRAPH_BUILDER_LLM_BASE_URI": "http://llm.test/v1",
    "NEWS_GRAPH_BUILDER_LLM_MODEL": "test-model",
}


@pytest.fixture
def required_env(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)


def test_defaults(required_env):
    settings = LlmSettings()

    assert settings.summary_max_chars == 24000
    assert settings.llm_timeout == 120
    assert settings.max_entities == 30
    assert settings.max_relations == 50


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_missing_required_value_raises(required_env, monkeypatch, missing):
    monkeypatch.delenv(missing)

    with pytest.raises(ValidationError):
        LlmSettings()


@pytest.mark.parametrize(
    "name",
    [
        "NEWS_GRAPH_BUILDER_SUMMARY_MAX_CHARS",
        "NEWS_GRAPH_BUILDER_LLM_TIMEOUT",
        "NEWS_GRAPH_BUILDER_MAX_ENTITIES",
        "NEWS_GRAPH_BUILDER_MAX_RELATIONS",
    ],
)
def test_non_positive_values_raise(required_env, monkeypatch, name):
    monkeypatch.setenv(name, "0")

    with pytest.raises(ValidationError):
        LlmSettings()


def test_the_api_key_is_optional_and_hidden_from_repr(required_env, monkeypatch):
    assert LlmSettings().llm_api_key is None

    monkeypatch.setenv("NEWS_GRAPH_BUILDER_LLM_API_KEY", "FAKE-LLM-KEY")
    settings = LlmSettings()

    assert settings.llm_api_key.get_secret_value() == "FAKE-LLM-KEY"
    assert "FAKE-LLM-KEY" not in repr(settings)
