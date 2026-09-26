"""The dispatcher and the invariants that span rule families.

Each family's own rules are tested beside it, in test_comment_bands.py and
test_comment_signed.py. What is left here is what no single family can check.
"""

import itertools

import numpy as np
import pytest
from ktb_market_analyzer.comments import (
    _FAMILIES,
    COMMENT_MEANINGS,
    COMMENTED_FIELDS,
    banded,
    comment_series,
    signed,
)


def test_macd_signal_has_no_rule_because_the_other_two_fields_say_it_all():
    assert "macd_signal" not in COMMENTED_FIELDS

    with pytest.raises(KeyError, match="macd_signal"):
        comment_series("macd_signal", np.array([1.0]))


def test_an_unknown_field_raises_rather_than_returning_a_default():
    with pytest.raises(KeyError, match="cci"):
        comment_series("cci", np.array([1.0]))


def test_the_dispatcher_routes_each_field_to_its_own_family():
    banded_result = comment_series("rsi", np.array([75.0]))
    signed_result = comment_series("macd", np.array([1.0, 2.0]))

    assert banded_result == banded.comments("rsi", np.array([75.0]))
    assert signed_result == signed.comments("macd", np.array([1.0, 2.0]))


def test_every_comment_is_aligned_with_its_input():
    values = np.array([float("nan"), 10.0, 50.0, 90.0], dtype=np.float64)

    assert len(comment_series("stochastic_k", values)) == values.size


def test_commented_fields_is_the_union_of_every_family():
    assert COMMENTED_FIELDS == frozenset().union(*(f.FIELDS for f in _FAMILIES))


def test_no_two_families_claim_the_same_field():
    # Overlapping FIELDS would make the dispatcher's answer depend on the order
    # of _FAMILIES, which is not a contract anyone should rely on.
    seen: set[str] = set()
    for family in _FAMILIES:
        assert not (seen & family.FIELDS), family.__name__
        seen |= family.FIELDS


def test_no_two_families_define_the_same_label():
    # The merge in comments/__init__.py raises on a collision, so reaching this
    # assertion at all means the import succeeded; this pins the property for a
    # reader rather than leaving it implicit in an import-time side effect.
    counts = itertools.chain.from_iterable(f.MEANINGS for f in _FAMILIES)
    labels = list(counts)
    assert len(labels) == len(set(labels))


def _every_emittable_label() -> set[str]:
    labels = set(banded.MEANINGS) | {"FLAT"}
    for rule in signed.SIGNED.values():
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
