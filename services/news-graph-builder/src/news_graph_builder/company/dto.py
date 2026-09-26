from typing import NamedTuple


class DartCompany(NamedTuple):
    corp_code: str  # 8 digits ID from OpenDART
    corp_name: str
    corp_eng_name: str | None
    stock_code: str  # 6 alphanumeric ID
