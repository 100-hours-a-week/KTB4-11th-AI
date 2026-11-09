import re

import sqlalchemy as sa

from market_http.repositories.stocks import StockMatch, list_kospi200_names

CHOSEONG = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
SYLLABLE_START = 0xAC00
SYLLABLES = 11172
JONGSEONG = 28
CHOSEONG_SPAN = 21 * JONGSEONG


def hangul_pattern(query: str) -> re.Pattern[str]:
    parts = []
    for index, char in enumerate(query):
        offset = ord(char) - SYLLABLE_START
        if char in CHOSEONG:
            start = SYLLABLE_START + CHOSEONG.index(char) * CHOSEONG_SPAN
            parts.append(f"[{chr(start)}-{chr(start + CHOSEONG_SPAN - 1)}]")
        elif index == len(query) - 1 and 0 <= offset < SYLLABLES and offset % JONGSEONG == 0:
            parts.append(f"[{char}-{chr(ord(char) + JONGSEONG - 1)}]")
        else:
            parts.append(re.escape(char))
    return re.compile("".join(parts), re.IGNORECASE)


def _literal_rank(name: str, query: str) -> int | None:
    if name == query:
        return 0
    if name.startswith(query):
        return 1
    if query in name:
        return 2
    return None


def _rank(name: str, english_name: str | None, query: str, pattern: re.Pattern[str]) -> int | None:
    rank = _literal_rank(name.casefold(), query)
    if rank is not None:
        return rank
    if pattern.search(name):
        return 3
    if english_name is None:
        return None
    rank = _literal_rank(english_name.casefold(), query)
    return None if rank is None else rank + 4


def search_stocks(engine: sa.Engine, query: str, size: int | None = None) -> list[StockMatch]:
    query = query.casefold()
    pattern = hangul_pattern(query)
    ranked = []
    for code, name, english_name, sector_name in list_kospi200_names(engine):
        ranks = (_literal_rank(code.casefold(), query), _rank(name, english_name, query, pattern))
        rank = min((rank for rank in ranks if rank is not None), default=None)
        if rank is not None:
            ranked.append((rank, code, name, sector_name))
    return [
        StockMatch(code=code, name=name, sector=sector_name)
        for _, code, name, sector_name in sorted(ranked)[:size]
    ]
