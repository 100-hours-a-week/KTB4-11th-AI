import pytest
from disclosure_indexer import __main__ as entry


@pytest.mark.parametrize("collection_failure", [False, True])
def test_collection_precedes_indexing_and_failure_does_not_skip_old_documents(
    monkeypatch, collection_failure
):
    monkeypatch.setenv("DISCLOSURE_INDEXER_POSTGRES_DSN", "sqlite://")
    calls = []

    def collect(engine, client, settings):
        calls.append("collect")
        if collection_failure:
            raise RuntimeError("DART unavailable")
        return {"ready": 1, "failed": 0}

    def index(engine, settings):
        calls.append("index")
        return {"ready": 1, "failed": 0, "chunks": 1}

    monkeypatch.setattr(entry, "collect", collect)
    monkeypatch.setattr(entry, "index_documents", index)
    if collection_failure:
        with pytest.raises(SystemExit) as result:
            entry.main()
        assert result.value.code == 1
    else:
        entry.main()
    assert calls == ["collect", "index"]
