import io
import json
from datetime import date

import pytest
import sqlalchemy as sa
from disclosure_retrieval.queries import Report, SearchResult
from disclosure_retrieval.search import SearchRequest, disclosures, search
from disclosure_retrieval.settings import Settings
from pydantic import TypeAdapter


def seed(
    engine,
    stock,
    receipt,
    period,
    published,
    text,
    vector,
    kind="annual",
    indexed=True,
    model="test-model",
    detail_type=None,
):
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO corporations(stock_code,corp_code,name) VALUES "
                "(:stock,:corp,:name) ON CONFLICT DO NOTHING"
            ),
            {"stock": stock, "corp": f"00{stock}", "name": f"회사{stock}"},
        )
        conn.execute(
            sa.text(
                "INSERT INTO "
                "disclosures(receipt_id,stock_code,corp_code,company_name,title,business_yea"
                "r,kind,detail_type,period_end,published_date,raw_payload,status) VALUES "
                "(:receipt,:stock,:corp,:name,'보고서',:year,:kind,:detail_type,:period,:published,'{}','rea"
                "dy')"
            ),
            {
                "receipt": receipt,
                "stock": stock,
                "corp": f"00{stock}",
                "name": f"회사{stock}",
                "year": period.year if period else None,
                "kind": kind,
                "detail_type": detail_type,
                "period": period,
                "published": published,
            },
        )
        doc = conn.execute(
            sa.text(
                "INSERT INTO "
                "disclosure_documents(receipt_id,archive_key,source_hash,parser_version,docu"
                "ments) VALUES (:receipt,'test','test','v1','[]') RETURNING id"
            ),
            {"receipt": receipt},
        ).scalar_one()
        if indexed:
            idx = conn.execute(
                sa.text(
                    "INSERT INTO "
                    "disclosure_indexes(document_id,signature,model,dimensions,status,active) "
                    "VALUES (:doc,'v1',:model,2000,'ready',true) RETURNING id"
                ),
                {"doc": doc, "model": model},
            ).scalar_one()
            conn.execute(
                sa.text(
                    "INSERT INTO "
                    "disclosure_chunks(index_id,ordinal,section,topic,document_name,start_offset"
                    ",end_offset,text,embedding) VALUES "
                    "(:idx,0,'위험관리','risk','main.xml',0,:end,:text,CAST(:vector AS vector))"
                ),
                {"idx": idx, "end": len(text), "text": text, "vector": json.dumps(vector)},
            )


def test_latest_groups_corrections_and_keeps_unindexed(engine):
    v = [1.0] + [0.0] * 1999
    seed(engine, "005930", "20260301000001", date(2025, 12, 31), date(2026, 3, 1), "원본", v)
    seed(engine, "005930", "20260401000001", date(2025, 12, 31), date(2026, 4, 1), "정정", v)
    seed(
        engine,
        "005930",
        "20260501000001",
        date(2026, 3, 31),
        date(2026, 5, 1),
        "미색인",
        v,
        kind="q1",
        indexed=False,
    )
    result = {
        "reports": TypeAdapter(list[Report]).dump_python(
            disclosures(
                engine, "005930", Settings(), limit=3, as_of=date.fromisoformat("2026-06-01")
            ),
            mode="json",
        )
    }
    rows = result["reports"]
    assert len(rows) == 2
    assert rows[0]["index_state"] == "pending"
    assert rows[1]["published_date"] == "2026-04-01"
    assert "receipt_id" not in rows[0] and "url" not in rows[0]
    old = {
        "reports": TypeAdapter(list[Report]).dump_python(
            disclosures(engine, "005930", Settings(), as_of=date.fromisoformat("2026-03-15")),
            mode="json",
        )
    }["reports"]
    assert len(old) == 1 and old[0]["published_date"] == "2026-03-01"


