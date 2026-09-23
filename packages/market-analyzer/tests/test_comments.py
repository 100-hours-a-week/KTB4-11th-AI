import itertools

import numpy as np
import pytest
from ktb_market_analyzer import COMMENT_MEANINGS, COMMENTED_FIELDS, comment_series
from ktb_market_analyzer.comments import _BANDS, _SIGNED

NAN = float("nan")


def _series(field, values):
    return comment_series(field, np.array(values, dtype=np.float64))


def test_macd_signal_has_no_rule_because_the_other_two_fields_say_it_all():
    assert "macd_signal" not in COMMENTED_FIELDS

    with pytest.raises(KeyError, match="macd_signal"):
        _series("macd_signal", [1.0])


def test_an_unknown_field_raises_rather_than_returning_a_default():
    with pytest.raises(KeyError, match="cci"):
        _series("cci", [1.0])


# ── banded rules ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("rsi", 69.9, "NEUTRAL"),
        ("rsi", 70.0, "OVERBOUGHT"),
        ("rsi", 70.1, "OVERBOUGHT"),
        ("rsi", 30.1, "NEUTRAL"),
        ("rsi", 30.0, "OVERSOLD"),
        ("rsi", 29.9, "OVERSOLD"),
        ("stochastic_k", 79.9, "NEUTRAL"),
        ("stochastic_k", 80.0, "OVERBOUGHT"),
        ("stochastic_k", 20.0, "OVERSOLD"),
        ("stochastic_d", 80.0, "OVERBOUGHT"),
        ("stochastic_d", 20.0, "OVERSOLD"),
        ("williams_r", -19.9, "OVERBOUGHT"),
        ("williams_r", -20.0, "OVERBOUGHT"),
        ("williams_r", -20.1, "NEUTRAL"),
        ("williams_r", -79.9, "NEUTRAL"),
        ("williams_r", -80.0, "OVERSOLD"),
        ("williams_r", -80.1, "OVERSOLD"),
    ],
)
def test_band_boundaries_are_inclusive_on_the_extreme_side(field, value, expected):
    assert _series(field, [value]) == [expected]


def test_williams_r_bands_are_negative_and_not_mirrored_by_accident():
    # Williams %R runs -100..0, so its "overbought" end is the arithmetically
    # larger one. A copy of the RSI rule with the signs left alone would invert
    # both verdicts without failing any single-value check.
    assert _series("williams_r", [-5.0, -95.0]) == ["OVERBOUGHT", "OVERSOLD"]


# ── signed rules ────────────────────────────────────────────────────────────


def test_the_first_computable_value_gets_no_verdict():
    # A direction of travel needs two points. Naming only the side here would be a
    # differently shaped answer from every other verdict in this family.
    assert _series("macd", [2.0]) == [None]
    assert _series("macd", [-2.0]) == [None]


def test_crossing_zero_is_reported_instead_of_the_magnitude_trend():
    assert _series("macd", [-1.0, 2.0]) == [None, "BULLISH_ZERO_CROSS"]
    assert _series("macd", [1.0, -2.0]) == [None, "BEARISH_ZERO_CROSS"]


def test_magnitude_growth_and_decay_on_the_same_side_of_zero():
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


# ── warm-up ─────────────────────────────────────────────────────────────────


def test_uncomputable_values_become_none_rather_than_a_verdict():
    assert _series("rsi", [NAN, NAN, 75.0]) == [None, None, "OVERBOUGHT"]
    # Banded fields need no earlier value, so the first computable one is judged;
    # signed fields need one, so it is not.
    assert _series("macd", [NAN, 1.0, 2.0]) == [None, None, "BULLISH_STRENGTHENING"]


def test_a_nan_does_not_become_the_previous_value_for_trend_purposes():
    # If the NaN were treated as a previous value, the 3.0 would compare against
    # it and the result would be neither a trend nor a crossing.
    assert _series("macd", [2.0, NAN, 3.0]) == [
        None,
        None,
        "BULLISH_STRENGTHENING",
    ]


def test_every_comment_is_aligned_with_its_input():
    values = np.array([NAN, 10.0, 50.0, 90.0], dtype=np.float64)

    assert len(comment_series("stochastic_k", values)) == values.size


# ── the vocabulary and its glossary must not drift apart ────────────────────


def _every_emittable_label() -> set[str]:
    labels = {"OVERBOUGHT", "OVERSOLD", "NEUTRAL", "FLAT"}
    for rule in _SIGNED.values():
        for side, suffix in itertools.product(
            ("BULLISH", "BEARISH"),
            (rule.cross, rule.growing, rule.shrinking, rule.steady),
        ):
            labels.add(f"{side}_{suffix}")
    return labels


def test_the_glossary_covers_exactly_the_labels_the_rules_can_emit():
    assert set(COMMENT_MEANINGS) == _every_emittable_label()


def test_every_meaning_is_a_non_empty_single_line():
    for label, text in COMMENT_MEANINGS.items():
        assert "\n" not in text, label
        assert text.strip(), label


def test_commented_fields_are_exactly_the_fields_with_rules():
    assert COMMENTED_FIELDS == frozenset(_BANDS) | frozenset(_SIGNED)
