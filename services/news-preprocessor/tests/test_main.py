import json

from news_preprocessor.__main__ import main

DSN = "postgresql://ktb:ktb@localhost:5432/news"


def test_main_runs_to_completion(monkeypatch, capsys):
    monkeypatch.setenv("NEWS_PREPROCESSOR_POSTGRES_DSN", DSN)

    main()

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["message"] == "news-preprocessor started"
    assert payload["level"] == "INFO"