def test_search_history_is_optional_and_company_date_isolated(engine, monkeypatch):
    v = [1.0] + [0.0] * 1999
    seed(engine, "005930", "20240301000001", date(2023, 12, 31), date(2024, 3, 1), "과거 소송", v)
    seed(engine, "005930", "20250301000001", date(2024, 12, 31), date(2025, 3, 1), "최근 소송", v)
    seed(engine, "005930", "20260301000001", date(2025, 12, 31), date(2026, 3, 1), "미래 소송", v)
    seed(engine, "000660", "20250301000002", date(2024, 12, 31), date(2025, 3, 1), "다른 기업", v)
    monkeypatch.setenv("DISCLOSURE_EMBEDDING_BASE_URI", "http://localhost/v1")
    monkeypatch.setenv("DISCLOSURE_EMBEDDING_MODEL", "test-model")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout: io.BytesIO(
            json.dumps({"data": [{"index": 0, "embedding": v}]}).encode()
        ),
    )
    body = {"query": "소송 위험", "latest_limit": 1, "as_of": "2025-06-01"}
    current = TypeAdapter(SearchResult).dump_python(
        search(engine, "005930", SearchRequest.model_validate(body), Settings()), mode="json"
    )
    assert [c["text"] for c in current["latest_evidence"]] == ["최근 소송"]
    assert current["historical_evidence"] == []
    body["include_history"] = True
    history = TypeAdapter(SearchResult).dump_python(
        search(engine, "005930", SearchRequest.model_validate(body), Settings()), mode="json"
    )
    assert [c["text"] for c in history["historical_evidence"]] == ["과거 소송"]
    assert all(
        c["receipt_id"] and c["document_name"] == "main.xml" and (c["start_offset"] == 0)
        for c in history["latest_evidence"]
    )


def test_empty_search_needs_no_embedding_configuration(engine):
    assert TypeAdapter(SearchResult).dump_python(
        search(engine, "005930", SearchRequest.model_validate({"query": "위험"}), Settings()),
        mode="json",
    ) == {
        "reports": [],
        "latest_evidence": [],
        "historical_evidence": [],
        "latest_searched": 0,
        "latest_returned": 0,
        "history_searched": 0,
        "history_returned": 0,
    }


def test_returned_evidence_includes_table_context(engine, monkeypatch):
    v = [1.0] + [0.0] * 1999
    seed(engine, "005930", "context-test", date(2025, 12, 31), date(2026, 3, 1), "A | 100", v)
    monkeypatch.setenv("DISCLOSURE_EMBEDDING_BASE_URI", "http://localhost/v1")
    monkeypatch.setenv("DISCLOSURE_EMBEDDING_MODEL", "test-model")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout: io.BytesIO(
            json.dumps({"data": [{"index": 0, "embedding": v}]}).encode()
        ),
    )
    data = TypeAdapter(SearchResult).dump_python(
        search(
            engine,
            "005930",
            SearchRequest.model_validate({"query": "매출", "as_of": "2026-06-01"}),
            Settings(),
        ),
        mode="json",
    )
    assert "context" in data["latest_evidence"][0]


def test_exact_search_scores_every_filtered_chunk_before_top_k(engine, monkeypatch):
    vector = [1.0] + [0.0] * 1999
    seed(engine, "005930", "exact-search", date(2025, 12, 31), date(2026, 3, 1), "기준", vector)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO disclosure_chunks "
                "(index_id,ordinal,section,topic,document_name,start_offset,end_offset,text,"
                "embedding) SELECT i.id,n,'위험','risk','main.xml',n,n+1,'후보 ' || n, "
                "CAST(:vector AS halfvec) FROM disclosure_indexes i JOIN "
                "disclosure_documents d ON d.id=i.document_id CROSS JOIN "
                "generate_series(1,600) AS series(n) WHERE d.receipt_id='exact-search'"
            ),
            {"vector": json.dumps(vector)},
        )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout: io.BytesIO(
            json.dumps({"data": [{"index": 0, "embedding": vector}]}).encode()
        ),
    )
    captured = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "filtered_chunks AS MATERIALIZED" in statement:
            captured.append((statement, parameters))

    sa.event.listen(engine, "before_cursor_execute", capture)
    try:
        response = TypeAdapter(SearchResult).dump_python(
            search(
                engine,
                "005930",
                SearchRequest.model_validate({"query": "위험", "as_of": "2026-06-01", "top_k": 6}),
                Settings(),
            ),
            mode="json",
        )
    finally:
        sa.event.remove(engine, "before_cursor_execute", capture)
    assert len(response["latest_evidence"]) == 6
    assert captured
    statement, parameters = captured[0]
    with engine.connect() as conn:
        plan = conn.exec_driver_sql(
            "EXPLAIN (ANALYZE, FORMAT JSON) " + statement, parameters
        ).scalar_one()[0]["Plan"]
    nodes, pending = ([], [plan])
    while pending:
        node = pending.pop()
        nodes.append(node)
        pending.extend(node.get("Plans", []))
    candidates = next(node for node in nodes if node.get("Subplan Name") == "CTE candidates")
    assert candidates["Actual Rows"] == 601


