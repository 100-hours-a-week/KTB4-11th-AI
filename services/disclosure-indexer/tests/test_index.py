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
    settings = _settings(max_attempts=3)
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


def test_modal_grouping_keeps_attachment_boundaries_and_excludes_tables():
    from disclosure_indexer.index import narrative_groups

    report = {
        **REPORT,
        "documents": [
            {
                "name": "a.xml",
                "sections": [
                    {
                        "section": "사업",
                        "topic": "business",
                        "text": "사업 설명",
                        "start": 0,
                        "end": 5,
                        "document": "a.xml",
                        "content_kind": "narrative",
                    },
                    {
                        "section": "매출",
                        "topic": "business",
                        "text": "매출 | 100",
                        "start": 7,
                        "end": 15,
                        "document": "a.xml",
                        "content_kind": "table",
                    },
                ],
            },
            {
                "name": "b.xml",
                "sections": [
                    {
                        "section": "위험",
                        "topic": "risk",
                        "text": "소송 위험",
                        "start": 0,
                        "end": 5,
                        "document": "b.xml",
                        "content_kind": "narrative",
                    }
                ],
            },
        ],
    }
    chunks, groups = narrative_groups(report, _settings())
    assert [c["text"] for c in chunks] == ["사업 설명", "소송 위험"]
    assert [g["document_id"] for g in groups] == ["a.xml:0", "b.xml:0"]
    assert [g["chunks"][0]["chunk_id"] for g in groups] == [0, 1]


def test_modal_index_preserves_legacy_vectors_and_is_idempotent(report_engine, monkeypatch):
    from ktb_core.embedding.config import model_identity

    settings = _settings(
        DISCLOSURE_INFERENCE_BACKEND="modal",
        DISCLOSURE_EMBEDDING_DIMENSIONS=1024,
        DISCLOSURE_EMBEDDING_MAX_TOKENS=16384,
        DISCLOSURE_MODAL_DOCUMENT_URL="https://documents.modal.run",
        DISCLOSURE_MODAL_KEY="key",
        DISCLOSURE_MODAL_SECRET="secret",
    )
    with report_engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE disclosure_documents SET documents=jsonb_set("
                "documents, '{0,sections,0,content_kind}', '\"narrative\"') "
                "WHERE receipt_id='index-test'"
            )
        )
    calls = []

    def respond(request, timeout):
        payload = json.loads(request.data)
        calls.append(payload)
        return io.BytesIO(
            json.dumps(
                {
                    "identity": model_identity("embedding"),
                    "vectors": {
                        str(c["chunk_id"]): [1.0] + [0.0] * 1023
                        for g in payload["documents"]
                        for c in g["chunks"]
                    },
                }
            ).encode()
        )

    monkeypatch.setattr("urllib.request.urlopen", respond)
    monkeypatch.setattr(
        "urllib.request.OpenerDirector.open", lambda self, req, timeout: respond(req, timeout)
    )
    assert index_documents(report_engine, settings)["ready"] == 1
    assert index_documents(report_engine, settings)["ready"] == 0
    assert len(calls) == 1
    with report_engine.connect() as conn:
        assert (
            conn.execute(sa.text("SELECT count(*) FROM disclosure_narrative_chunks")).scalar_one()
            >= 1
        )
        identity = conn.execute(
            sa.text(
                "SELECT identity FROM disclosure_indexes i JOIN "
                "disclosure_documents d ON d.id=i.document_id WHERE d.receipt_id='index-test'"
            )
        ).scalar_one()
        assert identity["adapter_sha256"].startswith("1f5d4367")


def test_nonperiodic_embedding_metadata_preserves_type_without_inventing_a_fiscal_period():
    report = {
        **REPORT,
        "business_year": None,
        "period_end": None,
        "kind": "F001",
        "detail_type": "F001",
    }
    value = embedding_inputs(report, [_chunk("본문")], 2000)[0]
    assert value.startswith("삼성전자 (005930) F001\n")
    assert "None" not in value


