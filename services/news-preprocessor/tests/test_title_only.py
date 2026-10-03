from datetime import datetime, timezone

import pytest
from news_preprocessor.sources import EmptyBodyError, FeedEntry
from news_preprocessor.sources.article_body import body_or_title

ENTRY = FeedEntry(
    source="test",
    external_id="https://example.com/news/1",
    url="https://example.com/news/1",
    title="제목만 있는 뉴스",
    published_at=datetime(2026, 10, 3, tzinfo=timezone.utc),
    raw_payload="<item />",
)


def test_uses_title_when_article_has_no_text_or_image():
    assert body_or_title(ENTRY, body="", has_image=False) == ENTRY.title


def test_keeps_article_body_when_text_exists():
    assert body_or_title(ENTRY, body="기사 본문", has_image=False) == "기사 본문"


def test_rejects_image_only_article():
    with pytest.raises(EmptyBodyError):
        body_or_title(ENTRY, body="", has_image=True)
