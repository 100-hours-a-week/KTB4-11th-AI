from bs4 import Tag

from news_preprocessor.sources.empty_body_error import EmptyBodyError
from news_preprocessor.sources.news_item import FeedEntry

ARTICLE_IMAGE_SELECTOR = "img, picture, figure, [class*='photo'], [class*='image']"


def contains_article_image(content: Tag | None) -> bool:
    return content is not None and content.select_one(ARTICLE_IMAGE_SELECTOR) is not None


def body_or_title(entry: FeedEntry, body: str, has_image: bool) -> str:
    if body:
        return body
    if not has_image:
        return entry.title
    raise EmptyBodyError(entry.url)
