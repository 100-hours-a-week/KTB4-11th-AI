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
        conn.execute(sa.text("CREATE TABLE corporations (stock_code TEXT PRIMARY KEY, name TEXT)"))
        conn.execute(
            sa.text("CREATE TABLE themes (theme_code TEXT PRIMARY KEY, name TEXT, synced_at TEXT)")
        )
        conn.execute(
            sa.text(
                "CREATE TABLE theme_companies (theme_code TEXT, stock_code TEXT, is_major BOOLEAN,"
                " PRIMARY KEY (theme_code, stock_code))"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO corporations VALUES ('005930', '삼성전자'), ('000660', 'SK하이닉스'),"
                " ('035420', 'NAVER'), ('373220', 'LG에너지솔루션')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO themes VALUES ('T002', '인공지능', '2026-11-01'),"
                " ('T001', '반도체', '2026-11-01'), ('T003', '빈테마', '2026-11-01')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO theme_companies VALUES ('T001', '005930', 1), ('T001', '000660', 1),"
                " ('T002', '005930', 0), ('T002', '035420', 1)"
            )
        )
    yield engine
    engine.dispose()


@pytest.fixture
def client(engine):
    with TestClient(create_app(FakeValkey(), "unused", postgres_engine=engine)) as client:
        yield client


def test_themes_returns_every_theme_as_array_ordered_by_code(client):
    response = client.get("/themes")

    assert response.status_code == 200
    assert response.json() == [
        {"code": "T001", "name": "반도체"},
        {"code": "T002", "name": "인공지능"},
        {"code": "T003", "name": "빈테마"},
    ]


def test_themes_ignores_search_and_pagination_parameters(client):
    assert client.get("/themes?limit=1&q=반도체").json() == client.get("/themes").json()


def test_theme_stocks_returns_relation_ordered_by_stock_code(client):
    response = client.get("/themes/T001/stocks")

    assert response.status_code == 200
    assert response.json() == [
        {"code": "000660", "name": "SK하이닉스", "is_major": True},
        {"code": "005930", "name": "삼성전자", "is_major": True},
    ]


def test_theme_stocks_pages_without_duplicates_or_gaps(client):
    pages = [client.get(f"/themes/T002/stocks?limit=1&offset={offset}").json() for offset in (0, 1)]
    last = client.get("/themes/T002/stocks?limit=1&offset=2").json()

    assert pages == [
        [{"code": "005930", "name": "삼성전자", "is_major": False}],
        [{"code": "035420", "name": "NAVER", "is_major": True}],
    ]
    assert last == []


def test_stock_themes_returns_same_relation_ordered_by_theme_code(client):
    response = client.get("/stocks/005930/themes")

    assert response.status_code == 200
    assert response.json() == [
        {"code": "T001", "name": "반도체", "is_major": True},
        {"code": "T002", "name": "인공지능", "is_major": False},
    ]


def test_existing_identifiers_without_relations_return_empty_arrays(client):
    assert client.get("/themes/T003/stocks").json() == []
    assert client.get("/stocks/373220/themes").json() == []


def test_missing_identifiers_return_404(client):
    theme = client.get("/themes/T999/stocks")
    stock = client.get("/stocks/999999/themes")

    assert (theme.status_code, theme.json()) == (404, {"detail": "theme not found"})
    assert (stock.status_code, stock.json()) == (404, {"detail": "stock not found"})


def test_theme_detail_route_is_not_provided(client):
    assert client.get("/themes/T001").status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/themes/T001/stocks?limit=0",
        "/themes/T001/stocks?limit=1001",
        "/themes/T001/stocks?offset=-1",
        "/themes/T-01/stocks",
        "/stocks/00593/themes",
        "/stocks/005930A/themes",
    ],
)
def test_invalid_parameters_return_422(client, path):
    assert client.get(path).status_code == 422


@pytest.mark.parametrize("path", ["/themes", "/themes/T001/stocks", "/stocks/005930/themes"])
def test_database_failures_return_503(path):
    engine = sa.create_engine("sqlite://", poolclass=StaticPool)
    with TestClient(create_app(FakeValkey(), "unused", postgres_engine=engine)) as client:
        response = client.get(path)

    assert (response.status_code, response.json()) == (503, {"detail": "theme data unavailable"})


def test_missing_engine_returns_503():
    with TestClient(create_app(FakeValkey(), "unused")) as client:
        assert client.get("/themes").status_code == 503


def test_openapi_documents_ordering_pagination_and_snapshot_boundary(client):
    paths = client.get("/openapi.json").json()["paths"]

    assert set(paths) >= {"/themes", "/themes/{code}/stocks", "/stocks/{code}/themes"}
    assert "/themes/{code}" not in paths
    for path in ("/themes", "/themes/{code}/stocks", "/stocks/{code}/themes"):
        operation = paths[path]["get"]
        assert "ascending" in operation["description"]
        assert "point-in-time" in operation["description"]
        assert operation["responses"]["200"]["content"]["application/json"]["schema"]["type"] == (
            "array"
        )
    stocks = paths["/themes/{code}/stocks"]["get"]
    assert {parameter["name"] for parameter in stocks["parameters"]} == {"code", "limit", "offset"}
    assert "no next page" in stocks["description"]
    assert {
        parameter["name"] for parameter in paths["/themes"]["get"].get("parameters", [])
    } == set()
