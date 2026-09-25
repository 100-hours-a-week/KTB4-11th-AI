import sqlalchemy as sa
from news_graph_builder.dart import DartCompany
from news_graph_builder.extract import Entity
from news_graph_builder.resolve import resolve
from news_graph_builder.sync_companies import sync_companies

SAMSUNG = DartCompany("00126380", "삼성전자", None, "005930")


def resolve_in(engine, *llm_entities):
    with engine.begin() as conn:
        return resolve(conn, llm_entities)


def test_an_alias_hit_ignores_the_llm_type(engine):
    with engine.begin() as conn:
        sync_companies(conn, [("005930", "삼성전자")], [SAMSUNG])

    first = resolve_in(engine, Entity("삼성전자(주)", "회사"))
    second = resolve_in(engine, Entity("삼성전자", "company"))

    assert first == second == {"삼성전자": first["삼성전자"]}
    with engine.connect() as conn:
        row = conn.execute(sa.text("SELECT corp_code, type FROM entities")).one()
    assert tuple(row) == ("00126380", "기업")


def test_a_miss_creates_one_entity_per_name_and_type(engine):
    bank = resolve_in(engine, Entity("한국은행", "기관"))["한국은행"]

    assert resolve_in(engine, Entity("한국 은행", "기관")) == {"한국은행": bank}
    assert resolve_in(engine, Entity("한국은행", "정부"))["한국은행"] != bank
    with engine.connect() as conn:
        raw = conn.execute(
            sa.text("SELECT raw_name FROM entities WHERE id = :id"), {"id": bank}
        ).scalar_one()
    assert raw == "한국은행"


def test_duplicate_and_empty_names_are_resolved_once(engine):
    resolved = resolve_in(
        engine, Entity("엔비디아", "기업"), Entity("엔비디아", "회사"), Entity("(주)", "기업")
    )

    assert list(resolved) == ["엔비디아"]
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM entities")).scalar_one() == 1