def test_parser_other_narrative_is_kept_for_modal_indexing():
    from disclosure_indexer.index import narrative_groups
    from news_preprocessor.sources.publishers.opendart.parser import parse_document

    document = parse_document(
        (
            "<DOCUMENT><TITLE>유형자산 양도결정</TITLE>"
            "<P>본사는 토지와 건물을 처분합니다.</P></DOCUMENT>"
        ).encode(),
        "asset-disposal.xml",
    )
    assert document["sections"][0]["topic"] == "other"
    chunks, groups = narrative_groups({**REPORT, "documents": [document]}, _settings())
    assert [chunk["text"] for chunk in chunks] == ["본사는 토지와 건물을 처분합니다."]
    assert groups[0]["document_id"] == "asset-disposal.xml:0"


def test_table_only_modal_index_is_empty_and_search_reports_it_without_gpu(
    report_engine, monkeypatch
):
    from datetime import date

    from disclosure_retrieval.queries import report_rows
    from disclosure_retrieval.search import SearchRequest, search
    from disclosure_retrieval.settings import Settings as RetrievalSettings

    settings = _settings(
        DISCLOSURE_INFERENCE_BACKEND="modal",
        DISCLOSURE_EMBEDDING_DIMENSIONS=1024,
        DISCLOSURE_EMBEDDING_MAX_TOKENS=16384,
        DISCLOSURE_MODAL_DOCUMENT_URL="https://documents.modal.run",
        DISCLOSURE_MODAL_KEY="key",
        DISCLOSURE_MODAL_SECRET="secret",
    )
    docs = [
        {
            "name": "table.xml",
            "sections": [
                {
                    "section": "표",
                    "topic": "other",
                    "text": "금액 | 100",
                    "start": 0,
                    "end": 8,
                    "document": "table.xml",
                    "content_kind": "table",
                }
            ],
        }
    ]
    with report_engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE disclosure_documents SET documents=CAST(:docs AS jsonb) "
                "WHERE receipt_id='index-test'"
            ),
            {"docs": json.dumps(docs)},
        )
    monkeypatch.setattr(
        "disclosure_indexer.index.embed_documents",
        lambda *a: pytest.fail("empty content called GPU"),
    )
    result = index_documents(report_engine, settings)
    assert result["ready"] == 0 and result["empty"] == 1
    assert index_documents(report_engine, settings)["empty"] == 0
    with report_engine.connect() as conn:
        state = conn.execute(sa.text("SELECT status,active FROM disclosure_indexes")).one()
        assert state.status == "empty" and state.active is False
        reports = report_rows(
            conn,
            "990001",
            3,
            date(2026, 1, 1),
            settings.index_model,
            1024,
            settings.index_signature,
        )
        assert reports[0]["index_state"] == "empty"
    retrieval = RetrievalSettings(
        DISCLOSURE_INFERENCE_BACKEND="modal",
        DISCLOSURE_EMBEDDING_DIMENSIONS=1024,
        DISCLOSURE_EMBEDDING_MAX_TOKENS=16384,
        DISCLOSURE_MODAL_QUERY_URL="https://query.modal.run",
        DISCLOSURE_MODAL_RERANK_URL="https://rerank.modal.run",
        DISCLOSURE_MODAL_KEY="key",
        DISCLOSURE_MODAL_SECRET="secret",
    )
    monkeypatch.setattr(
        "disclosure_retrieval.inference.embed_query",
        lambda *a: pytest.fail("empty search called GPU"),
    )
    result = search(
        report_engine, "990001", SearchRequest(query="자산", as_of=date(2026, 1, 1)), retrieval
    )
    assert result.reports[0].index_state == "empty"
    assert result.latest_evidence == [] and result.latest_searched == 0


