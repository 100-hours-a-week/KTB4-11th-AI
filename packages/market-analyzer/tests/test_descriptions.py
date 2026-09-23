from ktb_market_analyzer import COMMENT_MEANINGS, DESCRIPTIONS

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

VERDICT_WORDS = {"overbought", "oversold", "bullish", "bearish"}


def test_covers_every_indicator_output_field():
    assert set(DESCRIPTIONS) == EXPECTED_FIELDS


def test_every_description_is_a_single_line_under_160_chars():
    for text in DESCRIPTIONS.values():
        assert "\n" not in text
        assert 0 < len(text) <= 160


def test_descriptions_say_what_is_measured_not_what_it_means():
    # A description explains the measurement; the verdict on a particular value
    # belongs to COMMENT_MEANINGS. Without this test the two drift back together,
    # because "above 70 is typically overbought" reads like a helpful addition.
    for field, text in DESCRIPTIONS.items():
        words = set(text.lower().replace(",", " ").replace(".", " ").split())
        assert not (words & VERDICT_WORDS), f"{field} states a verdict: {text}"


def test_descriptions_carry_no_threshold_comparisons():
    for field, text in DESCRIPTIONS.items():
        assert ">" not in text, field
        assert "<" not in text, field


def test_descriptions_and_comment_meanings_are_keyed_differently():
    # One is keyed by output field, the other by verdict label. An overlap would
    # mean a field name had become a verdict or the reverse.
    assert not (set(DESCRIPTIONS) & set(COMMENT_MEANINGS))
