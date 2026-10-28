import pytest
import sqlalchemy as sa

URL = "/stocks/005930/disclosures/financials"

ACCOUNTS = [
    ("CFS", "BS", "ifrs-full_Assets", "자산총계", "1000.00", "1,000", True),
    ("OFS", "BS", "ifrs-full_Assets", "자산총계", "900.00", "900", True),
    ("CFS", "IS", "ifrs-full_Revenue", "매출액", None, "해당사항 없음", False),
]


@pytest.fixture
def facts(engine):
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO corporations(stock_code,corp_code,name) VALUES "
                "('005930','0005930','회사') ON CONFLICT DO NOTHING"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO disclosures(receipt_id,stock_code,corp_code,company_name,title,"
                "business_year,kind,period_end,published_date,raw_payload,status) VALUES "
                "('fin-1','005930','0005930','회사','사업보고서',2025,'annual','2025-12-31',"
                "'2026-03-01','{}','ready')"
            )
        )
        for ordinal, (scope, statement, account, name, value, source, ok) in enumerate(ACCOUNTS):
            conn.execute(
                sa.text(
                    "INSERT INTO disclosure_financial_facts(receipt_id,stock_code,business_year,"
                    "kind,period_end,scope,statement_type,account_id,account_name,currency,"
                    "ordinal,source_value,value,normalized) VALUES "
                    "('fin-1','005930',2025,'annual','2025-12-31',:scope,:statement,:account,"
                    ":name,'KRW',:ordinal,:source,CAST(:value AS numeric),:ok)"
                ),
                {
                    "scope": scope,
                    "statement": statement,
                    "account": account,
                    "name": name,
                    "ordinal": ordinal,
                    "source": source,
                    "value": value,
                    "ok": ok,
                },
            )
    return engine


def test_financials_require_an_explicit_period(client, facts):
    assert client.get(URL).status_code == 422


def test_financials_are_readable_by_business_year(client, facts):
    body = client.get(URL, params={"business_year": 2025}).json()

    assert {fact["scope"] for fact in body["facts"]} == {"CFS", "OFS"}
    assert all(fact["receipt_id"] == "fin-1" for fact in body["facts"])
    assert all(fact["period_end"] == "2025-12-31" for fact in body["facts"])


def test_financials_never_merge_scopes(client, facts):
    body = client.get(URL, params={"business_year": 2025, "account_id": "ifrs-full_Assets"}).json()

    pairs = sorted((fact["scope"], fact["value"]) for fact in body["facts"])
    assert pairs == [("CFS", "1000.00"), ("OFS", "900.00")]


def test_a_requested_scope_excludes_the_other(client, facts):
    body = client.get(URL, params={"period_end": "2025-12-31", "scope": "OFS"}).json()

    assert [fact["scope"] for fact in body["facts"]] == ["OFS"]


def test_an_unparseable_amount_is_served_as_its_source_string(client, facts):
    body = client.get(URL, params={"business_year": 2025, "statement_type": "IS"}).json()

    (fact,) = body["facts"]
    assert fact["value"] is None
    assert fact["source_value"] == "해당사항 없음"
    assert fact["normalized"] is False


def test_financials_of_another_company_are_not_returned(client, facts):
    body = client.get(
        "/stocks/000660/disclosures/financials", params={"business_year": 2025}
    ).json()

    assert body == {"facts": []}


def test_a_financial_read_makes_no_embedding_call(client, facts, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("a direct financial read must not call the embedding server")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    assert client.get(URL, params={"business_year": 2025}).status_code == 200
