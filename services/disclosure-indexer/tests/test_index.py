import io
import json
import logging

import pytest
import sqlalchemy as sa
from disclosure_indexer.index import chunk_column_type, embedding_inputs, index_documents
from disclosure_indexer.settings import Settings


@pytest.fixture
def report_engine(pg_engine):
    with pg_engine.begin() as c:
        c.execute(
            sa.text(
                "INSERT INTO corporations(stock_code,corp_code,name) VALUES "
                "('990001','00990001','테스트') ON CONFLICT DO NOTHING"
            )
        )
        c.execute(
            sa.text(
                "INSERT INTO "
                "disclosures(receipt_id,stock_code,corp_code,company_name,tit"
                "le,business_year,kind,period_end,published_date,raw_payload,"
                "status)"
                " VALUES "
                "('index-test','990001','00990001','테스트','분기보고서',2025,'q1','2"
                "025-03-31','2025-05-15','{}','ready')"
            )
        )
        docs = [
            {
                "name": "main.xml",
                "status": "ready",
                "body": "위험 설명",
                "sections": [
                    {
                        "section": "위험",
                        "topic": "risk",
                        "text": "위험 설명",
                        "start": 0,
                        "end": 5,
                        "document": "main.xml",
                    }
                ],
            }
        ]
        c.execute(
            sa.text(
                "INSERT INTO "
                "disclosure_documents(receipt_id,archive_key,source_hash,parser_version,documents)"
                " VALUES ('index-test','test','test','v1',CAST(:docs AS jsonb))"
            ),
            {"docs": json.dumps(docs)},
        )
    yield pg_engine
    with pg_engine.begin() as c:
        c.execute(sa.text("DELETE FROM disclosures WHERE receipt_id='index-test'"))
        c.execute(sa.text("DELETE FROM corporations WHERE stock_code='990001'"))


def test_chunk_column_type_reads_the_declared_halfvec_width(pg_conn):
    assert chunk_column_type(pg_conn) == "halfvec(2000)"


def test_indexing_rejects_a_chunk_column_of_another_type(report_engine, monkeypatch):
    settings = Settings(
        postgres_dsn="unused",
        DISCLOSURE_EMBEDDING_BASE_URI="http://localhost/v1",
        DISCLOSURE_EMBEDDING_MODEL="test-model",
    )
    monkeypatch.setattr("disclosure_indexer.index.chunk_column_type", lambda conn: "halfvec(1024)")
    with pytest.raises(RuntimeError, match="halfvec\\(2000\\).*halfvec\\(1024\\)"):
        index_documents(report_engine, settings)


def test_idempotent_index_and_failed_replacement_keep_active(report_engine, monkeypatch):
    settings = Settings(
        postgres_dsn="unused",
        DISCLOSURE_EMBEDDING_BASE_URI="http://localhost/v1",
        DISCLOSURE_EMBEDDING_MODEL="test-model",
    )
    v = [1.0] + [0.0] * 1999
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout: io.BytesIO(
            json.dumps({"data": [{"index": 0, "embedding": v}]}).encode()
        ),
    )
    assert index_documents(report_engine, settings)["ready"] == 1
    assert index_documents(report_engine, settings)["ready"] == 0

    def fail(req, timeout):
        raise TimeoutError()

    monkeypatch.setattr("urllib.request.urlopen", fail)
    settings.chunk_bytes = 1200
    assert index_documents(report_engine, settings)["failed"] == 1
    with report_engine.connect() as c:
        assert (
            c.execute(sa.text("SELECT count(*) FROM disclosure_indexes WHERE active")).scalar_one()
            >= 1
        )
        assert (
            c.execute(
                sa.text(
                    "SELECT count(*) FROM disclosure_chunks c JOIN disclosure_indexes "
                    "i ON i.id=c.index_id WHERE i.active AND i.document_id IN (SELECT "
                    "id FROM disclosure_documents WHERE receipt_id='index-test')"
                )
            ).scalar_one()
            == 1
        )


