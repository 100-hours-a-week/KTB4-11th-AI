import json
from dataclasses import asdict
from datetime import datetime
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup
from ktb_core.logging import get_logger

from news_preprocessor.sources import FeedEntry, NewsItem
from news_preprocessor.sources.article_body import body_or_title

log = get_logger(__name__)


class ChosunEconomyRSS:
    source = "chosun_economy"
    feed_url = "https://www.chosun.com/arc/outboundfeeds/rss/?outputType=xml"

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def entries(self) -> list[FeedEntry]:
        response = self._client.get(self.feed_url, headers={"Accept": "text/xml"}, timeout=30)
        feed = BeautifulSoup(response.raise_for_status().text, "xml")
        entries = []
        for item in feed.select("channel > item"):
            try:
                link = item.find("link")
                title = item.find("title")
                pub_date = item.find("pubDate")
                if not (link and title and pub_date):
                    raise ValueError("missing link, title or pubDate")
                url = link.get_text(strip=True)
                if not urlsplit(url).path.startswith("/economy/"):
                    continue
                entries.append(
                    FeedEntry(
                        source=self.source,
                        external_id=url,
                        url=url,
                        title=title.get_text(strip=True),
                        published_at=datetime.strptime(
                            pub_date.get_text(strip=True), "%a, %d %b %Y %H:%M:%S %z"
                        ),
                        raw_payload=str(item),
                    )
                )
            except ValueError as error:
                log.warning("feed_item_skipped", source=self.source, error=error)
        return entries

    def article(self, entry: FeedEntry) -> NewsItem:
        response = self._client.get(entry.url, headers={"Accept": "text/html"}, timeout=30)
        html = response.raise_for_status().text
        marker = "Fusion.globalContent="
        if marker not in html:
            return NewsItem(**asdict(entry), body=entry.title)
        content = json.JSONDecoder().raw_decode(html.split(marker, 1)[1])[0]
        body = " ".join(
            BeautifulSoup(element.get("content", ""), "html.parser").get_text(" ", strip=True)
            for element in content.get("content_elements", [])
            if element.get("type") == "text"
        ).strip()
        has_image = any(
            element.get("type") == "image" for element in content.get("content_elements", [])
        )
        return NewsItem(**asdict(entry), body=body_or_title(entry, body, has_image))
