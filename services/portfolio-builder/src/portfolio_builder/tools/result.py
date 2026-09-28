import json
from datetime import date


def json_result(value: object) -> str:
    """A tool result as JSON the model can read: Korean kept as-is, dates as ISO 8601."""
    return json.dumps(
        value,
        ensure_ascii=False,
        default=lambda v: v.isoformat() if isinstance(v, date) else str(v),
    )
