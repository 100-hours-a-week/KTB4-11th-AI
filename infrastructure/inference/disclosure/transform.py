"""The evaluated output transform for disclosure embeddings.

`tanh -> x127 -> round -> clip -> first N dimensions -> L2 normalize`, in that order.
This is an output transform, not weight quantization: the model runs in bf16 and only its
output is quantized, so changing anything here leaves the vector space the evaluation
measured.

The checkpoint's own `encode()` already performs `quantize`, so the serving path calls the
model for that half and `truncate_and_normalize` for the rest. `int8_embedding` composes
both and exists so the whole contract is stated and testable in one place, on CPU.

Bounds follow the checkpoint's reference implementation, which clips to [-128, 127]. tanh
cannot reach -128 after scaling, so the lower bound never binds; it is kept identical
rather than tidied, because a difference here is a difference in every stored vector.
"""

import math

INT8_SCALE = 127
INT8_MIN = -128
INT8_MAX = 127


def quantize(raw: list[float]) -> list[float]:
    """tanh, scale to INT8, round, clip. Mirrors the checkpoint's `encode`."""
    if not all(math.isfinite(value) for value in raw):
        raise ValueError("embedding output is not finite")
    return [
        float(min(INT8_MAX, max(INT8_MIN, round(math.tanh(value) * INT8_SCALE)))) for value in raw
    ]


def truncate_and_normalize(quantized: list[float], dimensions: int) -> list[float]:
    """Keep the leading `dimensions` values, then make the result unit length.

    Truncating before normalising is deliberate: the checkpoint emits 2048 dimensions and
    the index stores the first 1024, so normalising the full width first would scale every
    stored vector by a factor the evaluation never applied.
    """
    if len(quantized) < dimensions:
        raise ValueError(
            f"embedding output has {len(quantized)} values, fewer than the contracted {dimensions}"
        )
    head = quantized[:dimensions]
    if not all(math.isfinite(value) for value in head):
        raise ValueError("embedding output is not finite")
    norm = math.hypot(*head)
    if not norm or not math.isfinite(norm):
        raise ValueError("embedding output has an invalid norm")
    return [value / norm for value in head]


def int8_embedding(raw: list[float], dimensions: int) -> list[float]:
    """The whole transform, from the model's raw projection to a stored vector."""
    if len(raw) < dimensions:
        raise ValueError(
            f"embedding output has {len(raw)} values, fewer than the contracted {dimensions}"
        )
    return truncate_and_normalize(quantize(raw), dimensions)
