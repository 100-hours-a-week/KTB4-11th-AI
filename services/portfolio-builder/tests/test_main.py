import json

from portfolio_builder.__main__ import main


def test_main_runs_to_completion(monkeypatch, capsys):
    monkeypatch.setenv("PORTFOLIO_BUILDER_POSTGRES_DSN", "postgresql://ktb:ktb@localhost:5432/news")
    monkeypatch.setenv(
        "PORTFOLIO_BUILDER_QUESTDB_DSN", "postgresql://admin:quest@localhost:8812/qdb"
    )
    monkeypatch.setenv("PORTFOLIO_BUILDER_NEWS_CLUSTERER_URL", "http://localhost:8000")

    main()

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["message"] == "portfolio-builder started"


def test_market_analyzer_is_importable():
    import ktb_market_analyzer

    assert hasattr(ktb_market_analyzer, "rsi")
