from typing import NamedTuple

from opendartreader import OpenDartReader

__all__ = ["DartCorporation", "fetch_corp_codes"]


class DartCorporation(NamedTuple):
    corp_code: str  # 8 digits ID from OpenDART
    corp_name: str
    corp_eng_name: str | None
    stock_code: str  # 6 alphanumeric ID


def fetch_corp_codes(api_key: str) -> list[DartCorporation]:
    try:
        frame = OpenDartReader(api_key).corp_codes
    except Exception as error:
        # DART status errors are a ValueError holding a {'status', 'message'} dict.
        detail = (
            error.args[0]
            if isinstance(error, ValueError) and error.args and isinstance(error.args[0], dict)
            else type(error).__name__
        )
        raise RuntimeError(f"DART corp_codes failed: {detail}") from None
    return [
        DartCorporation(
            row.corp_code,
            row.corp_name,
            row.corp_eng_name.strip() or None,
            row.stock_code.strip(),
        )
        # pandas 3 stores a missing string as NaN, which is truthy and has no strip().
        for row in frame.fillna("").itertuples()
        if row.stock_code.strip()
    ]
