import numpy as np
from ktb_market_analyzer.comments import signed

NAN = float("nan")


def _series(field, values):
    return signed.comments(field, np.array(values, dtype=np.float64))


def test_the_first_computable_value_gets_no_verdict():
    # A direction of travel needs two points. Naming only the side here would be a
    # differently shaped answer from every other verdict in this family.
    assert _series("macd", [2.0]) == [None]
    assert _series("macd", [-2.0]) == [None]


def test_crossing_the_line_is_reported_instead_of_the_magnitude_trend():
    assert _series("macd", [-1.0, 2.0]) == [None, "BULLISH_ZERO_CROSS"]
    assert _series("macd", [1.0, -2.0]) == [None, "BEARISH_ZERO_CROSS"]


def test_magnitude_growth_and_decay_on_the_same_side():
    assert _series("macd", [1.0, 2.0, 1.5]) == [
        None,
        "BULLISH_STRENGTHENING",
        "BULLISH_WEAKENING",
    ]
    assert _series("macd", [-1.0, -2.0, -1.5]) == [
        None,
        "BEARISH_STRENGTHENING",
        "BEARISH_WEAKENING",
    ]


def test_roc_shares_the_macd_line_vocabulary():
    assert _series("roc", [-1.0, 2.0, 3.0, 2.0]) == [
        None,
        "BULLISH_ZERO_CROSS",
        "BULLISH_STRENGTHENING",
        "BULLISH_WEAKENING",
    ]


def test_the_histogram_names_its_crossing_differently_from_the_macd_line():
    # Same numbers, different field: the event is MACD crossing its signal line,
    # not the 12- and 26-period averages crossing each other.
    assert _series("macd_histogram", [-1.0, 2.0, 3.0, 2.0]) == [
        None,
        "BULLISH_CROSSOVER",
        "BULLISH_EXPANDING",
        "BULLISH_CONTRACTING",
    ]


def test_holding_the_same_distance_is_its_own_verdict_not_an_absence_of_one():
    # Neither widening nor closing the gap is an observed state, distinct from
    # "no earlier value to compare against", which is None.
    assert _series("macd", [2.0, 2.0]) == [None, "BULLISH_STEADY"]
    assert _series("macd", [-2.0, -2.0]) == [None, "BEARISH_STEADY"]
    assert _series("macd_histogram", [2.0, 2.0]) == [None, "BULLISH_STEADY"]


def test_exact_zero_is_flat_and_leaving_it_is_a_crossing():
    # FLAT needs no earlier value: it states where the value is, not how it moved.
    assert _series("macd", [0.0, 1.0]) == ["FLAT", "BULLISH_ZERO_CROSS"]
    assert _series("macd", [1.0, 0.0]) == [None, "FLAT"]


def test_uncomputable_values_become_none_rather_than_a_verdict():
    assert _series("macd", [NAN, 1.0, 2.0]) == [None, None, "BULLISH_STRENGTHENING"]


def test_a_nan_does_not_become_the_previous_value_for_trend_purposes():
    # If the NaN were treated as a previous value, the 3.0 would compare against
    # it and the result would be neither a trend nor a crossing.
    assert _series("macd", [2.0, NAN, 3.0]) == [
        None,
        None,
        "BULLISH_STRENGTHENING",
    ]


def test_fields_matches_the_rule_table():
    assert signed.FIELDS == frozenset(signed.SIGNED)


def test_labels_for_lists_the_words_this_field_actually_uses():
    # The MACD line crosses zero while the histogram crosses its signal line, so
    # the two cannot share one label list.
    assert "BULLISH_ZERO_CROSS" in signed.labels_for("macd")
    assert "BULLISH_CROSSOVER" not in signed.labels_for("macd")
    assert "BULLISH_CROSSOVER" in signed.labels_for("macd_histogram")
    assert "BULLISH_ZERO_CROSS" not in signed.labels_for("macd_histogram")


def test_labels_for_covers_every_label_the_rule_can_emit():
    for field in signed.FIELDS:
        rule = signed.SIGNED[field]
        labels = set(signed.labels_for(field))
        for suffix in (rule.cross, rule.growing, rule.shrinking, rule.steady):
            assert f"BULLISH_{suffix}" in labels, field
            assert f"BEARISH_{suffix}" in labels, field
