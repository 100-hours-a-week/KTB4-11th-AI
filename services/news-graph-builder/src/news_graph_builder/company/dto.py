from typing import NamedTuple


class DartCompany(NamedTuple):
    corp_code: str
    corp_name: str
    corp_eng_name: str | None
    stock_code: str