def _answer(vector):
    return lambda req, timeout: io.BytesIO(
        json.dumps({"data": [{"index": 0, "embedding": vector}]}).encode()
    )


def test_evidence_identifies_its_receipt_chunk_and_character_range(engine, monkeypatch):
    v = [1.0] + [0.0] * 1999
    seed(engine, "005930", "20260301000009", date(2025, 12, 31), date(2026, 3, 1), "위험 설명", v)
    monkeypatch.setattr("urllib.request.urlopen", _answer(v))
    item = TypeAdapter(SearchResult).dump_python(
        search(
            engine,
            "005930",
            SearchRequest.model_validate({"query": "위험", "as_of": "2026-06-01"}),
            Settings(),
        ),
        mode="json",
    )["latest_evidence"][0]
    assert item["receipt_id"] == "20260301000009"
    assert isinstance(item["chunk_id"], int)
    assert item["document_name"] == "main.xml"
    assert (item["start_offset"], item["end_offset"]) == (0, len("위험 설명"))


def test_a_partial_reindex_returns_the_evidence_that_matches_the_configured_space(
    engine, monkeypatch
):
    v = [1.0] + [0.0] * 1999
    seed(engine, "005930", "20260301000010", date(2025, 12, 31), date(2026, 3, 1), "현재 공간", v)
    seed(
        engine,
        "005930",
        "20260401000010",
        date(2026, 3, 31),
        date(2026, 4, 1),
        "다른 공간",
        v,
        kind="q1",
        model="retired-model",
    )
    monkeypatch.setattr("urllib.request.urlopen", _answer(v))
    response = TypeAdapter(SearchResult).dump_python(
        search(
            engine,
            "005930",
            SearchRequest.model_validate({"query": "위험", "as_of": "2026-06-01"}),
            Settings(),
        ),
        mode="json",
    )
    body = response
    assert [c["text"] for c in body["latest_evidence"]] == ["현재 공간"]
    assert {r["index_state"] for r in body["reports"]} == {"ready", "other_space"}


def test_no_distance_threshold_is_applied_unless_one_is_requested(engine, monkeypatch):
    stored = [1.0] + [0.0] * 1999
    orthogonal = [0.0, 1.0] + [0.0] * 1998
    seed(
        engine, "005930", "20260301000011", date(2025, 12, 31), date(2026, 3, 1), "먼 근거", stored
    )
    monkeypatch.setattr("urllib.request.urlopen", _answer(orthogonal))
    body = {"query": "위험", "as_of": "2026-06-01"}
    kept = TypeAdapter(SearchResult).dump_python(
        search(engine, "005930", SearchRequest.model_validate(body), Settings()), mode="json"
    )
    assert [c["text"] for c in kept["latest_evidence"]] == ["먼 근거"]
    assert (kept["latest_searched"], kept["latest_returned"]) == (1, 1)
    filtered = TypeAdapter(SearchResult).dump_python(
        search(
            engine,
            "005930",
            SearchRequest.model_validate({**body, "max_distance": 0.5}),
            Settings(),
        ),
        mode="json",
    )
    assert filtered["latest_evidence"] == []
    assert (filtered["latest_searched"], filtered["latest_returned"]) == (1, 0)


