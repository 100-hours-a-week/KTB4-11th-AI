from datetime import UTC, date, datetime

import pytest
import sqlalchemy as sa
from disclosure_http import app as app_module
from disclosure_http.app import create_app, published_by
from disclosure_http.settings import Settings
from disclosure_retrieval.inference import InputBudgetError
from disclosure_retrieval.queries import Evidence, SearchResult
from disclosure_retrieval.settings import Settings as RetrievalSettings
from fastapi.testclient import TestClient
from ktb_core.clock_file import write_clock
from ktb_core.krx import KST
from pydantic import ValidationError

PUBLIC_URL = "http://disclosure-http-pit:8000"
REPORT = {
    "receipt_id": "r1",
    "title": "사업보고서",
    "stock_code": "005930",
    "business_year": 2025,
    "kind": "annual",
    "detail_type": "A001",
    "period_end": date(2025, 12, 31),
    "published_date": date(2026, 3, 10),
    "status": "ready",
    "index_id": 9,
    "index_state": "ready",
}


@pytest.fixture
def clock(tmp_path):
    path = tmp_path / "as_of"
    write_clock(path, datetime(2026, 7, 6, 16, tzinfo=KST))
    return path


def make_client(engine, clock):
    return TestClient(create_app(engine, RetrievalSettings(), str(clock), PUBLIC_URL))


@pytest.fixture
def client(clock):
    engine = sa.create_engine("sqlite://")
    yield make_client(engine, clock)
    engine.dispose()


@pytest.mark.parametrize(
    ("as_of", "expected"),
    [
        (datetime(2026, 7, 6, 0, 1, tzinfo=KST), date(2026, 7, 5)),
        (datetime(2026, 7, 6, 23, 59, tzinfo=KST), date(2026, 7, 5)),
        (datetime(2026, 7, 5, 16, tzinfo=UTC), date(2026, 7, 5)),
    ],
)
def test_filings_count_from_the_day_before_the_decision(as_of, expected):
    assert published_by(as_of) == expected


def test_listing_uses_the_server_cutoff_and_returns_receipts(client, monkeypatch):
    seen = []

    def rows(conn, code, limit, as_of, *, model, dimensions, signature, detail_type):
        seen.append((code, limit, as_of, detail_type))
        assert signature == RetrievalSettings().index_signature
        return [REPORT]

    monkeypatch.setattr(app_module, "report_rows", rows)
    body = client.get("/disclosures/005930?limit=2&as_of=2026-12-31&detail_type=A001").json()
    assert seen == [("005930", 2, date(2026, 7, 5), "A001")]
    assert body[0]["receipt_id"] == "r1"
    assert "index_id" not in body[0]


def test_search_ignores_client_dates_and_hides_embedding_text(client, monkeypatch):
    seen = []
    evidence = Evidence(
        "r1", 1, "t", "005930", 2025, "annual", None, date(2026, 3, 10), "s", "risk", "d",
        0, 10, "text", "ctx", 0.9, embedding_text="secret embedding input",
    )  # fmt: skip

    def fake_search(engine, code, body, settings):
        seen.append(body)
        return SearchResult(reports=[], latest_evidence=[evidence], historical_evidence=[])

    monkeypatch.setattr(app_module, "search", fake_search)
    response = client.get(
        "/disclosures/005930/search?query=리스크&include_history=true&as_of=2026-12-31"
        "&history_start=2027-01-01"
    )
    assert response.status_code == 200
    [body] = seen
    assert (body.as_of, body.history_start, body.include_history) == (
        date(2026, 7, 5),
        None,
        True,
    )
    assert "embedding_text" not in response.json()["latest_evidence"][0]


@pytest.mark.parametrize(
    ("error", "status"),
    [(InputBudgetError("big"), 422), (RuntimeError("modal down"), 502)],
)
def test_search_failures_are_explicit(client, monkeypatch, error, status):
    def failing(*args):
        raise error

    monkeypatch.setattr(app_module, "search", failing)
    assert client.get("/disclosures/005930/search?query=q").status_code == status


def test_unreadable_clock_fails_closed(client, clock, monkeypatch):
    monkeypatch.setattr(app_module, "report_rows", lambda *a: pytest.fail("queried"))
    clock.unlink()
    assert client.get("/disclosures/005930").status_code == 503


