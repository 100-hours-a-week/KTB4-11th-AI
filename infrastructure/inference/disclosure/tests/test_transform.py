import math

import pytest
from disclosure_inference.transform import (
    INT8_MAX,
    INT8_MIN,
    int8_embedding,
    quantize,
    truncate_and_normalize,
)


def test_the_transform_applies_the_evaluated_order():
    # tanh -> x127 -> round -> clip -> first N -> L2. Hand-computed for one value.
    raw = [0.1, -0.2, 0.3, 0.4]

    vector = int8_embedding(raw, dimensions=4)

    expected = [round(math.tanh(value) * 127) for value in raw]
    norm = math.hypot(*expected)
    assert vector == pytest.approx([value / norm for value in expected])


def test_the_transform_keeps_only_the_leading_dimensions():
    raw = [1.0] + [0.0] * 2047

    assert len(int8_embedding(raw, dimensions=1024)) == 1024


def test_the_transform_rejects_an_output_shorter_than_the_contract():
    with pytest.raises(ValueError, match="1024"):
        int8_embedding([0.1] * 512, dimensions=1024)


def test_the_result_is_unit_length():
    raw = [0.4, -0.9, 0.2, 0.7, -0.1, 0.5]

    assert math.hypot(*int8_embedding(raw, dimensions=6)) == pytest.approx(1.0)


def test_rounding_happens_before_normalisation():
    # Two inputs that differ below the INT8 rounding step must land on the same vector;
    # normalising first would keep them apart and leave the evaluation's space.
    first = int8_embedding([0.100000, 0.2, 0.3, 0.4], dimensions=4)
    second = int8_embedding([0.100001, 0.2, 0.3, 0.4], dimensions=4)

    assert first == second


def test_clipping_bounds_the_integer_range():
    # tanh saturates, so a large magnitude must land exactly on the INT8 bound.
    vector = int8_embedding([50.0, 0.0, 0.0, 0.0], dimensions=4)

    assert vector == pytest.approx([1.0, 0.0, 0.0, 0.0])


def test_quantize_matches_the_checkpoint_bounds():
    # The reference clips to [-128, 127]; tanh*127 never reaches -128, so the bound must
    # not be tightened to -127 on the chance that it one day would.
    assert quantize([-50.0, 50.0]) == [-127.0, 127.0]
    assert (INT8_MIN, INT8_MAX) == (-128, 127)


def test_truncating_happens_before_normalising():
    # The checkpoint emits 2048 dimensions and the index stores 1024. Normalising the full
    # width first would scale every stored vector by a factor the evaluation never used.
    quantized = [3.0, 4.0, 120.0, 120.0]

    assert truncate_and_normalize(quantized, 2) == pytest.approx([0.6, 0.8])


def test_the_serving_path_and_the_whole_transform_agree():
    # The app lets the model quantize and only truncates and normalises; that must equal
    # running the whole transform locally.
    raw = [0.3, -0.7, 0.9, 0.1, -0.4, 0.6]

    assert truncate_and_normalize(quantize(raw), 4) == int8_embedding(raw, 4)


def test_an_all_zero_output_is_rejected_rather_than_divided_by_zero():
    with pytest.raises(ValueError, match="norm"):
        int8_embedding([0.0] * 1024, dimensions=1024)


def test_a_non_finite_output_is_rejected():
    with pytest.raises(ValueError, match="finite"):
        int8_embedding([float("nan"), 0.2, 0.3, 0.4], dimensions=4)
