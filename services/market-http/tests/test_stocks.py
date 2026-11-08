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
        conn.execute(
            sa.text(
                "CREATE TABLE corporation_indices (stock_code TEXT, index_name TEXT,"
                " PRIMARY KEY (stock_code, index_name))"
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
                " ('222222', '00999999', '무테마', NULL, 'KOSDAQ', '',"
                " '2026-11-03T09:00:00+09:00'),"
                " ('066570', '00401731', 'LG전자', 'LG Electronics Inc.', 'KOSPI', '', ''),"
                " ('051910', '00356361', '엘지화학', 'LG Chem, Ltd.', 'KOSPI', '', ''),"
                " ('005935', '00126380', '삼성전자우', NULL, 'KOSPI', '', ''),"
                " ('000001', '00000001', '삼성', NULL, 'KOSPI', '', ''),"
                " ('000002', '00000002', '삼성비편입', NULL, 'KOSPI', '', ''),"
                " ('000003', '00000003', 'A.B전자', NULL, 'KOSPI', '', '')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO corporation_indices VALUES ('005930', 'KOSPI200'),"
                " ('066570', 'KOSPI200'), ('051910', 'KOSPI200'), ('005935', 'KOSPI200'),"
                " ('000001', 'KOSPI200'), ('000003', 'KOSPI200'), ('000002', 'KOSDAQ150')"
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

    assert (response.status_code, response.json()) == (503, {"detail": "data store unavailable"})


def test_openapi_documents_stock_detail_and_drops_stock_themes(client):
    paths = client.get("/openapi.json").json()["paths"]

    assert set(paths["/stocks/{code}"]["get"]["responses"]) >= {"200", "404", "422", "503"}
    assert "/stocks/{code}/themes" not in paths
    assert "/stocks" in paths
    assert "/stocks/search" not in paths
    assert client.get("/stocks/005930/themes").status_code == 404


def _search(client, q):
    response = client.get("/stocks", params={"q": q})
    assert response.status_code == 200
    return [stock["code"] for stock in response.json()]


@pytest.mark.parametrize(
    "q",
    [
        "삼성",
        "전자",
        "성전",
        "ㅅㅅ",
        "삼ㅅ",
        "삼서",
        "삼성저",
        "ㅅ성ㅈ자",
        "ㅅㅅㅈㅈ",
        "ㅈㅈ",
        "Samsung",
        "samsung",
        "Electronics",
        "  삼성  ",
    ],
)
def test_search_finds_samsung_electronics(client, q):
    assert "005930" in _search(client, q)


def test_search_returns_code_and_name_only(client):
    response = client.get("/stocks", params={"q": "samsung"})

    assert response.json() == [{"code": "005930", "name": "삼성전자"}]


def test_search_ranks_exact_prefix_contains_partial_then_english(client):
    assert _search(client, "삼성") == ["000001", "005930", "005935"]
    assert _search(client, "ㅅㅅ") == ["000001", "005930", "005935"]
    assert _search(client, "전자") == ["000003", "005930", "005935", "066570"]
    assert _search(client, "LG") == ["066570", "051910"]


def test_search_excludes_non_kospi200_stocks(client):
    assert "000002" not in _search(client, "삼성비")
    assert "222222" not in _search(client, "무테마")


def test_search_treats_regex_characters_literally(client):
    assert _search(client, "A.B") == ["000003"]
    assert _search(client, "A*B") == []
    assert _search(client, ".*") == []


def test_search_without_match_returns_empty_list(client):
    assert _search(client, "없는종목") == []


@pytest.mark.parametrize("q", ["", "   "])
def test_blank_search_returns_422(client, q):
    assert client.get("/stocks", params={"q": q}).status_code == 422


def test_search_size_limits_to_top_ranked_results(client):
    response = client.get("/stocks", params={"q": "전자", "size": 2})

    assert response.status_code == 200
    assert [stock["code"] for stock in response.json()] == ["000003", "005930"]


@pytest.mark.parametrize("size", [1, 4, 200])
def test_search_size_returns_prefix_of_full_results(client, size):
    full = _search(client, "전자")
    response = client.get("/stocks", params={"q": "전자", "size": size})

    assert [stock["code"] for stock in response.json()] == full[:size]


def test_search_size_without_match_returns_empty_list(client):
    response = client.get("/stocks", params={"q": "없는종목", "size": 10})

    assert (response.status_code, response.json()) == (200, [])


@pytest.mark.parametrize("size", ["0", "201", "-1", "1.5", "ten"])
def test_invalid_search_size_returns_422(client, size):
    assert client.get("/stocks", params={"q": "삼성", "size": size}).status_code == 422
