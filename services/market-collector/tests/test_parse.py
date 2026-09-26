from datetime import UTC, datetime

import pytest
from market_collector.kiwoom.parse import (
    classify_session,
    parse_daily_bar,
    parse_daily_ts,
    parse_minute_bar,
    parse_minute_ts,
    parse_price,
    parse_volume,
)

MINUTE_ROW = {
    "cur_prc": "+277500",
    "trde_qty": "38961",
    "cntr_tm": "20260922151900",
    "open_pric": "+277750",
    "high_pric": "+278000",
    "low_pric": "+277500",
    "acc_trde_qty": "15620193",
    "pred_pre": "+3500",
    "pred_pre_sig": "2",
}
DAILY_ROW = {
    "cur_prc": "277500",
    "trde_qty": "15620240",
    "trde_prica": "4366136",
    "dt": "20260922",
    "open_pric": "283000",
    "high_pric": "283500",
    "low_pric": "274500",
    "pred_pre": "+3500",
    "pred_pre_sig": "2",
    "trde_tern_rt": "+0.27",
}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("+277500", 277500.0), ("-27550", 27550.0), ("277500", 277500.0), ("0", 0.0)],
)
def test_price_ignores_the_sign_prefix(raw, expected):
    assert parse_price(raw) == expected


def test_price_rejects_an_empty_field():
    with pytest.raises(ValueError):
        parse_price("")


def test_volume_parses_a_plain_integer():
    assert parse_volume("38961") == 38961


def test_minute_timestamp_converts_kst_to_utc():
    assert parse_minute_ts("20260922151900") == datetime(2026, 9, 22, 6, 19, tzinfo=UTC)


def test_daily_timestamp_is_utc_midnight_of_the_trading_date():
    assert parse_daily_ts("20260922") == datetime(2026, 9, 22, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("20260922090000", "regular"),
        ("20260922143000", "regular"),
        ("20260922153000", "regular"),
        ("20260922153100", "extended"),
        ("20260917170500", "extended"),
        ("20260922083500", "extended"),
    ],
)
def test_session_boundaries(raw, expected):
    assert classify_session(parse_minute_ts(raw)) == expected


def test_minute_bar_maps_cur_prc_to_close():
    bar = parse_minute_bar(MINUTE_ROW)

    assert bar.close == 277500.0
    assert bar.open == 277750.0
    assert bar.high == 278000.0
    assert bar.low == 277500.0
    assert bar.volume == 38961
    assert bar.ts == datetime(2026, 9, 22, 6, 19, tzinfo=UTC)
    assert bar.session == "regular"
    assert bar.trade_value is None


def test_daily_bar_carries_trade_value():
    bar = parse_daily_bar(DAILY_ROW)

    assert bar.close == 277500.0
    assert bar.open == 283000.0
    assert bar.volume == 15620240
    assert bar.trade_value == 4366136.0
    assert bar.ts == datetime(2026, 9, 22, 0, 0, tzinfo=UTC)
    assert bar.session == "regular"


def test_open_and_close_are_not_confused():
    bar = parse_minute_bar(MINUTE_ROW)

    assert bar.open != bar.close