def test_modal_reranks_fifty_candidates_to_five_with_separate_scores(engine, monkeypatch):
    from disclosure_retrieval.settings import Settings
    from ktb_core.embedding.config import model_identity

    settings = Settings(
        DISCLOSURE_INFERENCE_BACKEND="modal",
        DISCLOSURE_EMBEDDING_DIMENSIONS=1024,
        DISCLOSURE_EMBEDDING_MAX_TOKENS=16384,
        DISCLOSURE_MODAL_QUERY_URL="https://query.modal.run",
        DISCLOSURE_MODAL_RERANK_URL="https://rerank.modal.run",
        DISCLOSURE_MODAL_KEY="key",
        DISCLOSURE_MODAL_SECRET="secret",
    )
    seed(
        engine,
        "005930",
        "modal-search",
        date(2025, 12, 31),
        date(2026, 3, 1),
        "legacy",
        [1.0] + [0.0] * 1999,
    )
    with engine.begin() as conn:
        idx = conn.execute(
            sa.text(
                "INSERT INTO disclosure_indexes "
                "(document_id,signature,model,dimensions,identity,status,active) SELECT "
                "id,'modal',:model,1024,CAST(:identity AS jsonb),'ready',true FROM "
                "disclosure_documents WHERE receipt_id='modal-search' RETURNING id"
            ),
            {"model": settings.index_model, "identity": json.dumps(model_identity("embedding"))},
        ).scalar_one()
        conn.execute(
            sa.text(
                "INSERT INTO disclosure_narrative_chunks "
                "(index_id,ordinal,section,topic,document_name,start_offset,end_offset,text,"
                "embedding_text,embedding) SELECT :idx,n,'위험','risk','main.xml',n,n+1,'본문 "
                "'||n,'회사 공시 본문 '||n,CAST(:v AS halfvec) FROM generate_series(1,60) n"
            ),
            {"idx": idx, "v": json.dumps([1.0] + [0.0] * 1023)},
        )
    calls = []

    def respond(request, timeout):
        payload = json.loads(request.data)
        calls.append(request.full_url)
        if request.full_url == "https://query.modal.run":
            result = {"identity": model_identity("embedding"), "vector": [1.0] + [0.0] * 1023}
        else:
            assert len(payload["candidates"]) == 50
            assert payload["candidates"][0]["text"].startswith("회사 공시 본문")
            result = {
                "identity": model_identity("reranker"),
                "scores": [
                    {"chunk_id": c["chunk_id"], "score": n / 50, "logit_difference": n}
                    for n, c in enumerate(payload["candidates"])
                ],
            }
        return io.BytesIO(json.dumps(result).encode())

    monkeypatch.setattr("urllib.request.urlopen", respond)
    monkeypatch.setattr(
        "urllib.request.OpenerDirector.open", lambda self, req, timeout: respond(req, timeout)
    )
    response = TypeAdapter(SearchResult).dump_python(
        search(
            engine,
            "005930",
            SearchRequest.model_validate({"query": "위험", "as_of": "2026-06-01"}),
            settings,
        ),
        mode="json",
    )
    rows = response["latest_evidence"]
    assert [row["text"] for row in rows] == ["본문 50", "본문 49", "본문 48", "본문 47", "본문 46"]
    assert rows[0]["score"] == 1
    assert rows[0]["embedding_text"].startswith("회사 공시 본문")
    assert rows[0]["reranker_score"] == 0.98
    assert rows[0]["reranker_logit_difference"] == 49
    assert calls == ["https://query.modal.run", "https://rerank.modal.run"]


def test_ready_empty_narrative_index_returns_empty_without_modal(engine, monkeypatch):
    from disclosure_retrieval.settings import Settings
    from ktb_core.embedding.config import model_identity

    settings = Settings(
        DISCLOSURE_INFERENCE_BACKEND="modal",
        DISCLOSURE_EMBEDDING_DIMENSIONS=1024,
        DISCLOSURE_EMBEDDING_MAX_TOKENS=16384,
        DISCLOSURE_MODAL_QUERY_URL="https://query.modal.run",
        DISCLOSURE_MODAL_RERANK_URL="https://rerank.modal.run",
        DISCLOSURE_MODAL_KEY="key",
        DISCLOSURE_MODAL_SECRET="secret",
    )
    seed(
        engine,
        "005930",
        "empty-narrative",
        date(2025, 12, 31),
        date(2026, 3, 1),
        "legacy",
        [1.0] + [0.0] * 1999,
    )
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO disclosure_indexes "
                "(document_id,signature,model,dimensions,identity,status,active) SELECT "
                "id,'modal',:model,1024,CAST(:identity AS jsonb),'ready',true FROM "
                "disclosure_documents WHERE receipt_id='empty-narrative'"
            ),
            {"model": settings.index_model, "identity": json.dumps(model_identity("embedding"))},
        )

    def forbidden(*a, **kw):
        pytest.fail("empty narrative search must not call Modal")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    monkeypatch.setattr(
        "urllib.request.OpenerDirector.open", lambda self, req, timeout: forbidden(req, timeout)
    )
    response = TypeAdapter(SearchResult).dump_python(
        search(
            engine,
            "005930",
            SearchRequest.model_validate({"query": "위험", "as_of": "2026-06-01"}),
            settings,
        ),
        mode="json",
    )
    assert response["latest_evidence"] == []


