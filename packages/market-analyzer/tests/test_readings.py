"""The two calls a consumer makes, and the surface they are reached through."""

import ktb_market_analyzer as ma
import numpy as np
import pytest
from ktb_market_analyzer import Candles, Reading, get_basic_market_data, interpret
from ktb_market_analyzer.comments import COMMENTED_FIELDS
from ktb_market_analyzer.descriptions import DESCRIPTIONS


def _candles(n: int = 120) -> Candles:
    rng = np.random.default_rng(11)
    close = 70000 + np.cumsum(rng.normal(0, 500, n))
    return Candles(high=close + 200.0, low=close - 200.0, close=close)


def test_the_public_surface_is_exactly_the_two_calls_and_their_types():
    # Everything else moved behind them. A consumer needing whole series reaches
    # into the submodules deliberately, not by accident through this surface.
    assert set(ma.__all__) == {"Candles", "Reading", "get_basic_market_data", "interpret"}


def test_interpret_returns_all_four_parts_in_one_call():
    reading = interpret("rsi", _candles())

    assert isinstance(reading, Reading)
    assert reading.value is not None
    assert reading.comment is not None
    assert reading.comment_meaning is not None
    assert reading.description == DESCRIPTIONS["rsi"]


def test_the_meaning_explains_the_label_so_no_lookup_is_needed():
    from ktb_market_analyzer.comments import COMMENT_MEANINGS

    reading = interpret("rsi", _candles())
    assert reading.comment is not None

    assert reading.comment_meaning == COMMENT_MEANINGS[reading.comment]


def test_it_reads_the_newest_candle():
    candles = _candles()
    shorter = Candles(high=candles.high[:-1], low=candles.low[:-1], close=candles.close[:-1])

    assert interpret("rsi", candles).value != interpret("rsi", shorter).value


def test_every_described_field_can_be_interpreted():
    candles = _candles()

    for field in DESCRIPTIONS:
        reading = interpret(field, candles)
        assert reading.description == DESCRIPTIONS[field], field
        assert reading.value is not None, field


def test_a_field_with_no_verdict_rule_still_returns_its_value_and_description():
    reading = interpret("macd_signal", _candles())

    assert "macd_signal" not in COMMENTED_FIELDS
    assert reading.value is not None
    assert reading.comment is None
    assert reading.comment_meaning is None
    assert reading.description == DESCRIPTIONS["macd_signal"]


def test_too_little_data_yields_a_value_of_none_rather_than_raising():
    # TA-Lib cannot compute MACD from five candles; the description still applies.
    reading = interpret("macd", _candles(5))

    assert reading.value is None
    assert reading.comment is None
    assert reading.description == DESCRIPTIONS["macd"]


def test_an_empty_series_is_handled():
    empty = np.array([], dtype=np.float64)

    reading = interpret("rsi", Candles(high=empty, low=empty, close=empty))

    assert reading == Reading(None, None, None, DESCRIPTIONS["rsi"])


def test_an_unknown_indicator_names_the_ones_that_exist():
    with pytest.raises(KeyError, match="cci"):
        interpret("cci", _candles())

    with pytest.raises(KeyError, match="rsi"):
        # The message lists the known fields, so a caller can recover without
        # reading the source.
        get_basic_market_data("cci")


def test_basic_market_data_needs_no_price_data():
    text = get_basic_market_data("rsi")

    assert DESCRIPTIONS["rsi"] in text
    assert "OVERBOUGHT" in text
    assert "OVERSOLD" in text
    assert "NEUTRAL" in text


def test_basic_market_data_lists_only_the_labels_that_field_can_emit():
    macd_text = get_basic_market_data("macd")
    histogram_text = get_basic_market_data("macd_histogram")

    assert "ZERO_CROSS" in macd_text and "CROSSOVER" not in macd_text
    assert "CROSSOVER" in histogram_text and "ZERO_CROSS" not in histogram_text


def test_basic_market_data_says_where_to_look_for_a_field_with_no_verdict():
    text = get_basic_market_data("macd_signal")

    assert DESCRIPTIONS["macd_signal"] in text
    assert "macd_histogram" in text
    assert "OVERBOUGHT" not in text


def test_basic_market_data_covers_every_described_field():
    for field in DESCRIPTIONS:
        assert get_basic_market_data(field).startswith(f"{field}:"), field


def test_the_catalogue_needs_no_argument_and_names_every_field():
    # This is the discovery path: nothing else publishes a list of field names, so
    # a caller starts here and picks what to interpret.
    catalogue = get_basic_market_data()

    for field in DESCRIPTIONS:
        assert f"- {field}:" in catalogue, field
        assert DESCRIPTIONS[field] in catalogue, field


def test_the_catalogue_tells_the_caller_what_to_call_next():
    catalogue = get_basic_market_data()

    assert "interpret(" in catalogue
    assert "get_basic_market_data(field)" in catalogue


def test_the_catalogue_omits_the_verdict_vocabulary():
    # Four fields share the same three labels, so listing them per field would pad
    # the briefing; interpret() returns each verdict's meaning alongside it anyway.
    catalogue = get_basic_market_data()

    assert "OVERBOUGHT" not in catalogue
    assert "ZERO_CROSS" not in catalogue


def test_naming_a_field_still_gives_its_verdicts():
    assert "OVERBOUGHT" in get_basic_market_data("rsi")
