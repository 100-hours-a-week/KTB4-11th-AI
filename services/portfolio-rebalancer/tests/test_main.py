import logging

from portfolio_rebalancer import __main__


class Recorder:
    def __init__(self):
        self.logging = []
        self.disposed = 0
        self.ticks = []
        self.authenticated = []


def wire(monkeypatch, recorder, sent=3):
    monkeypatch.setattr(
        __main__,
        "setup_logging",
        lambda level, *, service_name: recorder.logging.append((level, service_name)),
    )
    monkeypatch.setattr(__main__, "Settings", lambda: _settings())
    monkeypatch.setattr(__main__, "connect", lambda conf: _closing("db"))
    monkeypatch.setattr(__main__, "build_client", lambda url: _closing("client"))
    monkeypatch.setattr(
        __main__, "access_token", lambda secret, subject, issuer: f"token-{subject}"
    )
    monkeypatch.setattr(
        __main__,
        "authenticate",
        lambda client, token: recorder.authenticated.append((client, token)),
    )
    monkeypatch.setattr(__main__.sa, "create_engine", lambda dsn: _engine(recorder))
    monkeypatch.setattr(
        __main__,
        "tick",
        lambda engine, db, client, *, token_for, log: (
            recorder.ticks.append((engine, db, client, token_for, log)) or sent
        ),
    )


def test_logging_is_set_up_under_the_service_name(monkeypatch):
    recorder = Recorder()
    wire(monkeypatch, recorder)

    __main__.main()

    assert recorder.logging == [("INFO", "portfolio-rebalancer")]


def test_the_tick_gets_the_engine_both_datastores_and_a_token(monkeypatch):
    recorder = Recorder()
    wire(monkeypatch, recorder)

    __main__.main()

    engine, db, client, token_for, log = recorder.ticks[0]
    assert (db, client) == ("db", "client")
    assert hasattr(engine, "begin")
    assert callable(log.info)
    # The handshake uses the service subject; the tick signs per user with the factory.
    assert recorder.authenticated == [("client", "token-ai-server")]
    assert token_for("4242") == "token-4242"


def test_the_engine_is_disposed_even_when_the_tick_raises(monkeypatch):
    """A tick that dies must not leak the connection pool; compose runs this every hour."""
    recorder = Recorder()
    wire(monkeypatch, recorder)
    monkeypatch.setattr(
        __main__, "tick", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError)
    )

    try:
        __main__.main()
    except RuntimeError:
        pass

    assert recorder.disposed == 1


def test_the_tick_owns_its_transactions_rather_than_being_handed_one(monkeypatch):
    """An order has to be committed before it is sent, so no single transaction may span
    the send. main() therefore hands over the engine, not a connection."""
    recorder = Recorder()
    wire(monkeypatch, recorder)

    __main__.main()

    handed = recorder.ticks[0][0]
    assert hasattr(handed, "begin")
    assert not isinstance(handed, str)


class _Secret:
    def __init__(self, value):
        self._value = value

    def get_secret_value(self):
        return self._value


class _settings:
    log_level = "INFO"
    backend_jwt_issuer = "https://stock-spoon.com"
    backend_url = "http://backend:8080"
    postgres_dsn = "postgresql+psycopg://ktb:ktb@postgres:5432/ktb"
    questdb_conf = "ws::addr=questdb:9000;"
    backend_jwt_secret = _Secret("a-shared-secret-of-at-least-thirty-two-bytes")


class _closing:
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        return self.name

    def __exit__(self, *args):
        return None


class _engine:
    def __init__(self, recorder):
        self._recorder = recorder

    def begin(self):
        return _closing("conn")

    def dispose(self):
        self._recorder.disposed += 1


def test_the_tick_is_handed_a_logger_bound_to_a_run_id(monkeypatch, caplog):
    """Every line of one pass shares a run_id, so two passes can never be read as one,
    and the logger name is the service's rather than whichever module logged."""
    recorder = Recorder()
    wire(monkeypatch, recorder)

    __main__.main()

    log = recorder.ticks[0][4]
    with caplog.at_level(logging.INFO):
        log.info("probe")

    record = caplog.records[-1]
    assert record.name == "portfolio_rebalancer.__main__"
    assert "run_id" in record.fields
