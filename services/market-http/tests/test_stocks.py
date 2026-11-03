import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from market_http.app import create_app
from sqlalchemy.pool import StaticPool

from .test_market_subscription import FakeValkey


@pytest.fixture
def engine():
    engine = sa.create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE corporations (stock_code TEXT PRIMARY KEY, corp_code TEXT,"
                " name TEXT, eng_name TEXT, market TEXT, sector_name TEXT, synced_at TEXT)"
            )
        )
        conn.execute(sa.text("CREATE TABLE themes (theme_code TEXT PRIMARY KEY, name TEXT)"))
        conn.execute(
            sa.text(
                "CREATE TABLE theme_corporations (theme_code TEXT, stock_code TEXT,"
                " is_major BOOLEAN, PRIMARY KEY (theme_code, stock_code))"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO corporations VALUES"
                " ('005930', '00126380', '삼성전자', 'SAMSUNG ELECTRONICS CO., LTD.', 'KOSPI',"
                " '전기전자', '2026-11-03T09:00:00+09:00'),"
                " ('222222', '00999999', '무테마', NULL, 'KOSDAQ', '', '2026-11-03T09:00:00+09:00')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO themes VALUES ('550', '반도체_생산'),"
                " ('515', '스마트폰_삼성전자관련주')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO theme_corporations VALUES ('550', '005930', 1), ('515', '005930', 0)"
            )
        )
    yield engine
    engine.dispose()


@pytest.fixture
def client(engine):
    with TestClient(create_app(FakeValkey(), "unused", postgres_engine=engine)) as client:
        yield client


def test_stock_detail_includes_themes_in_code_order(client):
    response = client.get("/stocks/005930")

    assert response.status_code == 200
    assert response.json() == {
        "code": "005930",
        "name": "삼성전자",
        "english_name": "SAMSUNG ELECTRONICS CO., LTD.",
        "market": "KOSPI",
        "sector_name": "전기전자",
        "corp_code": "00126380",
        "synced_at": "2026-11-03T09:00:00+09:00",
        "themes": [
            {"code": "515", "name": "스마트폰_삼성전자관련주", "is_major": False},
            {"code": "550", "name": "반도체_생산", "is_major": True},
        ],
    }


def test_stock_without_themes_returns_nullable_and_empty_fields(client):
    body = client.get("/stocks/222222").json()

    assert (body["english_name"], body["sector_name"], body["themes"]) == (None, "", [])


def test_missing_stock_returns_404(client):
    response = client.get("/stocks/999999")

    assert (response.status_code, response.json()) == (404, {"detail": "stock not found"})


@pytest.mark.parametrize("code", ["00593", "005930A", "00593a"])
def test_invalid_code_returns_422(client, code):
    assert client.get(f"/stocks/{code}").status_code == 422


def test_database_failure_returns_503():
    engine = sa.create_engine("sqlite://", poolclass=StaticPool)
    with TestClient(create_app(FakeValkey(), "unused", postgres_engine=engine)) as client:
        response = client.get("/stocks/005930")

    assert (response.status_code, response.json()) == (503, {"detail": "stock data unavailable"})


def test_missing_engine_returns_503():
    with TestClient(create_app(FakeValkey(), "unused")) as client:
        response = client.get("/stocks/005930")

    assert (response.status_code, response.json()) == (503, {"detail": "stock data unavailable"})


def test_openapi_documents_stock_detail_and_drops_stock_themes(client):
    paths = client.get("/openapi.json").json()["paths"]

    assert set(paths["/stocks/{code}"]["get"]["responses"]) >= {"200", "404", "422", "503"}
    assert "/stocks/{code}/themes" not in paths
    assert client.get("/stocks/005930/themes").status_code == 404