@pytest.mark.parametrize(
    "path",
    [
        "/disclosures/5930",
        "/disclosures/005930?limit=4",
        "/disclosures/005930/search",
        "/disclosures/005930/financials",
        "/disclosures/005930/sections",
    ],
)
def test_invalid_requests_are_rejected(client, path):
    assert client.get(path).status_code == 422


def test_openapi_names_the_server_and_the_tools(client):
    spec = client.get("/openapi.json").json()
    assert spec["servers"] == [{"url": PUBLIC_URL}]
    operations = sorted(
        op["operationId"] for path in spec["paths"].values() for op in path.values()
    )
    assert operations == [
        "get_disclosure_financials",
        "get_disclosure_sections",
        "list_disclosures",
        "search_disclosures",
    ]


def test_settings_require_the_clock_and_public_url(monkeypatch):
    monkeypatch.setenv("DISCLOSURE_HTTP_POSTGRES_DSN", "postgresql+psycopg://ktb@localhost/ktb")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("DISCLOSURE_HTTP_EVALUATION_CLOCK_FILE", "/clock/as_of")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("DISCLOSURE_HTTP_PUBLIC_URL", PUBLIC_URL)
    assert Settings().public_url == PUBLIC_URL


def seed(engine):
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO corporations(stock_code,corp_code,name) VALUES "
                "('005930','0005930','회사')"
            )
        )
        for receipt, published, year, kind, period in (
            ("annual-2025", "2026-03-10", 2025, "annual", "2025-12-31"),
            ("q1-2026", "2026-05-15", 2026, "q1", "2026-03-31"),
            ("half-2026", "2026-07-06", 2026, "half", "2026-06-30"),
        ):
            conn.execute(
                sa.text(
                    "INSERT INTO disclosures(receipt_id,stock_code,corp_code,company_name,title,"
                    "business_year,kind,period_end,published_date,raw_payload,status) VALUES "
                    "(:receipt,'005930','0005930','회사',:receipt,:year,:kind,:period,"
                    ":published,'{}','ready')"
                ),
                {
                    "receipt": receipt,
                    "year": year,
                    "kind": kind,
                    "period": period,
                    "published": published,
                },
            )
            conn.execute(
                sa.text(
                    "INSERT INTO disclosure_financial_facts(receipt_id,stock_code,business_year,"
                    "kind,period_end,scope,statement_type,account_id,account_name,currency,"
                    "ordinal,source_value,value,normalized) VALUES "
                    "(:receipt,'005930',:year,:kind,:period,'CFS','BS','ifrs-full_Assets',"
                    "'자산총계','KRW',0,'1,000',1000,true)"
                ),
                {"receipt": receipt, "year": year, "kind": kind, "period": period},
            )
            document_id = conn.execute(
                sa.text(
                    "INSERT INTO disclosure_documents(receipt_id,archive_key,source_hash,"
                    "parser_version,documents) VALUES (:receipt,'key','hash','v1','[]')"
                    " RETURNING id"
                ),
                {"receipt": receipt},
            ).scalar_one()
            conn.execute(
                sa.text(
                    "INSERT INTO disclosure_sections(document_id,receipt_id,document_name,"
                    "document_order,ordinal,heading,topic,text,start_offset,end_offset,"
                    "parser_version) VALUES (:document,:receipt,'본문',0,0,'사업의 내용',"
                    "'business','text',0,4,'v1')"
                ),
                {"document": document_id, "receipt": receipt},
            )


def test_financials_exclude_filings_from_the_decision_day_on(engine, clock):
    seed(engine)
    client = make_client(engine, clock)
    facts = client.get("/disclosures/005930/financials?business_year=2026").json()
    assert [f["receipt_id"] for f in facts] == ["q1-2026"]
    half = client.get("/disclosures/005930/financials?period_end=2026-06-30").json()
    assert half == []
    write_clock(clock, datetime(2026, 7, 7, 8, tzinfo=KST))
    later = client.get("/disclosures/005930/financials?period_end=2026-06-30").json()
    assert [f["receipt_id"] for f in later] == ["half-2026"]


def test_sections_of_unpublished_filings_are_empty(engine, clock):
    seed(engine)
    client = make_client(engine, clock)
    visible = client.get("/disclosures/005930/sections?receipt_id=q1-2026").json()
    assert [s["heading"] for s in visible] == ["사업의 내용"]
    assert client.get("/disclosures/005930/sections?receipt_id=half-2026").json() == []
    assert client.get("/disclosures/000660/sections?receipt_id=q1-2026").json() == []
