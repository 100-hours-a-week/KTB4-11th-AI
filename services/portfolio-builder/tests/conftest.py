import pytest
import sqlalchemy as sa

TABLES = (
    "portfolio_exits, portfolio_holdings, portfolios, relations, cluster_entities, entities,"
    " cluster_summaries, article_clusters, clusters, articles, theme_companies, themes,"
    " corporation_indices, corporation_aliases, corporations"
)

SEED = [
    "INSERT INTO corporations (stock_code, corp_code, name) VALUES"
    " ('005930', '00126380', '삼성전자'), ('000660', '00164779', 'SK하이닉스'),"
    " ('373220', '01515323', 'LG에너지솔루션')",
    "INSERT INTO corporation_indices (stock_code, index_name) VALUES"
    " ('005930', 'KOSPI200'), ('000660', 'KOSPI200'), ('373220', 'KRX300')",
    "INSERT INTO corporation_aliases (alias, stock_code) VALUES"
    " ('삼성전자', '005930'), ('sk하이닉스', '000660'), ('lg에너지솔루션', '373220')",
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
    "INSERT INTO entities (id, raw_name, name, type, stock_code) OVERRIDING SYSTEM VALUE VALUES"
    " (1, '삼성전자', '삼성전자', 'company', '005930'),"
    " (2, '엔비디아', '엔비디아', 'company', NULL),"
    " (3, 'SK하이닉스', 'sk하이닉스', 'company', '000660'),"
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
    "INSERT INTO theme_companies (theme_code, stock_code, is_major) VALUES"
    " ('T1', '005930', true), ('T1', '000660', false)",
]


def _truncate(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(sa.text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
def engine(pg_engine):
    _truncate(pg_engine)
    with pg_engine.begin() as conn:
        for statement in SEED:
            conn.execute(sa.text(statement))
    yield pg_engine
    _truncate(pg_engine)
