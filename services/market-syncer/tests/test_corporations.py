import pytest
from market_syncer.corporations import has_corporations, replace_index, sync_corporations
from market_syncer.dart import DartCorporation

SAMSUNG = DartCorporation("00126380", "삼성전자", "SAMSUNG ELECTRONICS CO,.LTD", "005930")
HYNIX = DartCorporation("00164779", "SK하이닉스", "SK hynix Inc.", "000660")
KOSDAQ = DartCorporation("00111111", "코스닥기업", None, "111111")
KOSPI = [("005930", "삼성전자"), ("005935", "삼성전자우"), ("000660", "SK하이닉스")]


def sync(engine, kospi=KOSPI, dart=(SAMSUNG, HYNIX, KOSDAQ)):
    with engine.begin() as conn:
        return sync_corporations(conn, kospi, dart)


def replace(engine, codes, index_name="KOSPI200"):
    with engine.begin() as conn:
        return replace_index(conn, index_name, codes)


def members(query, engine):
    return query(engine, "SELECT stock_code, index_name FROM corporation_indices ORDER BY 1, 2")


def test_joins_kospi_codes_to_dart_and_seeds_aliases(engine, query):
    assert sync(engine) == 2

    assert query(
        engine, "SELECT stock_code, corp_code, name, eng_name, market FROM corporations ORDER BY 1"
    ) == [
        ("000660", "00164779", "SK하이닉스", "SK hynix Inc.", "KOSPI"),
        ("005930", "00126380", "삼성전자", "SAMSUNG ELECTRONICS CO,.LTD", "KOSPI"),
    ]
    assert dict(query(engine, "SELECT alias, stock_code FROM corporation_aliases")) == {
        "삼성전자": "005930",
        "samsungelectronicsco,.ltd": "005930",
        "sk하이닉스": "000660",
        "skhynixinc.": "000660",
    }


def test_no_joined_rows_raises(engine, query):
    with pytest.raises(ValueError):
        sync(engine, kospi=[("A005930", "삼성전자")])

    assert query(engine, "SELECT count(*) FROM corporations") == [(0,)]


def test_a_shared_alias_keeps_its_first_owner(engine, query):
    sync(engine, kospi=[("005930", "공유이름"), ("000660", "공유이름")])

    assert query(engine, "SELECT stock_code FROM corporation_aliases WHERE alias = '공유이름'") == [
        ("005930",)
    ]


def test_a_second_sync_updates_names_without_duplicating(engine, query):
    sync(engine)
    [(before,)] = query(engine, "SELECT synced_at FROM corporations WHERE stock_code = '005930'")

    sync(engine, dart=(SAMSUNG._replace(corp_name="삼성전자신", corp_eng_name=None), HYNIX))

    assert query(engine, "SELECT count(*) FROM corporations") == [(2,)]
    [(name, eng_name, synced_at)] = query(
        engine, "SELECT name, eng_name, synced_at FROM corporations WHERE stock_code = '005930'"
    )
    assert (name, eng_name) == ("삼성전자신", None)
    assert synced_at > before
    assert query(engine, "SELECT stock_code FROM corporation_aliases WHERE alias = '삼성전자'") == [
        ("005930",)
    ]


def test_has_corporations(engine):
    with engine.connect() as conn:
        assert has_corporations(conn) is False
    sync(engine)
    with engine.connect() as conn:
        assert has_corporations(conn) is True


def test_replace_index_skips_and_counts_codes_outside_corporations(engine, query):
    sync(engine)

    assert replace(engine, {"005930", "000660", "999999"}) == (2, 1)

    assert members(query, engine) == [("000660", "KOSPI200"), ("005930", "KOSPI200")]


def test_replace_index_replaces_only_its_own_rows(engine, query):
    sync(engine)
    replace(engine, {"005930", "000660"})
    replace(engine, {"000660"}, index_name="OTHER")

    assert replace(engine, {"005930"}) == (1, 0)

    assert members(query, engine) == [("000660", "OTHER"), ("005930", "KOSPI200")]


@pytest.mark.parametrize("codes", [set(), {"999999"}])
def test_an_empty_or_unknown_fetch_raises_and_keeps_the_old_rows(engine, query, codes):
    sync(engine)
    replace(engine, {"005930"})

    with pytest.raises(ValueError):
        replace(engine, codes)

    assert members(query, engine) == [("005930", "KOSPI200")]


def test_replace_index_commits_nothing_on_its_own(engine, query):
    sync(engine)
    replace(engine, {"005930"})

    with pytest.raises(RuntimeError), engine.begin() as conn:
        replace_index(conn, "KOSPI200", {"000660"})
        raise RuntimeError("a later statement failed")

    assert members(query, engine) == [("005930", "KOSPI200")]
