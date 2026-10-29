import json

import pytest
from disclosure_indexer import __main__ as entry


@pytest.mark.parametrize("failed", [0, 1])
def test_indexer_reads_stored_documents_without_dart_credentials(monkeypatch, capsys, failed):
    monkeypatch.delenv("DISCLOSURE_INDEXER_DART_API_KEY", raising=False)
    monkeypatch.setenv("DISCLOSURE_INDEXER_POSTGRES_DSN", "sqlite://")
    monkeypatch.setattr(
        entry,
        "collect",
        lambda *args: pytest.fail("indexer must not collect disclosures"),
        raising=False,
    )
    result = {"ready": 1, "failed": failed, "chunks": 3}
    monkeypatch.setattr(entry, "index_documents", lambda engine, settings: result)
    if failed:
        with pytest.raises(SystemExit) as error:
            entry.main()
        assert error.value.code == 1
    else:
        entry.main()
    events = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")
    ]
    assert events[0]["message"] == "run_start"
    end = next(event for event in events if event["message"] == "run_end")
    assert end["indexed"] == result
    assert "collected" not in end
    assert end["outcome"] == ("error" if failed else "ok")
