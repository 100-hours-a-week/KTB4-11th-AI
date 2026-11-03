from typing import NamedTuple

import sqlalchemy as sa
from ktb_core.logging import get_logger

from news_preprocessor.sources import EmptyBodyError, NewsSource
from news_preprocessor.sources.publishers.opendart.opendart import OpenDart
from news_preprocessor.sources.publishers.opendart.storage import (
    complete_collection,
    load_collection_state,
    remember_collection_attempt,
)
from news_preprocessor.storage import insert_new, known_external_ids

log = get_logger(__name__)


class ScrapeResult(NamedTuple):
    succeed: list[str]
    failed: list[str]


def scrape(engine: sa.Engine, source: NewsSource) -> ScrapeResult:
    stage = "listing"
    entries = None
    if isinstance(source, OpenDart):
        source.collection_plan = None
    try:
        if isinstance(source, OpenDart):
            stage = "state_load"
            with engine.connect() as conn:
                source.collection_state = load_collection_state(conn)
            stage = "listing"
        entries = source.entries()
    except Exception as error:
        log.exception(
            "feed_failed",
            source=source.source,
            feed_url=source.feed_url,
            stage=stage,
            error_type=type(error).__name__,
        )
        return ScrapeResult(succeed=[], failed=[source.feed_url])
    finally:
        if isinstance(source, OpenDart) and source.collection_plan is not None:
            plan = source.collection_plan
            first_date = None
            if plan.initial and entries is not None:
                first_date = min((e.published_at.date() for e in entries), default=plan.end_date)
            try:
                with engine.begin() as conn:
                    remember_collection_attempt(conn, plan.scheduled_day, first_date)
            except Exception as error:
                log.exception(
                    "opendart_state_failed",
                    source=source.source,
                    stage="attempt",
                    error_type=type(error).__name__,
                )
                raise

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
    if isinstance(source, OpenDart) and source.collection_plan is not None and not failed:
        plan = source.collection_plan
        try:
            with engine.begin() as conn:
                complete_collection(
                    conn,
                    plan.end_date,
                    plan.scheduled_day,
                    plan.limit is not None and len(entries) == plan.limit,
                )
            log.info(
                "opendart_collection_completed",
                initial=plan.initial,
                listed_through=plan.end_date,
                scheduled_day=plan.scheduled_day,
                selected=len(entries),
                already_stored=len(known),
                stored=len(succeed),
                recheck_boundary=plan.limit is not None and len(entries) == plan.limit,
            )
        except Exception as error:
            log.exception(
                "opendart_state_failed",
                source=source.source,
                stage="completion",
                error_type=type(error).__name__,
            )
            raise
    log.info(
        "scrape_complete",
        source=source.source,
        feed_url=source.feed_url,
        entries=len(entries),
        new_articles=len(succeed),
        already_stored=len(known),
        failed=len(failed),
    )
    return ScrapeResult(succeed=succeed, failed=failed)
