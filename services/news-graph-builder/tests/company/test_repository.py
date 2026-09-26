import sqlalchemy as sa
from news_graph_builder.company import has_companies


def test_has_companies(engine):
    with engine.connect() as conn:
        assert has_companies(conn) is False
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO companies (corp_code, stock_code, corp_name)"
                " VALUES ('00126380', '005930', '삼성전자')"
            )
        )

    with engine.connect() as conn:
        assert has_companies(conn) is True
