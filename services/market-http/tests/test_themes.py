import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from market_http.app import create_app
from redis.exceptions import RedisError
from sqlalchemy.pool import StaticPool

from .test_market_subscription import FakeValkey

TRADE_VALUES = {"005930": 900.0, "000660": 500.0, "035420": 300.0, "373220": 0.0}


class Valkey(FakeValkey):
    def __init__(self, values=TRADE_VALUES, *, fail=False):
        super().__init__()
        self.values = values
        self.fail = fail

    async def exists(self, key):
        if self.fail:
            raise RedisError("down")
        return int(key.startswith("ranking:trade_value:"))

    async def zmscore(self, key, codes):
        return [self.values.get(code) for code in codes]


@pytest.fixture
def engine():
    engine = sa.create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE corporations (stock_code TEXT PRIMARY KEY, name TEXT)"))
        conn.execute(sa.text("CREATE TABLE corporation_indices (stock_code TEXT, index_name TEXT)"))
        conn.execute(
            sa.text("CREATE TABLE themes (theme_code TEXT PRIMARY KEY, name TEXT, synced_at TEXT)")
        )
        conn.execute(
            sa.text(
                "CREATE TABLE theme_corporations (theme_code TEXT, stock_code TEXT,"
                " is_major BOOLEAN, PRIMARY KEY (theme_code, stock_code))"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO corporations VALUES ('005930', '삼성전자'), ('000660', 'SK하이닉스'),"
                " ('035420', 'NAVER'), ('373220', 'LG에너지솔루션'), ('111111', '소형주'),"
                " ('222222', '무테마')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO corporation_indices VALUES ('005930', 'KOSPI200'),"
                " ('000660', 'KOSPI200'), ('035420', 'KOSPI200'), ('373220', 'OTHER')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO themes VALUES ('T005', '플랫폼', '2026-11-01'),"
                " ('T002', '인공지능', '2026-11-01'), ('T001', '반도체_생산', '2026-11-01'),"
                " ('T003', '빈테마', '2026-11-01'), ('T004', '반도체_장비', '2026-11-01'),"
                " ('T006', '소형주', '2026-11-01')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO theme_corporations VALUES ('T001', '373220', 0),"
                " ('T001', '005930', 0), ('T001', '111111', 1), ('T001', '000660', 1),"
                " ('T002', '035420', 0), ('T002', '005930', 1), ('T004', '005930', 0),"
                " ('T005', '035420', 0), ('T006', '111111', 1), ('T006', '373220', 0)"
            )
        )
    yield engine
    engine.dispose()


@pytest.fixture
def client(engine):
    with TestClient(create_app(Valkey(), "unused", postgres_engine=engine)) as client:
        yield client


def test_kospi200_themes_are_grouped_by_prefix_in_member_trade_value_order(client):
    response = client.get("/themes")

    assert response.status_code == 200
    assert response.json() == [
        {
            "name": "반도체",
            "items": [{"code": "T001", "name": "생산"}, {"code": "T004", "name": "장비"}],
        },
        {"code": "T002", "name": "인공지능"},
        {"code": "T005", "name": "플랫폼"},
    ]


def test_themes_ignores_search_and_pagination_parameters(client):
    assert client.get("/themes?limit=1&q=반도체").json() == client.get("/themes").json()


def test_theme_stocks_are_ordered_by_trade_value_with_missing_values_last(client):
    response = client.get("/themes/T001/stocks?limit=1&offset=1")

    assert response.status_code == 200
    assert response.json() == [
        {"code": "005930", "name": "삼성전자", "is_major": False},
        {"code": "000660", "name": "SK하이닉스", "is_major": True},
    ]


@pytest.mark.parametrize("valkey", [Valkey({}), Valkey(fail=True)], ids=["empty", "failing"])
def test_orders_fall_back_to_major_member_count_and_code_without_trade_values(engine, valkey):
    with TestClient(create_app(valkey, "unused", postgres_engine=engine)) as client:
        themes = [theme.get("code", theme["name"]) for theme in client.get("/themes").json()]
        stocks = [stock["code"] for stock in client.get("/themes/T001/stocks").json()]

    assert themes == ["반도체", "T002", "T005"]
    assert stocks == ["000660", "005930"]


def test_existing_identifiers_without_relations_return_empty_arrays(client):
    assert client.get("/themes/T003/stocks").json() == []
    assert client.get("/themes/T006/stocks").json() == []


def test_missing_theme_returns_404(client):
    theme = client.get("/themes/T999/stocks")

    assert (theme.status_code, theme.json()) == (404, {"detail": "theme not found"})


def test_theme_detail_route_is_not_provided(client):
    assert client.get("/themes/T001").status_code == 404


def test_invalid_theme_code_returns_422(client):
    assert client.get("/themes/T-01/stocks").status_code == 422


@pytest.mark.parametrize("path", ["/themes", "/themes/T001/stocks"])
def test_database_failures_return_503(path):
    engine = sa.create_engine("sqlite://", poolclass=StaticPool)
    with TestClient(create_app(Valkey(), "unused", postgres_engine=engine)) as client:
        response = client.get(path)

    assert (response.status_code, response.json()) == (503, {"detail": "theme data unavailable"})


def test_missing_engine_returns_503():
    with TestClient(create_app(FakeValkey(), "unused")) as client:
        assert client.get("/themes").status_code == 503


def test_openapi_documents_theme_routes(client):
    paths = client.get("/openapi.json").json()["paths"]

    assert set(paths) >= {"/themes", "/themes/{code}/stocks"}
    assert "/themes/{code}" not in paths
    assert "/stocks/{code}/themes" not in paths
    for path in ("/themes", "/themes/{code}/stocks"):
        operation = paths[path]["get"]
        assert operation["responses"]["200"]["content"]["application/json"]["schema"]["type"] == (
            "array"
        )
    stocks = paths["/themes/{code}/stocks"]["get"]
    assert {parameter["name"] for parameter in stocks["parameters"]} == {"code"}
    assert {
        parameter["name"] for parameter in paths["/themes"]["get"].get("parameters", [])
    } == set()
