import json

import pytest
from market_collector.__main__ import main

QDB = "postgresql://admin:quest@localhost:8812/qdb"
ACCOUNTS = '[{"app_key":"k1","secret_key":"s1"}]'


def _populate(monkeypatch):
    monkeypatch.setenv("MARKET_COLLECTOR_QUESTDB_DSN", QDB)
    monkeypatch.setenv("MARKET_COLLECTOR_QUESTDB_ILP_HOST", "localhost")
    monkeypatch.setenv("MARKET_COLLECTOR_KIWOOM_ACCOUNTS", ACCOUNTS)


def test_bare_invocation_validates_and_exits(monkeypatch, capsys):
    _populate(monkeypatch)
    monkeypatch.setattr("sys.argv", ["market-collector"])

    main()

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["message"] == "market-collector started"


def test_unknown_subcommand_exits_nonzero(monkeypatch):
    _populate(monkeypatch)
    monkeypatch.setattr("sys.argv", ["market-collector", "nope"])

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 2
