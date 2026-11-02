from typing import NamedTuple

import sqlalchemy as sa
from ktb_core.logging import get_logger

from news_preprocessor.sources import EmptyBodyError, NewsSource
from news_preprocessor.storage import insert_new, known_external_ids

log = get_logger(__name__)


class ScrapeResult(NamedTuple):
    succeed: list[str]
    failed: list[str]


def scrape(engine: sa.Engine, source: NewsSource) -> ScrapeResult:
    try:
        entries = source.entries()
    except Exception as error:
        log.exception(
            "feed_failed",
            source=source.source,
            feed_url=source.feed_url,
            stage="listing",
            error_type=type(error).__name__,
        )
        return ScrapeResult(succeed=[], failed=[source.feed_url])

    with engine.connect() as conn:
        known = known_external_ids(conn, source.source, [entry.external_id for entry in entries])

    succeed: list[str] = []
    failed: list[str] = []
    for entry in entries:
        if entry.external_id in known:
            continue
        try:
            item = source.article(entry)
        except EmptyBodyError:
            log.warning(
                "empty_body_article",
                source=source.source,
                url=entry.url,
                article_id=entry.external_id,
            )
            continue
        except Exception as error:
            log.exception(
                "article_failed",
                source=source.source,
                url=entry.url,
                article_id=entry.external_id,
                stage="fetch_parse",
                error_type=type(error).__name__,
            )
            failed.append(entry.external_id)
            continue
        try:
            with engine.begin() as conn:
                if insert_new(conn, item):
                    succeed.append(entry.external_id)
        except Exception as error:
            log.exception(
                "article_save_failed",
                source=source.source,
                url=entry.url,
                article_id=entry.external_id,
                stage="storage",
                error_type=type(error).__name__,
            )
            raise
    log.info(
        "scrape_complete",
        source=source.source,
        feed_url=source.feed_url,
        entries=len(entries),
        new_articles=len(succeed),
        failed=len(failed),
    )
    return ScrapeResult(succeed=succeed, failed=failed)
