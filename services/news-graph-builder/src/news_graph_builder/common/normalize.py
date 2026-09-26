import re
import unicodedata


def normalize(text: str) -> str:
    for step in (fold_width, remove_corporate_markers, remove_whitespace, str.casefold):
        text = step(text)
    return text


def fold_width(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def remove_corporate_markers(text: str) -> str:
    corporate_markers = re.compile(r"\(주\)|㈜|주식회사")
    return corporate_markers.sub("", text)


def remove_whitespace(text: str) -> str:
    return "".join(text.split())
