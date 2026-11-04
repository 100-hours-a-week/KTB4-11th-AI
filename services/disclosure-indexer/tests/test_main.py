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
    monkeypatch.setattr(entry, "index_documents", lambda engine, settings, **kwargs: result)
    if failed:
        with pytest.raises(SystemExit) as error:
            entry.main([])
        assert error.value.code == 1
    else:
        entry.main([])
    events = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")
    ]
    assert events[0]["message"] == "run_start"
    end = next(event for event in events if event["message"] == "run_end")
    assert end["indexed"] == result
    assert "collected" not in end
    assert end["outcome"] == ("error" if failed else "ok")


def test_default_deployment_without_model_config_skips_indexing(monkeypatch, capsys):
    monkeypatch.delenv("DISCLOSURE_ENABLED", raising=False)
    monkeypatch.delenv("DISCLOSURE_EMBEDDING_BASE_URI")
    monkeypatch.delenv("DISCLOSURE_EMBEDDING_MODEL")
    monkeypatch.setenv("DISCLOSURE_INDEXER_POSTGRES_DSN", "sqlite://")
    monkeypatch.setattr(
        entry.sa, "create_engine", lambda *args, **kwargs: pytest.fail("disabled indexer opened DB")
    )
    monkeypatch.setattr(entry, "index_documents", lambda *args: pytest.fail("disabled indexer ran"))
    entry.main([])
    events = [
        json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")
    ]
    assert any(event["message"] == "disclosure_index_skipped" for event in events)


def test_enabled_indexing_with_missing_model_config_fails(monkeypatch):
    from pydantic import ValidationError

    monkeypatch.setenv("DISCLOSURE_ENABLED", "true")
    monkeypatch.delenv("DISCLOSURE_EMBEDDING_BASE_URI")
    monkeypatch.delenv("DISCLOSURE_EMBEDDING_MODEL")
    monkeypatch.setenv("DISCLOSURE_INDEXER_POSTGRES_DSN", "sqlite://")
    monkeypatch.setattr(
        entry, "index_documents", lambda *args: pytest.fail("indexing ran without model config")
    )
    with pytest.raises(ValidationError):
        entry.main([])


def test_retry_cli_forwards_each_receipt(monkeypatch):
    monkeypatch.setenv("DISCLOSURE_INDEXER_POSTGRES_DSN", "sqlite://")
    received = []

    def index(engine, settings, *, retry_receipts):
        received.extend(retry_receipts)
        return {"failed": 0}

    monkeypatch.setattr(entry, "index_documents", index)
    entry.main(["--retry-receipt", "first", "--retry-receipt", "second"])
    assert received == ["first", "second"]


def test_disabled_retry_rejects_before_opening_database(monkeypatch):
    monkeypatch.setenv("DISCLOSURE_ENABLED", "false")
    monkeypatch.setenv("DISCLOSURE_INDEXER_POSTGRES_DSN", "sqlite://")
    monkeypatch.setattr(
        entry.sa, "create_engine", lambda *a, **k: pytest.fail("disabled retry opened DB")
    )
    with pytest.raises(ValueError, match="Enable disclosure indexing"):
        entry.main(["--retry-receipt", "first"])


def test_disabled_job_ignores_missing_dsn_and_invalid_budgets(monkeypatch, capsys):
    monkeypatch.setenv("DISCLOSURE_ENABLED", "false")
    monkeypatch.delenv("DISCLOSURE_INDEXER_POSTGRES_DSN", raising=False)
    monkeypatch.setenv("DISCLOSURE_INDEXER_CHUNK_BYTES", "64")
    monkeypatch.setenv("DISCLOSURE_INDEXER_OVERLAP_BYTES", "300")
    monkeypatch.setattr(entry.sa, "create_engine", lambda *a, **k: pytest.fail("opened database"))
    entry.main([])
    assert "disclosure_index_skipped" in capsys.readouterr().out
