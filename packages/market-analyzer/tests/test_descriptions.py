from ktb_market_analyzer import DESCRIPTIONS

EXPECTED_FIELDS = {
    "rsi",
    "macd",
    "macd_signal",
    "macd_histogram",
    "stochastic_k",
    "stochastic_d",
    "roc",
    "williams_r",
}


def test_covers_every_indicator_output_field():
    assert set(DESCRIPTIONS) == EXPECTED_FIELDS


def test_every_description_is_a_single_line_under_160_chars():
    for text in DESCRIPTIONS.values():
        assert "\n" not in text
        assert 0 < len(text) <= 160
