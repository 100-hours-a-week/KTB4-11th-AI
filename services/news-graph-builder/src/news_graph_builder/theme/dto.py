from typing import NamedTuple


class Theme(NamedTuple):
    code: str
    name: str
    main_stocks: str


class ThemeMember(NamedTuple):
    stock_code: str
    stock_name: str
