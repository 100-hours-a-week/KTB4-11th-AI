import pytest
import sqlalchemy as sa
from market_streamer.symbols import load_codes


@pytest.fixture
def dsn(tmp_path):
    url = f"sqlite:///{tmp_path / 'ktb.sqlite'}"
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE corporation_indices (stock_code TEXT, index_name TEXT)"))
        conn.execute(
            sa.text("INSERT INTO corporation_indices VALUES (:code, :index)"),
            [
                {"code": "005930", "index": "KOSPI200"},
                {"code": "000660", "index": "KOSPI200"},
                {"code": "999999", "index": "KRX300"},
            ],
        )
    engine.dispose()
    return url


def test_load_codes_reads_one_index_in_code_order(dsn):
    assert load_codes(dsn, "KOSPI200") == ["000660", "005930"]


def test_load_codes_fails_when_the_index_is_empty(dsn):
    with pytest.raises(RuntimeError, match="run market-syncer"):
        load_codes(dsn, "KOSDAQ150")
