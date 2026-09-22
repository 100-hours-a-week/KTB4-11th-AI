"""Extracts the article body from Hankyung's page markup."""

from news_preprocessor.sources import ArticleBodyParser


class HankyungEconomyParser(ArticleBodyParser):
    def __init__(self) -> None:
        super().__init__("article-body")
