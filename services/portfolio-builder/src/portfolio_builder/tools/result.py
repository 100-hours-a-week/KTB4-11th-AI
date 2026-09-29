import json
from datetime import date


def json_result(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        default=lambda v: v.isoformat() if isinstance(v, date) else str(v),
    )
