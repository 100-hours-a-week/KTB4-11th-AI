"""Types and helpers shared by every news publisher adapter."""

from news_preprocessor.sources.article_body_parser import ArticleBodyParser, parse_article_text
from news_preprocessor.sources.empty_body_error import EmptyBodyError
from news_preprocessor.sources.http import fetch_bytes
from news_preprocessor.sources.news_item import FeedEntry, NewsItem
from news_preprocessor.sources.news_source import NewsSource

__all__ = [
    "ArticleBodyParser",
    "EmptyBodyError",
    "FeedEntry",
    "NewsItem",
    "NewsSource",
    "fetch_bytes",
    "parse_article_text",
]
