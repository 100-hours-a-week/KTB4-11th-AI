"""Extracts the article body from Maeil Business's page markup."""

from news_preprocessor.sources import ArticleBodyParser


class MaeilBusinessEconomyParser(ArticleBodyParser):
    def __init__(self) -> None:
        super().__init__("news_cnt_detail_wrap")
