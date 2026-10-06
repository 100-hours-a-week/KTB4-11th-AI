from typing import NamedTuple

import httpx
import sqlalchemy as sa
from ktb_core.logging import get_logger

from news_preprocessor.sources import EmptyBodyError, NewsSource
from news_preprocessor.storage import insert_new, known_external_ids

log = get_logger(__name__)


class ScrapeResult(NamedTuple):
    succeed: list[str]
    failed: list[str]
    skipped: list[str]


def scrape(engine: sa.Engine, source: NewsSource) -> ScrapeResult:
    try:
        entries = source.entries()
    except Exception:
        log.exception("feed_failed", source=source.source)
        return ScrapeResult(succeed=[], failed=[source.feed_url], skipped=[])

    with engine.connect() as conn:
        known = known_external_ids(conn, source.source, [entry.external_id for entry in entries])

    succeed: list[str] = []
    failed: list[str] = []
    skipped: list[str] = []
    for entry in entries:
        if entry.external_id in known:
            continue
        try:
            item = source.article(entry)
        except EmptyBodyError:
            skipped.append(entry.external_id)
            log.warning(
                "empty_body_article",
                source=source.source,
                url=entry.url,
                article_id=entry.external_id,
            )
            continue
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if status in (404, 410):
                skipped.append(entry.external_id)
                log.warning(
                    "article_unavailable",
                    source=source.source,
                    url=entry.url,
                    article_id=entry.external_id,
                    status=status,
                    final_url=str(error.response.url),
                )
            else:
                failed.append(entry.external_id)
                log.exception("article_failed", source=source.source, url=entry.url)
            continue
        except Exception:
            failed.append(entry.external_id)
            log.exception("article_failed", source=source.source, url=entry.url)
            continue
        with engine.begin() as conn:
            if insert_new(conn, item):
                succeed.append(entry.external_id)
    log.info(
        "scrape_complete",
        source=source.source,
        feed_url=source.feed_url,
        entries=len(entries),
        new_articles=len(succeed),
        failed=len(failed),
        skipped=len(skipped),
    )
    return ScrapeResult(succeed=succeed, failed=failed, skipped=skipped)
