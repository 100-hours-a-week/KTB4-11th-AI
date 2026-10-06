from dataclasses import asdict
from datetime import datetime

import httpx
from bs4 import BeautifulSoup
from ktb_core.logging import get_logger

from news_preprocessor.sources import FeedEntry, NewsItem
from news_preprocessor.sources.article_body import (
    article_image_urls,
    body_or_title,
    contains_article_image,
)

log = get_logger(__name__)


class EdailyRSS:
    source = "edaily"
    sections = ("economy", "stock")
    pub_date_format = "%a, %d %b %Y %H:%M:%S %z"

    def __init__(self, client: httpx.Client, section: str) -> None:
        self._client = client
        self.feed_url = f"http://rss.edaily.co.kr/{section}_news.xml"

    def entries(self) -> list[FeedEntry]:
        response = self._client.get(self.feed_url, headers={"Accept": "text/xml"}, timeout=30)
        feed = BeautifulSoup(response.raise_for_status().text, "xml")
        entries = []
        for item in feed.select("channel > item"):
            try:
                link = item.link.get_text(strip=True) if item.link else ""
                title = item.title.get_text(strip=True) if item.title else ""
                pub_date = item.pubDate.get_text(strip=True) if item.pubDate else ""
                if not (link and title and pub_date):
                    raise ValueError(f"missing link, title or pubDate: {link or title!r}")
                entries.append(
                    FeedEntry(
                        source=self.source,
                        external_id=link,
                        url=link,
                        title=title,
                        published_at=datetime.strptime(pub_date, self.pub_date_format),
                        raw_payload=str(item),
                    )
                )
            except ValueError as error:
                log.warning("feed_item_skipped", source=self.source, error=error)
        return entries

    def article(self, entry: FeedEntry) -> NewsItem:
        response = self._client.get(entry.url, headers={"Accept": "text/html"}, timeout=30)
        article = response.raise_for_status().text
        body = BeautifulSoup(article, "html.parser").select_one(".news_body")
        has_image = contains_article_image(body)
        if body:
            for element in body.select("table, .view_ad01, .view_ad02, script, style, iframe"):
                element.decompose()
        text = " ".join(body.get_text(" ").split()) if body else ""
        image_urls = article_image_urls(body, str(response.url)) if not text else []
        text = body_or_title(entry, text, has_image, image_urls=image_urls, client=self._client)
        return NewsItem(**asdict(entry), body=text)
