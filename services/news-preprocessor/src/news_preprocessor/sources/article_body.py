import subprocess
from collections.abc import Sequence
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import Tag
from ktb_core.logging import get_logger
from PIL import Image

from news_preprocessor.ocr import image_text
from news_preprocessor.settings import OCRSettings
from news_preprocessor.sources.empty_body_error import ImageOnlyArticleError
from news_preprocessor.sources.news_item import FeedEntry

ARTICLE_IMAGE_SELECTOR = "img, picture, figure, [class*='photo'], [class*='image']"
log = get_logger(__name__)


def contains_article_image(content: Tag | None) -> bool:
    return content is not None and content.select_one(ARTICLE_IMAGE_SELECTOR) is not None


def article_image_urls(content: Tag | None, base_url: str) -> list[str]:
    if content is None:
        return []
    urls: list[str] = []
    images = [
        image
        for image in content.select("picture, img")
        if image.name == "picture" or image.find_parent("picture") is None
    ]
    for image in images:
        alternatives = (
            image.select("img") + image.select("source") if image.name == "picture" else [image]
        )
        resolved_url = None
        for alternative in alternatives:
            candidates = [alternative.get("data-src"), alternative.get("data-original")]
            srcset = alternative.get("data-srcset") or alternative.get("srcset")
            if isinstance(srcset, str) and srcset.strip():
                candidates.append(srcset.split(",")[-1].strip().split()[0])
            candidates.append(alternative.get("src"))
            for candidate in candidates:
                if not isinstance(candidate, str) or not candidate.strip():
                    continue
                try:
                    url = urljoin(base_url, candidate.strip())
                    if urlsplit(url).scheme in ("http", "https"):
                        resolved_url = url
                        break
                except ValueError:
                    continue
            if resolved_url:
                break
        if resolved_url is None:
            log.warning("article_image_url_missing", url=base_url, image_count=len(images))
            raise ImageOnlyArticleError(len(images))
        if resolved_url not in urls:
            urls.append(resolved_url)
    return urls


def body_or_title(
    entry: FeedEntry,
    body: str,
    has_image: bool,
    *,
    image_urls: Sequence[str] = (),
    client: httpx.Client | None = None,
) -> str:
    if body:
        return body
    if not has_image:
        return entry.title
    settings = OCRSettings()
    if not settings.enabled:
        raise ImageOnlyArticleError(len(image_urls))
    if not image_urls or client is None or len(image_urls) > settings.max_images:
        log.warning(
            "article_ocr_failed",
            source=entry.source,
            url=entry.url,
            article_id=entry.external_id,
            image_count=len(image_urls),
            error="image URLs unavailable or image count exceeds limit",
        )
        raise ImageOnlyArticleError(len(image_urls))
    texts = []
    for image_url in image_urls:
        try:
            texts.append(image_text(client, image_url, settings))
        except (
            httpx.HTTPError,
            OSError,
            ValueError,
            subprocess.SubprocessError,
            Image.DecompressionBombError,
        ) as error:
            log.warning(
                "article_ocr_failed",
                source=entry.source,
                url=entry.url,
                article_id=entry.external_id,
                image_url=image_url,
                error=str(error),
            )
            raise ImageOnlyArticleError(len(image_urls)) from error
    if texts:
        text = " ".join(texts)
        log.info(
            "article_ocr_complete",
            source=entry.source,
            url=entry.url,
            article_id=entry.external_id,
            image_count=len(image_urls),
            recognized_images=len(texts),
            characters=len(text),
        )
        return text
    raise ImageOnlyArticleError(len(image_urls))
