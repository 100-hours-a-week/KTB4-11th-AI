import math

INT8_SCALE = 127
INT8_MIN = -128
INT8_MAX = 127


def quantize(raw: list[float]) -> list[float]:
    if not all(math.isfinite(value) for value in raw):
        raise ValueError("embedding output is not finite")
    return [
        float(min(INT8_MAX, max(INT8_MIN, round(math.tanh(value) * INT8_SCALE)))) for value in raw
    ]


def truncate_and_normalize(quantized: list[float], dimensions: int) -> list[float]:
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
    if len(raw) < dimensions:
        raise ValueError(
            f"embedding output has {len(raw)} values, fewer than the contracted {dimensions}"
        )
    return truncate_and_normalize(quantize(raw), dimensions)
