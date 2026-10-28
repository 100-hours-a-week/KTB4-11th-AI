"""The evaluated output transform for disclosure embeddings.

`tanh -> x127 -> round -> clip -> first N dimensions -> L2 normalize`, in that order. This
is an output transform, not weight quantization: the model runs in bf16 and only its output
is quantized, so changing anything here leaves the vector space the evaluation measured.

Pure Python and no GPU, so the contract is provable on CPU.
"""

import math

INT8_SCALE = 127
INT8_MIN = -127
INT8_MAX = 127


def int8_embedding(raw: list[float], dimensions: int) -> list[float]:
    if len(raw) < dimensions:
        raise ValueError(
            f"embedding output has {len(raw)} values, fewer than the contracted {dimensions}"
        )
    head = raw[:dimensions]
    if not all(math.isfinite(value) for value in head):
        raise ValueError("embedding output is not finite")
    # Rounding comes before normalisation: normalising first would preserve differences
    # smaller than the INT8 step and produce vectors the evaluation never saw.
    quantized = [
        float(min(INT8_MAX, max(INT8_MIN, round(math.tanh(value) * INT8_SCALE)))) for value in head
    ]
    norm = math.hypot(*quantized)
    if not norm or not math.isfinite(norm):
        raise ValueError("embedding output has an invalid norm")
    return [value / norm for value in quantized]
