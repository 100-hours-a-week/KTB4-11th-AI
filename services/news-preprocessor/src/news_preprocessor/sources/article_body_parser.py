"""Extracts an article's text from the first element carrying one CSS class."""

from html.parser import HTMLParser

VOID_ELEMENTS = frozenset("area base br col embed hr img input link meta source track wbr".split())


class ArticleBodyParser(HTMLParser):
    """Collects text inside the first element whose class list contains `body_class`.

    Void elements never close, so they must not move the nesting depth — in either
    handler, because `html.parser` routes a self-closing `<br/>` through both.
    """

    def __init__(self, body_class: str) -> None:
        super().__init__()
        self._body_class = body_class
        self._depth = 0
        self._done = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in VOID_ELEMENTS or self._done:
            return
        if self._depth:
            self._depth += 1
        elif self._body_class in (dict(attrs).get("class") or "").split():
            self._depth = 1

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_ELEMENTS or not self._depth:
            return
        self._depth -= 1
        if not self._depth:
            self._done = True

    def handle_data(self, data: str) -> None:
        if self._depth:
            self._parts.append(data)

    @property
    def text(self) -> str:
        return " ".join("".join(self._parts).split())


def parse_article_text(html: bytes, parser: ArticleBodyParser) -> str:
    parser.feed(html.decode("utf-8", errors="replace"))
    parser.close()
    return parser.text
