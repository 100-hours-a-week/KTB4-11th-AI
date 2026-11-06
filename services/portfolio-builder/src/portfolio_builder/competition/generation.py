from collections.abc import Callable

import sqlalchemy as sa
from disclosure_retrieval.settings import Settings as DisclosureSettings
from ktb_core.logging import StructuredLogger
from langchain_core.language_models import BaseChatModel

from portfolio_builder.agent.run import RunResult, run_agent
from portfolio_builder.agent.system_prompt import COMPETITION_SYSTEM_PROMPT, DISCLOSURE_PROMPT
from portfolio_builder.agent.trace import TraceEntry
from portfolio_builder.briefing import load_briefing
from portfolio_builder.competition.repository import (
    ParticipantSettings,
    claim_job,
    fail_job,
    finish_job,
    generation_key,
    latest_participant_portfolio,
    link_portfolio,
    participants_without_ready_portfolio,
)
from portfolio_builder.database import portfolios
from portfolio_builder.explain import Explanations, explain_and_save
from portfolio_builder.market import HttpMarket
from portfolio_builder.news_client import NewsClient
from portfolio_builder.portfolio import excluded_company_ids, mark_explanation_failed, save_trace
from portfolio_builder.settings import Settings
from portfolio_builder.tools.disclosure.tool import disclosure_tools
from portfolio_builder.tools.graph.tools import graph_tools
from portfolio_builder.tools.news.tools import news_tools
from portfolio_builder.tools.submit import submit_tool
from portfolio_builder.tools.technicals import technicals_tool

ModelFactory = Callable[..., BaseChatModel]


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
    disclosure_settings = DisclosureSettings() if settings.disclosure_enabled else None
    tools = [
        *news_tools(news_client),
        *(disclosure_tools(engine, disclosure_settings) if disclosure_settings else []),
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
    system_prompt = COMPETITION_SYSTEM_PROMPT + (
        DISCLOSURE_PROMPT if settings.disclosure_enabled else ""
    )
    log.info("prompt", system_prompt=system_prompt, briefing=briefing_text)
    return run_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        briefing=briefing_text,
        max_turns=settings.max_turns,
        log=log,
        disclosure_enabled=settings.disclosure_enabled,
    )


def _generate_for(
    participant: ParticipantSettings,
    engine: sa.Engine,
    model_factory: ModelFactory,
    settings: Settings,
    log: StructuredLogger,
) -> None:
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
        log.info("generation_skipped", participant_id=participant.participant_id, key=key)
        return

    if not claim.mine:
        link_portfolio(engine, participant.participant_id, claim.portfolio_id)
        log.info(
            "shared",
            participant_id=participant.participant_id,
            portfolio_id=claim.portfolio_id,
            key=key,
        )
        return

    try:
        excluded = excluded_company_ids(engine, participant.excluded_theme_codes)
        log.info(
            "excluded_themes",
            excluded_theme_codes=participant.excluded_theme_codes,
            excluded_company_count=len(excluded),
        )
        news_client = NewsClient(settings.news_http_base_uri)
        briefing = load_briefing(
            engine, news_client, settings.news_window_days, participant, excluded
        )
        log.info(
            "ingestion",
            participant_id=participant.participant_id,
            previous_portfolio_id=briefing.previous_portfolio_id,
            previous_holdings=briefing.previous_holdings,
            previous_exits=briefing.previous_exits,
            cluster_ids=briefing.cluster_ids,
            cluster_count=len(briefing.cluster_ids),
            company_count=briefing.company_count,
            theme_count=briefing.theme_count,
            briefing_chars=len(briefing.text),
        )

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
        log.info(
            "generated",
            participant_id=participant.participant_id,
            outcome=result.outcome,
            portfolio_id=result.portfolio_id,
            turns=result.turns,
            usage=result.usage,
        )

        if result.portfolio_id is None:
            error = result.error or f"agent ended with outcome {result.outcome}"
            fail_job(engine, key, error)
            return

        save_trace(engine, result.portfolio_id, result.trace)
        link_portfolio(engine, participant.participant_id, result.portfolio_id)
        finish_job(engine, key, result.portfolio_id)
    except Exception as error:
        log.exception(
            "generation_failed",
            participant_id=participant.participant_id,
            error=str(error),
        )
        fail_job(engine, key, f"{type(error).__name__}: {error}")
        return

    _explain(engine, model_factory, settings, log, result.portfolio_id, result.trace)


def generate_missing_portfolios(
    engine: sa.Engine,
    model_factory: ModelFactory,
    settings: Settings,
    log: StructuredLogger,
) -> None:
    for participant in participants_without_ready_portfolio(
        engine, settings.competition_max_attempts
    ):
        _generate_for(participant, engine, model_factory, settings, log)
