import pytest
from news_preprocessor import __main__ as entry


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("NEWS_PREPROCESSOR_POSTGRES_DSN", "postgresql+psycopg://u@unused.invalid/db")
    monkeypatch.setattr(entry, "SOURCES", ("first", "second"))


@pytest.mark.parametrize(
    ("scrape_results", "embed_result", "code"),
    [
        ((True, True), True, 0),
        ((True, False), True, 1),
        ((True, True), False, 1),
    ],
)
def test_exit_code_reflects_every_step(env, monkeypatch, scrape_results, embed_result, code):
    results = iter(scrape_results)
    monkeypatch.setattr(entry, "scrape", lambda engine, source: next(results))
    monkeypatch.setattr(entry, "embed_pending", lambda engine, embedder, limit: embed_result)

    with pytest.raises(SystemExit) as exit_info:
        entry.main()

    assert exit_info.value.code == code


def test_a_failing_source_does_not_stop_the_others(env, monkeypatch):
    scraped = []
    monkeypatch.setattr(entry, "scrape", lambda engine, source: scraped.append(source) or False)
    monkeypatch.setattr(entry, "embed_pending", lambda engine, embedder, limit: True)

    with pytest.raises(SystemExit):
        entry.main()

    assert scraped == ["first", "second"]
