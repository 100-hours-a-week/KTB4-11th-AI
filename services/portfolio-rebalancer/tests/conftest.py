import pytest
import sqlalchemy as sa

TABLES = "portfolio_reasons, portfolio_exits, portfolio_holdings, portfolios, corporations"


def _truncate(engine):
    with engine.begin() as conn:
        conn.execute(sa.text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))


@pytest.fixture
def engine(pg_engine):
    _truncate(pg_engine)
    with pg_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO corporations (stock_code, corp_code, name) VALUES"
                " ('005930', '00126380', '삼성전자'), ('000660', '00164779', 'SK하이닉스'),"
                " ('373220', '01515323', 'LG에너지솔루션')"
            )
        )
    yield pg_engine
    _truncate(pg_engine)


@pytest.fixture
def snapshot_state():
    from portfolio_rebalancer.snapshot_store import SnapshotStore, metadata, states

    database = sa.create_engine("sqlite://")
    metadata.create_all(database)
    with database.begin() as conn:
        conn.execute(states.insert().values(id=1, usable=False))
    yield SnapshotStore(database)
    database.dispose()
