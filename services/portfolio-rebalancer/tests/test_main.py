from portfolio_rebalancer import __main__
from portfolio_rebalancer.__main__ import SERVICE_NAME


class Recorder:
    def __init__(self):
        self.logging = []
        self.disposed = 0
        self.ticks = []


def wire(monkeypatch, recorder, sent=3):
    monkeypatch.setattr(
        __main__,
        "setup_logging",
        lambda level, *, service_name: recorder.logging.append((level, service_name)),
    )
    monkeypatch.setattr(__main__, "Settings", lambda: _settings())
    monkeypatch.setattr(__main__, "build_client", lambda url: _closing("client"))
    monkeypatch.setattr(__main__, "bearer_token", lambda secret, subject: "a-token")
    monkeypatch.setattr(__main__.sa, "create_engine", lambda dsn: _engine(recorder))
    monkeypatch.setattr(
        __main__,
        "tick",
        lambda engine, client, token: recorder.ticks.append((engine, client, token)) or sent,
    )


def test_logging_is_set_up_under_the_service_name(monkeypatch):
    """#54 makes service_name a required keyword of setup_logging, so a tick that omitted
    it would die on startup. Stubbed, because core's signature lands with #54."""
    recorder = Recorder()
    wire(monkeypatch, recorder)

    __main__.main()

    assert recorder.logging == [("INFO", SERVICE_NAME)]


def test_the_tick_gets_the_engine_a_client_and_a_token(monkeypatch):
    recorder = Recorder()
    wire(monkeypatch, recorder)

    __main__.main()

    engine, client, token = recorder.ticks[0]
    assert (client, token) == ("client", "a-token")
    assert hasattr(engine, "begin")


def test_the_engine_is_disposed_even_when_the_tick_raises(monkeypatch):
    """A tick that dies must not leak the connection pool; compose runs this every hour."""
    recorder = Recorder()
    wire(monkeypatch, recorder)
    monkeypatch.setattr(__main__, "tick", lambda *args: (_ for _ in ()).throw(RuntimeError))

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
    backend_url = "http://backend:8080"
    postgres_dsn = "postgresql+psycopg://ktb:ktb@postgres:5432/ktb"
    backend_jwt_secret = _Secret("a-shared-secret-of-at-least-thirty-two-bytes")
    backend_jwt_subject = "portfolio-rebalancer"


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