def test_explicit_retry_recovers_dead_letter_once_without_duplicate_chunks(
    report_engine, monkeypatch
):
    settings = _settings(max_attempts=2)

    def fail(*args, **kwargs):
        raise TimeoutError()

    monkeypatch.setattr("disclosure_indexer.index.embed_vectors", fail)
    for _ in range(2):
        assert index_documents(report_engine, settings)["failed"] == 1
    assert index_documents(report_engine, settings)["failed"] == 0
    monkeypatch.setattr(
        "disclosure_indexer.index.embed_vectors",
        lambda texts, *a: [[1.0] + [0.0] * 1999 for _ in texts],
    )
    with report_engine.begin() as conn:
        conn.execute(
            sa.text("""
            INSERT INTO disclosures(receipt_id,stock_code,corp_code,company_name,title,
                business_year,kind,period_end,published_date,raw_payload,status)
            SELECT 'unrelated',stock_code,corp_code,company_name,title,business_year,kind,
                period_end,published_date,raw_payload,status FROM disclosures
            WHERE receipt_id='index-test'
        """)
        )
        conn.execute(
            sa.text("""
            INSERT INTO disclosure_documents(receipt_id,archive_key,source_hash,
                parser_version,documents)
            SELECT 'unrelated',archive_key,source_hash,parser_version,documents
            FROM disclosure_documents WHERE receipt_id='index-test'
        """)
        )
    result = index_documents(report_engine, settings, retry_receipts=("index-test",))
    assert result["requeued"] == 1 and result["ready"] == 1
    with report_engine.connect() as conn:
        state = conn.execute(
            sa.text("""
            SELECT i.status,i.attempts FROM disclosure_indexes i
            JOIN disclosure_documents d ON d.id=i.document_id WHERE d.receipt_id='index-test'
        """)
        ).one()
        assert state.status == "ready" and state.attempts == 0
        assert (
            conn.execute(
                sa.text("""
            SELECT i.status FROM disclosure_indexes i JOIN disclosure_documents d
            ON d.id=i.document_id WHERE d.receipt_id='unrelated'
        """)
            ).scalar_one()
            == "pending"
        )
        count = conn.execute(sa.text("SELECT count(*) FROM disclosure_chunks")).scalar_one()
    assert index_documents(report_engine, settings, retry_receipts=("index-test",))["ready"] == 0
    with report_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM disclosure_chunks")).scalar_one() == count
    with report_engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM disclosures WHERE receipt_id='unrelated'"))


@pytest.mark.parametrize(
    "changed",
    [
        {"embedding_model": "model-b"},
        {"chunk_bytes": 1200},
        {"max_tokens": 8192},
        {"query_instruction": "different prefix"},
    ],
)
def test_model_rollback_reactivates_existing_vectors_without_inference(
    report_engine, monkeypatch, changed
):
    from datetime import date

    from disclosure_retrieval.search import disclosures
    from disclosure_retrieval.settings import Settings as RetrievalSettings

    calls = []

    def embed(texts, *args):
        calls.append(texts)
        return [[1.0] + [0.0] * 1999 for _ in texts]

    monkeypatch.setattr("disclosure_indexer.index.embed_vectors", embed)
    a = _settings()
    b = a.model_copy(update=changed)
    assert index_documents(report_engine, a)["ready"] == 1
    assert index_documents(report_engine, b)["ready"] == 1
    assert len(calls) == 2
    settings = RetrievalSettings(
        DISCLOSURE_EMBEDDING_MODEL="test-model", DISCLOSURE_EMBEDDING_BASE_URI="http://model/v1"
    )
    rows = disclosures(report_engine, "990001", settings, as_of=date(2026, 1, 1))
    assert rows[0].index_state == "other_space"
    result = index_documents(report_engine, a)
    assert result["ready"] == 1
    assert len(calls) == 2
    settings = RetrievalSettings(
        DISCLOSURE_EMBEDDING_MODEL="test-model", DISCLOSURE_EMBEDDING_BASE_URI="http://model/v1"
    )
    rows = disclosures(report_engine, "990001", settings, as_of=date(2026, 1, 1))
    assert rows[0].index_state == "ready"
    with report_engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM disclosure_indexes")).scalar_one() == 2
        assert (
            conn.execute(sa.text("SELECT model FROM disclosure_indexes WHERE active")).scalar_one()
            == "test-model"
        )


