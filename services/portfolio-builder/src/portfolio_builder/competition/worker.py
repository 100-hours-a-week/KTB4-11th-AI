from collections.abc import Callable
from functools import partial
from typing import Any

import sqlalchemy as sa
from ktb_core.logging import StructuredLogger
from langchain_core.language_models import BaseChatModel
from pydantic import ValidationError

from portfolio_builder.agent.run import RunResult, run_agent
from portfolio_builder.agent.system_prompt import COMPETITION_SYSTEM_PROMPT
from portfolio_builder.agent.trace import TraceEntry
from portfolio_builder.briefing import load_briefing
from portfolio_builder.competition import jev
from portfolio_builder.competition.backend import Backend
from portfolio_builder.competition.canonical import classify_additional_request
from portfolio_builder.competition.message import Request
from portfolio_builder.competition.repository import (
    claim_job,
    fail_job,
    finish_job,
    generation_key,
    latest_participant_portfolio,
    link_portfolio,
    store_settings,
)
from portfolio_builder.database import portfolios
from portfolio_builder.explain import Explanations, explain_and_save
from portfolio_builder.market import HttpMarket
from portfolio_builder.news_client import NewsClient
from portfolio_builder.portfolio import excluded_company_ids, mark_explanation_failed, save_trace
from portfolio_builder.settings import Settings
from portfolio_builder.tools.graph.tools import graph_tools
from portfolio_builder.tools.news.tools import news_tools
from portfolio_builder.tools.submit import submit_tool
from portfolio_builder.tools.technicals import technicals_tool

ModelFactory = Callable[..., BaseChatModel]


def _deliver(
    sqs: Any,
    queue_url: str,
    receipt_handle: str,
    log: StructuredLogger,
    notify: Callable[[], None],
) -> None:
    try:
        notify()
    except Exception as error:
        log.error("notify_failed", error_type=type(error).__name__)
        return
    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)


def _explain(
    engine: sa.Engine,
    model_factory: ModelFactory,
    settings: Settings,
    log: StructuredLogger,
    portfolio_id: int,
    trace: list[TraceEntry],
) -> None:
    try:
        explainer = model_factory(
            max_tokens=settings.explain_max_tokens, reasoning={"effort": "none"}
        ).with_structured_output(Explanations, include_raw=True)
        stocks = explain_and_save(
            engine, explainer, portfolio_id, trace, settings.explain_result_chars
        )
        log.info("explained", portfolio_id=portfolio_id, stocks=stocks)
    except Exception as error:
        log.exception("explain_failed", portfolio_id=portfolio_id, error=str(error))
        mark_explanation_failed(engine, portfolio_id)


def _generate(
    engine: sa.Engine,
    settings: Settings,
    log: StructuredLogger,
    model_factory: ModelFactory,
    news_client: NewsClient,
    previous_company_ids: frozenset[str],
    excluded: frozenset[str],
    briefing_text: str,
    participant_id: int,
) -> RunResult:
    market = HttpMarket(settings.market_http_base_uri, engine)
    model = model_factory(reasoning={"effort": settings.thinking_level})
    tools = [
        *news_tools(news_client),
        *graph_tools(news_client),
        technicals_tool(engine, market),
        submit_tool(
            engine,
            previous_company_ids,
            f"openrouter/{settings.llm_model}",
            news_client,
            log,
            excluded,
            participant_id,
        ),
    ]
    return run_agent(
        model=model,
        tools=tools,
        system_prompt=COMPETITION_SYSTEM_PROMPT,
        briefing=briefing_text,
        max_turns=settings.max_turns,
        log=log,
    )


def _classify(
    engine: sa.Engine,
    settings: Settings,
    log: StructuredLogger,
    request: Request,
) -> tuple[Any, str | None, bool]:
    ask = partial(
        jev.ask,
        settings.jev_base_uri,
        settings.jev_model,
        settings.llm_api_key,
        timeout=settings.jev_timeout,
    )
    return classify_additional_request(
        engine,
        ask,
        settings.jev_model,
        settings.jev_noul_threshold,
        settings.jev_choice_min_confidence,
        request.additional_request,
        request.effective_interested,
        log,
    )


def process_message(
    message: dict[str, object],
    sqs: Any,
    engine: sa.Engine,
    backend: Backend,
    model_factory: ModelFactory,
    settings: Settings,
    log: StructuredLogger,
) -> None:
    queue_url = str(message["_queue_url"])
    receipt_handle = str(message["ReceiptHandle"])

    try:
        request = Request.model_validate_json(str(message["Body"]))
    except ValidationError as error:
        log.error("invalid_message", error_type=type(error).__name__)
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
        return

    participant, differing_fields = store_settings(
        engine, request, partial(_classify, engine, settings, log)
    )
    if differing_fields:
        log.warning(
            "settings_mismatch",
            participant_id=participant.participant_id,
            fields=differing_fields,
        )

    previous = latest_participant_portfolio(engine, participant.participant_id)
    previous_portfolio_id = previous[0] if previous is not None else None

    if previous is not None:
        portfolio_id, status, has_trace = previous
        if status != "ready" and has_trace:
            with engine.connect() as conn:
                trace_rows = conn.execute(
                    sa.select(portfolios.c.trace).where(portfolios.c.id == portfolio_id)
                ).scalar_one()
            trace = [TraceEntry.model_validate(entry) for entry in trace_rows]
            _explain(engine, model_factory, settings, log, portfolio_id, trace)
            _deliver(
                sqs,
                queue_url,
                receipt_handle,
                log,
                lambda: backend.completed(participant, portfolio_id),
            )
            return

    key = generation_key(participant, previous_portfolio_id, settings.llm_model)
    claim = claim_job(
        engine,
        key,
        participant,
        previous_portfolio_id,
        settings.competition_stale_running_seconds,
        settings.competition_max_attempts,
    )
    if claim is None:
        return

    if not claim.mine:
        link_portfolio(engine, participant.participant_id, claim.portfolio_id)
        _deliver(
            sqs,
            queue_url,
            receipt_handle,
            log,
            lambda: backend.completed(participant, claim.portfolio_id),
        )
        return

    excluded = excluded_company_ids(engine, participant.excluded_theme_codes)
    log.info(
        "excluded_themes",
        excluded_theme_codes=participant.excluded_theme_codes,
        excluded_company_count=len(excluded),
    )
    news_client = NewsClient(settings.news_http_base_uri)
    briefing = load_briefing(engine, news_client, settings.news_window_days, participant, excluded)

    result = _generate(
        engine,
        settings,
        log,
        model_factory,
        news_client,
        briefing.previous_company_ids,
        excluded,
        briefing.text,
        participant.participant_id,
    )

    if result.portfolio_id is None:
        error = result.error or f"agent ended with outcome {result.outcome}"
        fail_job(engine, key, error)
        _deliver(sqs, queue_url, receipt_handle, log, lambda: backend.failed(participant, error))
        return

    save_trace(engine, result.portfolio_id, result.trace)
    link_portfolio(engine, participant.participant_id, result.portfolio_id)
    finish_job(engine, key, result.portfolio_id)
    _explain(engine, model_factory, settings, log, result.portfolio_id, result.trace)
    _deliver(
        sqs,
        queue_url,
        receipt_handle,
        log,
        lambda: backend.completed(participant, result.portfolio_id),
    )
