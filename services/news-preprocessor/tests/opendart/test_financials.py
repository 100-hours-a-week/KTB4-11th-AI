from decimal import Decimal

import pytest
from news_preprocessor.sources.publishers.opendart.financials import financial_rows

ASSETS = {
    "sj_div": "BS",
    "account_id": "ifrs-full_Assets",
    "account_nm": "자산총계",
    "currency": "KRW",
    "ord": "1",
    "thstrm_amount": "514,531,948,000,000",
}


def _payload(**overrides):
    return [{**ASSETS, **overrides}]


def test_a_thousands_separated_amount_normalises():
    (row,) = financial_rows(_payload(), "CFS")

    assert row["value"] == Decimal("514531948000000")
    assert row["source_value"] == "514,531,948,000,000"
    assert row["normalized"] is True


@pytest.mark.parametrize("source", ["(1,234)", "-1,234"])
def test_a_negative_amount_normalises(source):
    (row,) = financial_rows(_payload(thstrm_amount=source), "CFS")

    assert row["value"] == Decimal("-1234")
    assert row["normalized"] is True


@pytest.mark.parametrize("source", ["", "-"])
def test_a_missing_amount_is_null_not_zero(source):
    (row,) = financial_rows(_payload(thstrm_amount=source), "CFS")

    assert row["value"] is None
    assert row["normalized"] is True, "DART writes an empty field or a dash for nil"


def test_an_unparseable_amount_keeps_its_source_string():
    (row,) = financial_rows(_payload(thstrm_amount="해당사항 없음"), "CFS")

    assert row["source_value"] == "해당사항 없음"
    assert row["value"] is None
    assert row["normalized"] is False


def test_rows_carry_the_scope_statement_and_account_identity():
    (row,) = financial_rows(_payload(), "OFS")

    assert row["scope"] == "OFS"
    assert row["statement_type"] == "BS"
    assert row["account_id"] == "ifrs-full_Assets"
    assert row["account_name"] == "자산총계"
    assert row["currency"] == "KRW"
    assert row["ordinal"] == 0
    assert row["source_ordinal"] == "1"


def test_an_entry_without_a_statement_type_is_skipped():
    assert financial_rows(_payload(sj_div=""), "CFS") == []


def test_a_non_numeric_ordinal_falls_back_to_position():
    rows = financial_rows(
        [{**ASSETS, "ord": "x"}, {**ASSETS, "account_id": "other", "ord": "x"}], "CFS"
    )

    assert [row["ordinal"] for row in rows] == [0, 1]


def test_sce_repeated_account_ordinals_preserve_each_component():
    payload = [
        {**ASSETS, "sj_div": "SCE", "ord": "3", "account_detail": detail, "thstrm_amount": amount}
        for detail, amount in (("자본금", "100"), ("이익잉여금", "200"), ("자본 합계", "300"))
    ]
    rows = financial_rows(payload, "CFS")
    assert [row["ordinal"] for row in rows] == [0, 1, 2]
    assert [row["source_ordinal"] for row in rows] == ["3", "3", "3"]
    assert [row["account_detail"] for row in rows] == ["자본금", "이익잉여금", "자본 합계"]
    assert [row["value"] for row in rows] == [Decimal("100"), Decimal("200"), Decimal("300")]


def test_missing_and_repeated_ordinals_never_collide_with_response_positions():
    rows = financial_rows(
        [
            {**ASSETS, "ord": "2"},
            {**ASSETS, "ord": ""},
            {**ASSETS, "ord": "2"},
            {**ASSETS, "ord": 2},
        ],
        "OFS",
    )
    assert [row["ordinal"] for row in rows] == [0, 1, 2, 3]
    assert [row["source_ordinal"] for row in rows] == ["2", "", "2", "2"]
    assert all(row["account_detail"] is None for row in rows)