def test_interrupted_reactivation_keeps_ready_vectors_for_retry(report_engine, monkeypatch):
    monkeypatch.setattr(
        "disclosure_indexer.index.embed_vectors",
        lambda texts, *a: [[1.0] + [0.0] * 1999 for _ in texts],
    )
    a = _settings()
    b = a.model_copy(update={"embedding_model": "model-b"})
    index_documents(report_engine, a)
    index_documents(report_engine, b)
    monkeypatch.setattr(
        "disclosure_indexer.index.embed_vectors",
        lambda *a: pytest.fail("rollback must not call inference"),
    )

    def interrupt(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE disclosure_indexes SET active=true"):
            raise SystemExit("interrupted")

    sa.event.listen(report_engine, "after_cursor_execute", interrupt)
    try:
        with pytest.raises(SystemExit, match="interrupted"):
            index_documents(report_engine, a)
    finally:
        sa.event.remove(report_engine, "after_cursor_execute", interrupt)
    with report_engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT model,status,active FROM disclosure_indexes ORDER BY id")
        ).all()
        assert rows == [("test-model", "ready", False), ("model-b", "ready", True)]
    assert index_documents(report_engine, a)["ready"] == 1


def test_modal_context_rollback_restores_existing_vectors(report_engine, monkeypatch):
    from datetime import date

    from disclosure_retrieval.search import disclosures
    from disclosure_retrieval.settings import Settings as RetrievalSettings

    with report_engine.begin() as conn:
        docs = conn.execute(sa.text("SELECT documents FROM disclosure_documents")).scalar_one()
        docs[0]["sections"][0]["content_kind"] = "narrative"
        conn.execute(
            sa.text("UPDATE disclosure_documents SET documents=CAST(:docs AS jsonb)"),
            {"docs": json.dumps(docs)},
        )
    calls = []

    def embed(groups, settings):
        calls.append(groups)
        return {c["chunk_id"]: [1.0] + [0.0] * 1023 for g in groups for c in g["chunks"]}

    monkeypatch.setattr("disclosure_indexer.index.embed_documents", embed)
    shared = dict(
        DISCLOSURE_INFERENCE_BACKEND="modal",
        DISCLOSURE_EMBEDDING_DIMENSIONS=1024,
        DISCLOSURE_EMBEDDING_MAX_TOKENS=16384,
        DISCLOSURE_MODAL_KEY="key",
        DISCLOSURE_MODAL_SECRET="secret",
    )
    a = _settings(**shared, DISCLOSURE_MODAL_DOCUMENT_URL="https://document.modal.run")
    b = a.model_copy(update={"context_bytes": 11000})
    assert index_documents(report_engine, a)["ready"] == 1
    assert index_documents(report_engine, b)["ready"] == 1
    assert index_documents(report_engine, a)["ready"] == 1
    assert len(calls) == 2
    retrieval = RetrievalSettings(
        **shared,
        DISCLOSURE_MODAL_QUERY_URL="https://query.modal.run",
        DISCLOSURE_MODAL_RERANK_URL="https://rerank.modal.run",
    )
    assert a.index_signature == retrieval.index_signature
    assert (
        disclosures(report_engine, "990001", retrieval, as_of=date(2026, 1, 1))[0].index_state
        == "ready"
    )


def test_process_death_consumes_claim_budget_and_eventually_dead_letters(
    report_engine, monkeypatch
):
    settings = _settings(max_attempts=3)
    calls = []

    def killed(*args):
        calls.append(1)
        raise SystemExit("worker killed")

    monkeypatch.setattr("disclosure_indexer.index.embed_vectors", killed)
    for attempt in range(1, 4):
        with pytest.raises(SystemExit):
            index_documents(report_engine, settings)
        with report_engine.begin() as conn:
            row = conn.execute(sa.text("SELECT attempts,status FROM disclosure_indexes")).one()
            assert row == (attempt, "processing")
            conn.execute(
                sa.text("UPDATE disclosure_indexes SET lease_until=now()-interval '1 second'")
            )
    result = index_documents(report_engine, settings)
    assert result["failed"] == 1
    with report_engine.connect() as conn:
        assert (
            conn.execute(sa.text("SELECT status FROM disclosure_indexes")).scalar_one()
            == "dead_letter"
        )
    assert index_documents(report_engine, settings)["failed"] == 0
    assert len(calls) == 3
