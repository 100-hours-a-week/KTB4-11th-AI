import numpy as np
import pytest
from ktb_market_analyzer.comments import banded

NAN = float("nan")


def _series(field, values):
    return banded.comments(field, np.array(values, dtype=np.float64))


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


def test_the_first_computable_value_is_judged_immediately():
    # This family needs no earlier value, unlike the signed one.
    assert _series("rsi", [NAN, NAN, 75.0]) == [None, None, "OVERBOUGHT"]


def test_every_field_in_this_family_has_both_bounds():
    for field, band in banded.BANDS.items():
        assert band.upper > band.lower, field


def test_fields_matches_the_rule_table():
    assert banded.FIELDS == frozenset(banded.BANDS)


def test_labels_for_is_the_same_three_for_every_banded_field():
    for field in banded.FIELDS:
        assert banded.labels_for(field) == ["OVERBOUGHT", "NEUTRAL", "OVERSOLD"]


def test_labels_for_rejects_a_field_from_another_family():
    with pytest.raises(KeyError):
        banded.labels_for("macd")
