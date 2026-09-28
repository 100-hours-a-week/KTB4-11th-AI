from portfolio_rebalancer_http.settings import Settings


def test_defaults_need_no_environment():
    settings = Settings()

    assert settings.log_level == "INFO"
    assert settings.host == "0.0.0.0"
    assert settings.port == 8000


def test_reads_the_prefixed_environment(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_REBALANCER_HTTP_PORT", "9000")

    assert Settings().port == 9000