def _settings(**overrides):
    return Settings(
        postgres_dsn="unused",
        DISCLOSURE_EMBEDDING_BASE_URI="http://localhost/v1",
        DISCLOSURE_EMBEDDING_MODEL="test-model",
        **overrides,
    )


def test_a_document_that_always_fails_reaches_dead_letter(report_engine, monkeypatch):
    settings = _settings(DISCLOSURE_INDEXER_MAX_ATTEMPTS=3)
    attempts = []

    def fail(req, timeout):
        attempts.append(1)
        raise TimeoutError()

    monkeypatch.setattr("urllib.request.urlopen", fail)
    for _ in range(settings.max_attempts):
        assert index_documents(report_engine, settings)["failed"] == 1
    assert len(attempts) == settings.max_attempts

    with report_engine.connect() as c:
        state = c.execute(
            sa.text(
                "SELECT status, attempts FROM disclosure_indexes i "
                "JOIN disclosure_documents d ON d.id = i.document_id "
                "WHERE d.receipt_id = 'index-test'"
            )
        ).one()
    assert state.status == "dead_letter"
    assert state.attempts == settings.max_attempts

    # A dead-lettered document must stop consuming embedding calls.
    assert index_documents(report_engine, settings)["failed"] == 0
    assert len(attempts) == settings.max_attempts


def test_the_index_signature_covers_the_query_instruction(report_engine, monkeypatch):
    v = [1.0] + [0.0] * 1999
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout: io.BytesIO(
            json.dumps({"data": [{"index": 0, "embedding": v}]}).encode()
        ),
    )
    assert index_documents(report_engine, _settings())["ready"] == 1

    changed = _settings(DISCLOSURE_EMBEDDING_QUERY_INSTRUCTION="Instruct: 다르게\nQuery: ")
    assert index_documents(report_engine, changed)["ready"] == 1


REPORT = {
    "company_name": "삼성전자",
    "stock_code": "005930",
    "business_year": 2026,
    "kind": "half",
    "period_end": "2026-06-30",
}


def _chunk(text: str, section: str = "II. 사업의 내용", context: str = "(단위: 백만원)"):
    return {"section": section, "context": context, "text": text, "document": "main.xml"}


def test_embedding_input_keeps_the_company_and_period_when_the_heading_is_long():
    chunk = _chunk("본문", section="위험" * 4000)

    value = embedding_inputs(REPORT, [chunk], 2000)[0]

    assert value.startswith("삼성전자 (005930) 2026 half 2026-06-30\n")
    assert value.endswith("\n본문")
    assert len(value.encode("utf-8")) <= 2000


def test_trimmed_metadata_is_recorded(caplog):
    chunk = _chunk("본문", section="가" * 4000)

    with caplog.at_level(logging.WARNING):
        embedding_inputs(REPORT, [chunk], 2000)

    assert [record.getMessage() for record in caplog.records] == ["disclosure_metadata_trimmed"]


def test_embedding_input_rejects_a_budget_too_small_for_the_company_header():
    # The header alone is 43 bytes and the body another 7, so 40 cannot hold both.
    with pytest.raises(ValueError, match="metadata header"):
        embedding_inputs(REPORT, [_chunk("본문")], 40)


def test_embedding_input_budgets_long_context_and_preserves_chunk_body():
    from disclosure_indexer.chunks import chunk_sections

    text = "매출 | " + "가" * 4500 + "\n단위: 원\n" + "사업 내용 " * 200
    section = {
        "section": "위험" * 4500,
        "topic": "risk",
        "document": "main.xml",
        "text": text,
        "start": 0,
        "end": len(text),
    }
    chunks = chunk_sections([section])
    metadata = {
        "company_name": "기업",
        "stock_code": "005930",
        "business_year": 2025,
        "kind": "annual",
        "period_end": "2025-12-31",
    }
    inputs = embedding_inputs(metadata, chunks, 12000)
    assert len(inputs) == len(chunks)
    for value, chunk in zip(inputs, chunks, strict=True):
        assert len(value.encode("utf-8")) <= 12000
        assert value.endswith("\n" + chunk["text"])
    assert chunks[0]["context"].startswith("단위: 원")
    assert section["text"] == text
