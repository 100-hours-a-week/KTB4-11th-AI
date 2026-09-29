import pytest
import sqlalchemy as sa
from market_syncer.kiwoom import Theme, ThemeMember
from market_syncer.themes import sync_themes

SAMSUNG = ("005930", "00126380", "삼성전자")
HYNIX = ("000660", "00164779", "SK하이닉스")


@pytest.fixture
def corporations(engine):
    with engine.begin() as conn:
        for stock_code, corp_code, name in (SAMSUNG, HYNIX):
            conn.execute(
                sa.text(
                    "INSERT INTO corporations (stock_code, corp_code, name)"
                    " VALUES (:stock_code, :corp_code, :name)"
                ),
                {"stock_code": stock_code, "corp_code": corp_code, "name": name},
            )


def sync(engine, themes, members):
    with engine.begin() as conn:
        return sync_themes(conn, themes=themes, members=members)


def memberships(query, engine):
    return query(
        engine, "SELECT theme_code, stock_code, is_main FROM theme_companies ORDER BY 1, 2"
    )


def test_keeps_every_member_known_as_a_corporation(engine, query, corporations):
    members = {
        "100": [
            ThemeMember("005930", "삼성전자"),
            ThemeMember("000660", "SK하이닉스"),
            ThemeMember("247540", "에코프로비엠"),
            ThemeMember("123456", "비상장"),
        ]
    }

    assert sync(engine, [Theme("100", "HBM", "")], members) == (2, 0, 2)

    assert memberships(query, engine) == [("100", "000660", False), ("100", "005930", False)]


def test_members_outside_kospi200_are_kept(engine, query, corporations):
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO corporation_indices (stock_code, index_name)"
                " VALUES ('005930', 'KOSPI200')"
            )
        )

    sync(engine, [Theme("100", "HBM", "")], {"100": [ThemeMember("000660", "SK하이닉스")]})

    assert memberships(query, engine) == [("100", "000660", False)]


@pytest.mark.parametrize(
    "main_stocks",
    ["SK하이닉스", "000660", "삼성전자, SK하이닉스", "삼성전자,000660", "SK 하이닉스"],
)
def test_main_stocks_match_by_code_or_normalized_name(engine, query, corporations, main_stocks):
    members = {"100": [ThemeMember("005930", "삼성전자"), ThemeMember("000660", "SK하이닉스")]}

    _, main, _ = sync(engine, [Theme("100", "HBM", main_stocks)], members)

    rows = {stock_code: is_main for _, stock_code, is_main in memberships(query, engine)}
    assert rows["000660"] is True
    assert rows["005930"] is ("삼성전자" in main_stocks)
    assert main == sum(rows.values())


def test_a_repeated_member_is_main_if_any_listing_is(engine, query, corporations):
    themes = [Theme("100", "HBM", ""), Theme("100", "HBM", "SK하이닉스")]
    members = {"100": [ThemeMember("000660", "SK하이닉스"), ThemeMember("000660", "하이닉스")]}

    assert sync(engine, themes, members) == (1, 1, 0)
    assert memberships(query, engine) == [("100", "000660", True)]


def test_themes_without_kept_members_are_still_stored(engine, query, corporations):
    sync(
        engine,
        [Theme("100", "HBM", ""), Theme("200", "2차전지", "")],
        {"100": [ThemeMember("005930", "삼성전자")], "200": []},
    )

    assert query(engine, "SELECT name FROM themes ORDER BY theme_code") == [("HBM",), ("2차전지",)]


def test_a_second_sync_replaces_everything(engine, query, corporations):
    sync(engine, [Theme("100", "HBM", "")], {"100": [ThemeMember("005930", "삼성전자")]})

    sync(engine, [Theme("200", "2차전지", "")], {"200": [ThemeMember("000660", "SK하이닉스")]})

    assert query(engine, "SELECT theme_code FROM themes") == [("200",)]
    assert memberships(query, engine) == [("200", "000660", False)]


@pytest.mark.parametrize(
    ("themes", "members"),
    [
        ([], {}),
        ([Theme("200", "2차전지", "")], {"200": [ThemeMember("999999", "없는회사")]}),
    ],
)
def test_empty_or_unknown_data_raises_and_keeps_the_old_tables(
    engine, query, corporations, themes, members
):
    sync(engine, [Theme("100", "HBM", "")], {"100": [ThemeMember("005930", "삼성전자")]})

    with pytest.raises(ValueError):
        sync(engine, themes, members)

    assert memberships(query, engine) == [("100", "005930", False)]
    assert query(engine, "SELECT theme_code FROM themes") == [("100",)]