def test_legacy_rollback_can_use_same_source_before_parser_upgrade(engine, monkeypatch):
    vector = [1.0] + [0.0] * 1999
    seed(
        engine,
        "005930",
        "parser-rollback",
        date(2025, 12, 31),
        date(2026, 3, 1),
        "기존 파서의 근거",
        vector,
    )
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO disclosure_documents "
                "(receipt_id,archive_key,source_hash,parser_version,documents) VALUES "
                "('parser-rollback','test','test','sections-v2-content-kind','[]')"
            )
        )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **kw: io.BytesIO(
            json.dumps({"data": [{"index": 0, "embedding": vector}]}).encode()
        ),
    )
    response = TypeAdapter(SearchResult).dump_python(
        search(
            engine,
            "005930",
            SearchRequest.model_validate({"query": "위험", "as_of": "2026-06-01"}),
            Settings(),
        ),
        mode="json",
    )
    assert response["reports"][0]["index_state"] == "ready"
    assert response["latest_evidence"][0]["text"] == "기존 파서의 근거"


def test_legacy_rollback_never_reads_an_obsolete_source(engine, monkeypatch):
    seed(
        engine,
        "005930",
        "changed-source",
        date(2025, 12, 31),
        date(2026, 3, 1),
        "변경 전 근거",
        [1.0] + [0.0] * 1999,
    )
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO disclosure_documents "
                "(receipt_id,archive_key,source_hash,parser_version,documents) VALUES "
                "('changed-source','new-source','changed-hash','sections-v2-content-kind','["
                "]')"
            )
        )

    def forbidden(*a, **kw):
        raise AssertionError("An obsolete source must not trigger GPU inference")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    response = TypeAdapter(SearchResult).dump_python(
        search(
            engine,
            "005930",
            SearchRequest.model_validate({"query": "위험", "as_of": "2026-06-01"}),
            Settings(),
        ),
        mode="json",
    )
    assert response["reports"][0]["index_state"] == "pending"
    assert response["latest_evidence"] == []


def test_unrelated_nonperiodic_receipts_with_unknown_period_are_not_revisions(engine):
    vector = [1.0] + [0.0] * 1999
    for day in (1, 2, 3):
        seed(
            engine,
            "005930",
            f"filing-{day}",
            None,
            date(2026, 3, day),
            "중요 사건",
            vector,
            kind="F001",
            detail_type="F001",
        )
    reports = disclosures(engine, "005930", Settings(), as_of=date(2026, 4, 1))
    assert len(reports) == 3
    assert all(
        r.detail_type == "F001" and r.period_end is None and r.business_year is None
        for r in reports
    )


def test_detail_type_filter_applies_to_latest_history_counts_and_evidence(engine, monkeypatch):
    vector = [1.0] + [0.0] * 1999
    for day, detail_type in ((1, "F001"), (2, "F002"), (3, "F001"), (4, "F002")):
        seed(
            engine,
            "005930",
            f"typed-{day}",
            None,
            date(2026, 3, day),
            detail_type,
            vector,
            kind=detail_type,
            detail_type=detail_type,
        )
    monkeypatch.setattr("urllib.request.urlopen", _answer(vector))
    result = search(
        engine,
        "005930",
        SearchRequest(
            query="위험",
            detail_type="F001",
            latest_limit=1,
            include_history=True,
            as_of=date(2026, 4, 1),
        ),
        Settings(),
    )
    assert len(result.reports) == 1 and result.reports[0].published_date == date(2026, 3, 3)
    assert result.latest_searched == 1 and result.history_searched == 1
    assert [e.receipt_id for e in result.latest_evidence] == ["typed-3"]
    assert [e.receipt_id for e in result.historical_evidence] == ["typed-1"]
    for evidence in result.latest_evidence + result.historical_evidence:
        assert evidence.detail_type == "F001"
        assert evidence.business_year is None and evidence.period_end is None


def test_typed_periodic_correction_supersedes_legacy_filing(engine):
    vector = [1.0] + [0.0] * 1999
    seed(engine, "005930", "legacy", date(2025, 12, 31), date(2026, 3, 1), "원본", vector)
    seed(
        engine,
        "005930",
        "typed",
        date(2025, 12, 31),
        date(2026, 3, 2),
        "정정",
        vector,
        detail_type="A001",
    )
    reports = disclosures(engine, "005930", Settings(), as_of=date(2026, 4, 1))
    assert len(reports) == 1 and reports[0].published_date == date(2026, 3, 2)
