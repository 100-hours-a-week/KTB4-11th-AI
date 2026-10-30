import json

import pytest
from news_preprocessor.sources.publishers.opendart.disclosures import __main__ as entry


@pytest.mark.parametrize("failed", [0, 1])
def test_disclosure_collection_does_not_require_or_call_an_embedding_model(
    monkeypatch, capsys, failed
):
    monkeypatch.setenv("NEWS_PREPROCESSOR_POSTGRES_DSN", "sqlite://")
    monkeypatch.setenv("NEWS_PREPROCESSOR_DART_API_KEY", "test")
    monkeypatch.delenv("KTB_EMBEDDING_BASE_URI", raising=False)
    monkeypatch.delenv("DISCLOSURE_EMBEDDING_MODEL", raising=False)
    result = {"ready": 1, "failed": failed, "skipped": 0, "unavailable": 0}
    monkeypatch.setattr(entry, "collect", lambda engine, client, settings: result)
    if failed:
        with pytest.raises(SystemExit) as error:
            entry.main()
        assert error.value.code == 1
    else:
        entry.main()
    events = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")
    ]
    end = next(event for event in events if event["message"] == "run_end")
    assert end["collected"] == result
    assert "indexed" not in end
    assert end["outcome"] == ("error" if failed else "ok")
