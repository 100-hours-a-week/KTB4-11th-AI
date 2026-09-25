import re

CORPORATE_MARKERS = re.compile(r"\(주\)|㈜|주식회사")


def normalize(text: str) -> str:
    return "".join(CORPORATE_MARKERS.sub("", text).split()).casefold()
