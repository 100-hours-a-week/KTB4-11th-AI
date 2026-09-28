import pytest
import sqlalchemy as sa

TABLES = (
    "portfolio_exits, portfolio_holdings, portfolios, relations, cluster_entities, entities,"
    " cluster_summaries, article_clusters, clusters, articles, theme_companies, themes,"
    " company_aliases, companies"
)

SEED = [
    "INSERT INTO companies (corp_code, stock_code, corp_name) VALUES"
    " ('00126380', '005930', '삼성전자'), ('00164779', '000660', 'SK하이닉스'),"
    " ('01515323', '373220', 'LG에너지솔루션')",
    "INSERT INTO company_aliases (alias, corp_code) VALUES"
    " ('삼성전자', '00126380'), ('sk하이닉스', '00164779'), ('lg에너지솔루션', '01515323')",
    "INSERT INTO clusters (id, updated_at) OVERRIDING SYSTEM VALUE VALUES"
    " (1, now()), (2, now() - interval '30 days')",
    "INSERT INTO cluster_summaries (cluster_id, title, summary, cluster_updated_at) VALUES"
    " (1, '삼성전자 HBM 공급 확대', '삼성전자가 엔비디아에 HBM을 공급한다.', now()),"
    " (2, '반도체 수출 둔화', 'SK하이닉스가 HBM 생산을 늘린다.', now() - interval '30 days')",
    "INSERT INTO articles"
    " (id, source, external_id, url, title, body, published_at, raw_payload)"
    " OVERRIDING SYSTEM VALUE VALUES"
    " (1, 'yonhap', 'a1', 'https://example.com/1', '삼성 HBM 공급', '본문', now(), '{}'),"
    " (2, 'yonhap', 'a2', 'https://example.com/2', '엔비디아 HBM 조달', '본문', now(), '{}'),"
    " (3, 'yonhap', 'a3', 'https://example.com/3', '하이닉스 증산', '본문',"
    " now() - interval '30 days', '{}')",
    "INSERT INTO article_clusters (article_id, cluster_id) VALUES (1, 1), (2, 1), (3, 2)",
    "INSERT INTO entities (id, raw_name, name, type, corp_code) OVERRIDING SYSTEM VALUE VALUES"
    " (1, '삼성전자', '삼성전자', 'company', '00126380'),"
    " (2, '엔비디아', '엔비디아', 'company', NULL),"
    " (3, 'SK하이닉스', 'sk하이닉스', 'company', '00164779'),"
    " (4, 'HBM', 'hbm', 'product', NULL)",
    "INSERT INTO cluster_entities (cluster_id, entity_id) VALUES"
    " (1, 1), (1, 2), (1, 4), (2, 3), (2, 4)",
    "INSERT INTO relations"
    " (id, cluster_id, source_entity_id, target_entity_id, type, description)"
    " OVERRIDING SYSTEM VALUE VALUES"
    " (1, 1, 1, 2, 'supplies', '삼성전자가 엔비디아에 HBM을 공급'),"
    " (2, 1, 4, 2, 'used_by', 'HBM은 엔비디아 GPU에 쓰인다'),"
    " (3, 2, 3, 4, 'produces', 'SK하이닉스가 HBM을 생산')",
    "INSERT INTO themes (theme_code, name) VALUES ('T1', 'HBM')",
    "INSERT INTO theme_companies (theme_code, corp_code, is_main) VALUES"
    " ('T1', '00126380', true), ('T1', '00164779', false)",
]


def _truncate(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(sa.text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
def engine(pg_engine):
    # The code under test commits, so empty the tables instead of rolling back.
    _truncate(pg_engine)
    with pg_engine.begin() as conn:
        for statement in SEED:
            conn.execute(sa.text(statement))
    yield pg_engine
    _truncate(pg_engine)
