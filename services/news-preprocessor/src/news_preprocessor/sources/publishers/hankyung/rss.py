import logging
from dataclasses import asdict
from datetime import datetime

import httpx
from bs4 import BeautifulSoup, Tag

from news_preprocessor.sources import USER_AGENT, EmptyBodyError, FeedEntry, NewsItem
from news_preprocessor.sources.publishers.hankyung.parser import parse_article_body

logger = logging.getLogger(__name__)


class HankyungEconomyRSS:
    source = "hankyung_economy"
    feed_url = "https://www.hankyung.com/feed/economy"
    pub_date_format = "%a, %d %b %Y %H:%M:%S %z"

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )

    def entries(self) -> list[FeedEntry]:
        response = self._client.get(self.feed_url, headers={"Accept": "text/xml"}, timeout=30)
        feed_text = response.raise_for_status().text
        feed = BeautifulSoup(feed_text, "xml")
        entries = []
        for item in feed.select("channel > item"):
            try:
                link = get_text(item, "link")
                title = get_text(item, "title")
                pub_date = get_text(item, "pubDate")
                if not (link and title and pub_date):
                    raise ValueError(f"missing link, title or pubDate: {link or title!r}")
                published_at = datetime.strptime(pub_date, self.pub_date_format)
                entries.append(
                    FeedEntry(
                        source=self.source,
                        external_id=link,
                        url=link,
                        title=title,
                        published_at=published_at,
                        raw_payload=str(item),
                    )
                )
            except ValueError as error:
                logger.warning("skipping %s feed item: %s", self.source, error)
        return entries

    def article(self, entry: FeedEntry) -> NewsItem:
        response = self._client.get(entry.url, headers={"Accept": "text/html"}, timeout=30)
        article = response.raise_for_status().text
        body = parse_article_body(article)
        if not body:
            raise EmptyBodyError(entry.url)
        return NewsItem(**asdict(entry), body=body)


def get_text(item: Tag, name: str) -> str:
    element = item.find(name)
    return element.get_text(strip=True) if element else ""
