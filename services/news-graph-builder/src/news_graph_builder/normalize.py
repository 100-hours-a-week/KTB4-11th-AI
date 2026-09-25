import re
import unicodedata

CORPORATE_MARKERS = re.compile(r"\(주\)|㈜|주식회사")


def normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text)
    return "".join(CORPORATE_MARKERS.sub("", folded).split()).casefold()
