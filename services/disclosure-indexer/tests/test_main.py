import json

import pytest
from disclosure_indexer import __main__ as entry


def _events(capsys) -> list[dict]:
    lines = (line for line in capsys.readouterr().out.splitlines() if line.startswith("{"))
    return list(map(json.loads, lines))


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setenv("DISCLOSURE_INDEXER_POSTGRES_DSN", "sqlite://")
    calls = []

    def stub(collection_failure):
        def collect(engine, client, settings):
            calls.append("collect")
            if collection_failure:
                raise RuntimeError("DART unavailable")
            return {"ready": 1, "failed": 0, "skipped": 0, "unavailable": 0}

        def index(engine, settings):
            calls.append("index")
            return {"ready": 1, "failed": 0, "chunks": 3}

        monkeypatch.setattr(entry, "collect", collect)
        monkeypatch.setattr(entry, "index_documents", index)
        return calls

    return stub


@pytest.mark.parametrize("collection_failure", [False, True])
def test_collection_precedes_indexing_and_failure_does_not_skip_old_documents(
    run, collection_failure
):
    calls = run(collection_failure)
    if collection_failure:
        with pytest.raises(SystemExit) as result:
            entry.main()
        assert result.value.code == 1
    else:
        entry.main()
    assert calls == ["collect", "index"]


def test_the_run_is_bracketed_by_run_start_and_run_end(run, capsys):
    run(False)
    entry.main()

    events = _events(capsys)
    assert [event["message"] for event in events][0] == "run_start"
    (end,) = [event for event in events if event["message"] == "run_end"]
    assert end["outcome"] == "ok"
    assert end["collected"] == {"ready": 1, "failed": 0, "skipped": 0, "unavailable": 0}
    assert end["indexed"] == {"ready": 1, "failed": 0, "chunks": 3}


def test_a_collection_failure_is_reported_on_run_end_without_skipping_indexing(run, capsys):
    run(True)
    with pytest.raises(SystemExit):
        entry.main()

    (end,) = [event for event in _events(capsys) if event["message"] == "run_end"]
    assert end["outcome"] == "error"
    assert end["stage"] == "collection"
    assert end["indexed"] == {"ready": 1, "failed": 0, "chunks": 3}
