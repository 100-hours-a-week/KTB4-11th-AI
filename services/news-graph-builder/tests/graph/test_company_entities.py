import sqlalchemy as sa
from news_graph_builder.graph.company_entities import has_corporations, merge_company_entities


def rows(engine, sql: str):
    with engine.connect() as conn:
        return conn.execute(sa.text(sql)).all()


def merge(engine) -> int:
    with engine.begin() as conn:
        return merge_company_entities(conn)


def test_has_corporations(engine, corporation):
    with engine.connect() as conn:
        assert has_corporations(conn) is False
    with engine.begin() as conn:
        corporation(conn, "005930", "삼성전자", "00126380")

    with engine.connect() as conn:
        assert has_corporations(conn) is True


def test_merges_plain_entities_into_a_newly_aliased_company(engine, article, cluster, corporation):
    with engine.begin() as conn:
        corporation(conn, "000660", "SK하이닉스", "00164779")
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

    assert merge(engine) == 0

    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO corporation_aliases VALUES ('하이닉스', '000660')"))

    assert merge(engine) == 2

    ((company, raw_name, name, type_),) = rows(
        engine, "SELECT id, raw_name, name, type FROM entities WHERE stock_code = '000660'"
    )
    assert (raw_name, name, type_) == ("SK하이닉스", "sk하이닉스", "기업")
    assert rows(engine, "SELECT id FROM entities WHERE stock_code IS NULL") == [(nvidia,)]
    assert set(rows(engine, "SELECT cluster_id, entity_id FROM cluster_entities")) == {
        (first, company),
        (first, nvidia),
        (second, company),
    }
    assert set(rows(engine, "SELECT source_entity_id, target_entity_id FROM relations")) == {
        (company, nvidia),
        (nvidia, company),
    }
    assert merge(engine) == 0
