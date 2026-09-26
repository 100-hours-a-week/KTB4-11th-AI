import pytest
import sqlalchemy as sa
from news_graph_builder.theme import Theme, ThemeMember, sync_themes

SAMSUNG = ("00126380", "005930", "삼성전자")
HYNIX = ("00164779", "000660", "SK하이닉스")
KOSPI200 = {"005930", "000660"}


@pytest.fixture
def companies(engine):
    with engine.begin() as conn:
        for corp_code, stock_code, corp_name in (SAMSUNG, HYNIX):
            conn.execute(
                sa.text(
                    "INSERT INTO companies (corp_code, stock_code, corp_name)"
                    " VALUES (:corp_code, :stock_code, :corp_name)"
                ),
                {"corp_code": corp_code, "stock_code": stock_code, "corp_name": corp_name},
            )


def sync(engine, themes, members, kospi200=KOSPI200):
    with engine.begin() as conn:
        return sync_themes(conn, themes=themes, kospi200_codes=kospi200, members=members)


def memberships(engine):
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT theme_code, corp_code, is_main FROM theme_companies ORDER BY 1, 2")
        ).all()
    return [tuple(row) for row in rows]


def test_keeps_kospi200_members_known_as_companies(engine, companies):
    members = {
        "100": [
            ThemeMember("005930", "삼성전자"),
            ThemeMember("000660", "SK하이닉스"),
            ThemeMember("247540", "에코프로비엠"),
            ThemeMember("123456", "비상장"),
        ]
    }

    counts = sync(engine, [Theme("100", "HBM", "")], members, kospi200=KOSPI200 | {"123456"})

    assert counts == (2, 0, 2)
    assert memberships(engine) == [("100", "00126380", False), ("100", "00164779", False)]


@pytest.mark.parametrize(
    "main_stocks",
    ["SK하이닉스", "000660", "삼성전자, SK하이닉스", "삼성전자,000660", "SK 하이닉스"],
)
def test_main_stocks_match_by_code_or_normalized_name(engine, companies, main_stocks):
    members = {"100": [ThemeMember("005930", "삼성전자"), ThemeMember("000660", "SK하이닉스")]}

    kept, main, skipped = sync(engine, [Theme("100", "HBM", main_stocks)], members)

    rows = dict(((corp_code, is_main) for _, corp_code, is_main in memberships(engine)))
    assert rows["00164779"] is True
    assert rows["00126380"] is ("삼성전자" in main_stocks)
    assert main == sum(rows.values())


def test_themes_without_kospi200_members_are_still_stored(engine, companies):
    sync(engine, [Theme("100", "HBM", ""), Theme("200", "2차전지", "")], {"100": [], "200": []})

    with engine.connect() as conn:
        names = conn.execute(sa.text("SELECT name FROM themes ORDER BY theme_code")).scalars().all()
    assert names == ["HBM", "2차전지"]


def test_a_second_sync_replaces_everything(engine, companies):
    sync(engine, [Theme("100", "HBM", "")], {"100": [ThemeMember("005930", "삼성전자")]})

    sync(engine, [Theme("200", "2차전지", "")], {"200": [ThemeMember("000660", "SK하이닉스")]})

    with engine.connect() as conn:
        codes = conn.execute(sa.text("SELECT theme_code FROM themes")).scalars().all()
    assert codes == ["200"]
    assert memberships(engine) == [("200", "00164779", False)]


@pytest.mark.parametrize(
    ("themes", "kospi200"),
    [([], KOSPI200), ([Theme("100", "HBM", "")], set())],
)
def test_empty_kiwoom_data_raises_and_keeps_the_old_tables(engine, companies, themes, kospi200):
    sync(engine, [Theme("100", "HBM", "")], {"100": [ThemeMember("005930", "삼성전자")]})

    with pytest.raises(ValueError):
        sync(engine, themes, {}, kospi200=kospi200)

    assert memberships(engine) == [("100", "00126380", False)]


def test_a_shared_stock_code_resolves_to_the_most_recently_synced_company(engine, companies):
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO companies (corp_code, stock_code, corp_name, synced_at)"
                " VALUES ('00999999', '005930', '옛삼성', now() - interval '1 day')"
            )
        )

    sync(engine, [Theme("100", "HBM", "")], {"100": [ThemeMember("005930", "삼성전자")]})

    assert memberships(engine) == [("100", "00126380", False)]
