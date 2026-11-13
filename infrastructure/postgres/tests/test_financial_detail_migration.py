from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config


def test_account_metadata_upgrade_and_downgrade_preserve_financial_rows(
    pg_dsn, pg_engine, monkeypatch
):
    monkeypatch.setenv("KTB_POSTGRES_DSN", pg_dsn)
    config = Config(str(Path(__file__).resolve().parents[3] / "alembic.ini"))
    command.downgrade(config, "0017")
    try:
        with pg_engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO corporations(stock_code,corp_code,name) "
                    "VALUES ('990008','00990008','financial migration')"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO disclosures(receipt_id,stock_code,corp_code,company_name,"
                    "title,business_year,kind,period_end,published_date,raw_payload,status) "
                    "VALUES ('metadata-test','990008','00990008','test','test',2025,"
                    "'annual','2025-12-31','2026-03-01','{}','ready')"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO disclosure_financial_facts(receipt_id,stock_code,"
                    "business_year,kind,period_end,scope,statement_type,account_id,account_name,"
                    "currency,ordinal,source_value,value,normalized) VALUES "
                    "('metadata-test','990008',2025,'annual','2025-12-31','CFS','SCE',"
                    "'equity','equity','USD',3,'100',100,true)"
                )
            )
        command.upgrade(config, "head")
        with pg_engine.connect() as conn:
            assert conn.execute(
                sa.text(
                    "SELECT source_value,account_detail,source_ordinal "
                    "FROM disclosure_financial_facts WHERE receipt_id='metadata-test'"
                )
            ).one() == ("100", None, None)
        command.downgrade(config, "0017")
        with pg_engine.connect() as conn:
            assert (
                conn.scalar(
                    sa.text(
                        "SELECT source_value FROM disclosure_financial_facts "
                        "WHERE receipt_id='metadata-test'"
                    )
                )
                == "100"
            )
    finally:
        command.upgrade(config, "head")
        with pg_engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM disclosures WHERE receipt_id='metadata-test'"))
            conn.execute(sa.text("DELETE FROM corporations WHERE stock_code='990008'"))
