from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup
from ktb_core.logging import get_logger

from news_preprocessor.sources import FeedEntry

log = get_logger(__name__)


class OpenDartRSS:
    source = "opendart"
    feed_url = "https://dart.fss.or.kr/api/companyRSS.xml"

    def __init__(self, client: httpx.Client, corp_code: str) -> None:
        self._client = client
        self._corp_code = corp_code

    def entries(self) -> list[FeedEntry]:
        response = self._client.get(
            self.feed_url,
            params={"crpCd": self._corp_code},
            headers={"Accept": "text/xml"},
            timeout=30,
        )
        feed = BeautifulSoup(response.raise_for_status().content, "xml")
        if feed.find("rss") is None or feed.find("channel") is None:
            raise ValueError("invalid DART RSS feed")
        entries = []
        for item in feed.select("channel > item"):
            try:
                title, link, pub_date = (item.find(name) for name in ("title", "link", "pubDate"))
                if title is None or link is None or pub_date is None:
                    raise ValueError("missing title, link or pubDate")
                url = link.get_text(strip=True)
                parsed = urlparse(url)
                receipt = parse_qs(parsed.query).get("rcpNo", [""])[0]
                if (
                    parsed.scheme not in ("http", "https")
                    or parsed.hostname != "dart.fss.or.kr"
                    or len(receipt) != 14
                    or not receipt.isascii()
                    or not receipt.isdigit()
                ):
                    raise ValueError("invalid DART receipt link")
                published_at = parsedate_to_datetime(pub_date.get_text(strip=True))
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=timezone.utc)
                entries.append(
                    FeedEntry(
                        source=self.source,
                        external_id=receipt,
                        url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
                        title=" ".join(title.get_text().split()),
                        published_at=published_at,
                        raw_payload=str(item),
                    )
                )
            except (TypeError, ValueError) as error:
                log.warning("feed_item_skipped", source=self.source, error=error)
        return entries
