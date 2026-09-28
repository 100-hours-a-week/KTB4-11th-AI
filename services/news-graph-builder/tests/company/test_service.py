import pytest
import sqlalchemy as sa
from news_graph_builder.company import DartCompany, sync_companies

SAMSUNG = DartCompany("00126380", "삼성전자", "SAMSUNG ELECTRONICS CO,.LTD", "005930")
HYNIX = DartCompany("00164779", "SK하이닉스", "SK hynix Inc.", "000660")
KOSDAQ = DartCompany("00111111", "코스닥기업", None, "111111")
KOSPI = [("005930", "삼성전자"), ("005935", "삼성전자우"), ("000660", "SK하이닉스")]


def sync(engine, kospi=KOSPI, dart=(SAMSUNG, HYNIX, KOSDAQ)):
    with engine.begin() as conn:
        return sync_companies(conn, kospi, dart)


def rows(engine, sql: str, **params):
    with engine.connect() as conn:
        return conn.execute(sa.text(sql), params).all()


def test_joins_kospi_codes_to_dart_and_seeds_aliases(engine):
    assert sync(engine) == (2, 0)

    assert rows(engine, "SELECT corp_code, stock_code, corp_name FROM companies ORDER BY 1") == [
        ("00126380", "005930", "삼성전자"),
        ("00164779", "000660", "SK하이닉스"),
    ]
    assert dict(rows(engine, "SELECT alias, corp_code FROM company_aliases")) == {
        "삼성전자": "00126380",
        "samsungelectronicsco,.ltd": "00126380",
        "sk하이닉스": "00164779",
        "skhynixinc.": "00164779",
    }


def test_no_joined_rows_raises(engine):
    with pytest.raises(ValueError):
        sync(engine, kospi=[("A005930", "삼성전자")])


def test_a_shared_alias_keeps_its_first_owner(engine):
    sync(engine, kospi=[("005930", "공유이름"), ("000660", "공유이름")])

    assert rows(engine, "SELECT corp_code FROM company_aliases WHERE alias = '공유이름'") == [
        ("00126380",)
    ]


def test_a_second_sync_updates_names_without_duplicating(engine):
    sync(engine)
    (before,) = rows(engine, "SELECT synced_at FROM companies WHERE corp_code = '00126380'")

    sync(engine, dart=(SAMSUNG._replace(corp_name="삼성전자신"), HYNIX))

    assert rows(engine, "SELECT count(*) FROM companies") == [(2,)]
    (name, synced_at) = rows(
        engine, "SELECT corp_name, synced_at FROM companies WHERE corp_code = '00126380'"
    )[0]
    assert name == "삼성전자신"
    assert synced_at > before[0]
    assert rows(engine, "SELECT corp_code FROM company_aliases WHERE alias = '삼성전자'") == [
        ("00126380",)
    ]


def test_merges_plain_entities_into_a_newly_aliased_company(engine, article, cluster):
    sync(engine)
    with engine.begin() as conn:
        first = cluster(conn, [article(conn)])
        second = cluster(conn, [article(conn)])
        as_company, as_firm, nvidia = (
            conn.execute(
                sa.text(
                    "INSERT INTO entities (raw_name, name, type) VALUES (:raw, :name, :type)"
                    " RETURNING id"
                ),
                {"raw": raw, "name": name, "type": type_},
            ).scalar_one()
            for raw, name, type_ in [
                ("하이닉스", "하이닉스", "회사"),
                ("하이닉스", "하이닉스", "기업"),
                ("엔비디아", "엔비디아", "기업"),
            ]
        )
        for cluster_id, entity_id in [
            (first, as_company),
            (first, as_firm),
            (first, nvidia),
            (second, as_company),
        ]:
            conn.execute(
                sa.text("INSERT INTO cluster_entities VALUES (:c, :e)"),
                {"c": cluster_id, "e": entity_id},
            )
        conn.execute(
            sa.text(
                "INSERT INTO relations (cluster_id, source_entity_id, target_entity_id, type,"
                " description) VALUES (:c, :a, :n, '공급', ''), (:c, :n, :b, '구매', '')"
            ),
            {"c": first, "a": as_company, "b": as_firm, "n": nvidia},
        )
        conn.execute(sa.text("INSERT INTO company_aliases VALUES ('하이닉스', '00164779')"))

    assert sync(engine) == (2, 2)

    (company,) = rows(engine, "SELECT id FROM entities WHERE corp_code = '00164779'")[0]
    assert rows(engine, "SELECT id FROM entities WHERE corp_code IS NULL") == [(nvidia,)]
    assert set(rows(engine, "SELECT cluster_id, entity_id FROM cluster_entities")) == {
        (first, company),
        (first, nvidia),
        (second, company),
    }
    assert set(rows(engine, "SELECT source_entity_id, target_entity_id FROM relations")) == {
        (company, nvidia),
        (nvidia, company),
    }
    assert sync(engine) == (2, 0)
