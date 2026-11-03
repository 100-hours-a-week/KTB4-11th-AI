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
                "CREATE TABLE theme_companies (theme_code TEXT, stock_code TEXT, is_major BOOLEAN,"
                " PRIMARY KEY (theme_code, stock_code))"
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
                " ('T003', '빈테마', '2026-11-01'), ('T004', '스마트폰', '2026-11-01'),"
                " ('T006', '반도체_후공정장비', '2026-11-01'),"
                " ('T007', '반도체_전공정장비', '2026-11-01'),"
                " ('T008', '자동차_전장', '2026-11-01')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO theme_companies VALUES ('T001', '373220', 0), ('T001', '005930', 0),"
                " ('T001', '111111', 1), ('T001', '000660', 0), ('T002', '035420', 0),"
                " ('T002', '005930', 1), ('T004', '005930', 0), ('T005', '035420', 0),"
                " ('T006', '000660', 1), ('T007', '111111', 0), ('T008', '035420', 0)"
            )
        )
    yield engine
    engine.dispose()


@pytest.fixture
def client(engine):
    with TestClient(create_app(Valkey(), "unused", postgres_engine=engine)) as client:
        yield client


def _flatten(groups):
    return [(item["code"], group["name"], item["name"]) for group in groups for item in group["items"]]


def test_themes_are_grouped_after_ranking_kospi200_members(client):
    response = client.get("/themes")
    assert response.status_code == 200
    assert response.json() == [
        {"name": "반도체", "items": [
            {"code": "T001", "name": "생산"},
            {"code": "T006", "name": "후공정장비"},
        ]},
        {"name": "기타", "items": [
            {"code": "T002", "name": "인공지능"},
            {"code": "T004", "name": "스마트폰"},
            {"code": "T005", "name": "플랫폼"},
        ]},
        {"name": "자동차", "items": [{"code": "T008", "name": "전장"}]},
    ]
    assert len(_flatten(response.json())) == 6


def test_themes_exclude_zero_members_but_keep_single_member(client):
    themes = {code for code, _, _ in _flatten(client.get("/themes").json())}
    assert {"T006", "T008"} <= themes
    assert {"T003", "T007"}.isdisjoint(themes)


def test_themes_ignores_search_and_pagination_parameters(client):
    assert client.get("/themes?limit=1&q=반도체").json() == client.get("/themes").json()


def test_theme_stocks_are_ordered_by_trade_value_and_kospi200_only(client):
    response = client.get("/themes/T001/stocks?limit=1&offset=1")
    assert response.status_code == 200
    assert response.json() == [
        {"code": "005930", "name": "삼성전자", "is_major": False},
        {"code": "000660", "name": "SK하이닉스", "is_major": False},
    ]


def test_stock_themes_are_ordered_by_kospi200_member_trade_value(client):
    assert client.get("/stocks/035420/themes").json() == [
        {"code": "T002", "name": "인공지능", "is_major": False},
        {"code": "T005", "name": "플랫폼", "is_major": False},
        {"code": "T008", "name": "자동차_전장", "is_major": False},
    ]


@pytest.mark.parametrize("valkey", [Valkey({}), Valkey(fail=True)], ids=["empty", "failing"])
def test_orders_fall_back_to_kospi200_member_count_and_code(engine, valkey):
    with TestClient(create_app(valkey, "unused", postgres_engine=engine)) as client:
        themes = _flatten(client.get("/themes").json())
        stocks = [stock["code"] for stock in client.get("/themes/T001/stocks").json()]
    assert themes == [
        ("T001", "반도체", "생산"), ("T006", "반도체", "후공정장비"),
        ("T002", "기타", "인공지능"), ("T004", "기타", "스마트폰"),
        ("T005", "기타", "플랫폼"), ("T008", "자동차", "전장"),
    ]
    assert stocks == ["000660", "005930"]


def test_relation_is_identical_in_both_directions(client):
    for group in client.get("/themes").json():
        for theme in group["items"]:
            for stock in client.get(f"/themes/{theme['code']}/stocks").json():
                reverse = client.get(f"/stocks/{stock['code']}/themes").json()
                original_name = (
                    f"{group['name']}_{theme['name']}"
                    if group["name"] != "기타" else theme["name"]
                )
                assert {
                    "code": theme["code"],
                    "name": original_name,
                    "is_major": stock["is_major"],
                } in reverse


def test_outside_kospi200_and_unrelated_identifiers_return_empty_arrays(client):
    assert client.get("/themes/T003/stocks").json() == []
    assert client.get("/themes/T007/stocks").json() == []
    assert client.get("/stocks/111111/themes").json() == []
    assert client.get("/stocks/373220/themes").json() == []
    assert client.get("/stocks/222222/themes").json() == []


def test_missing_identifiers_return_404(client):
    theme = client.get("/themes/T999/stocks")
    stock = client.get("/stocks/999999/themes")
    assert (theme.status_code, theme.json()) == (404, {"detail": "theme not found"})
    assert (stock.status_code, stock.json()) == (404, {"detail": "stock not found"})


def test_theme_detail_route_is_not_provided(client):
    assert client.get("/themes/T001").status_code == 404


@pytest.mark.parametrize(
    "path",
    ["/themes/T-01/stocks", "/stocks/00593/themes", "/stocks/005930A/themes"],
)
def test_invalid_parameters_return_422(client, path):
    assert client.get(path).status_code == 422


@pytest.mark.parametrize("path", ["/themes", "/themes/T001/stocks", "/stocks/005930/themes"])
def test_database_failures_return_503(path):
    engine = sa.create_engine("sqlite://", poolclass=StaticPool)
    with TestClient(create_app(Valkey(), "unused", postgres_engine=engine)) as client:
        response = client.get(path)
    assert (response.status_code, response.json()) == (503, {"detail": "theme data unavailable"})


def test_missing_engine_returns_503():
    with TestClient(create_app(FakeValkey(), "unused")) as client:
        assert client.get("/themes").status_code == 503


def test_openapi_documents_grouping_kospi200_and_snapshot_boundary(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert set(paths) >= {"/themes", "/themes/{code}/stocks", "/stocks/{code}/themes"}
    assert "/themes/{code}" not in paths
    for path in ("/themes", "/themes/{code}/stocks", "/stocks/{code}/themes"):
        operation = paths[path]["get"]
        assert "point-in-time" in operation["description"]
        assert operation["responses"]["200"]["content"]["application/json"]["schema"]["type"] == "array"
    assert "KOSPI 200" in paths["/themes"]["get"]["description"]
    assert "grouped" in paths["/themes"]["get"]["description"]
    assert "trade value descending" in paths["/themes/{code}/stocks"]["get"]["description"]
    assert {p["name"] for p in paths["/themes/{code}/stocks"]["get"]["parameters"]} == {"code"}
    assert {p["name"] for p in paths["/themes"]["get"].get("parameters", [])} == set()
