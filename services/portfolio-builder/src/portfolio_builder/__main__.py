import functools
import uuid

import sqlalchemy as sa
from ktb_core.logging import get_logger, setup_logging, start_logging
from langchain_openrouter import ChatOpenRouter

from portfolio_builder.briefing import load_briefing
from portfolio_builder.builder import _generate, run_participants
from portfolio_builder.explain import (
    Explanations,
    explain,
    load_targets,
    load_unexplained,
    save_explanations,
)
from portfolio_builder.news_client import NewsClient
from portfolio_builder.portfolio import mark_explanation_failed, save_trace
from portfolio_builder.settings import Settings


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="portfolio-builder")
    if settings.competition_enabled:
        raise SystemExit(0 if run_participants(settings) else 1)
    log = get_logger(__name__, run_id=str(uuid.uuid4()))
    with start_logging(
        log,
        provider="openrouter",
        model=settings.llm_model,
        reasoning_level=settings.thinking_level,
        max_turns=settings.max_turns,
        news_window_days=settings.news_window_days,
    ) as end:
        engine = sa.create_engine(settings.postgres_dsn)
        news_client = NewsClient(settings.news_http_base_uri)
        model_factory = functools.partial(
            ChatOpenRouter, model=settings.llm_model, api_key=settings.llm_api_key
        )
        try:
            unexplained = load_unexplained(engine)
            if unexplained is None:
                briefing = load_briefing(engine, news_client, settings.news_window_days)
                log.info(
                    "ingestion",
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
                    frozenset(),
                    briefing.text,
                )
                end.update(turns=result.turns, usage=result.usage)
                outcome, portfolio_id, trace, error = (
                    result.outcome,
                    result.portfolio_id,
                    result.trace,
                    result.error,
                )
            else:
                portfolio_id, trace = unexplained
                outcome, error = "reexplained", None
                log.info("reexplain", portfolio_id=portfolio_id)
            if portfolio_id is not None:
                try:
                    if unexplained is None:
                        save_trace(engine, portfolio_id, trace)
                    explainer = ChatOpenRouter(
                        model=settings.llm_model,
                        api_key=settings.llm_api_key,
                        max_tokens=settings.explain_max_tokens,
                        reasoning={"effort": "none"},
                    ).with_structured_output(Explanations, include_raw=True)
                    explanations = explain(
                        explainer,
                        load_targets(engine, portfolio_id),
                        trace,
                        settings.explain_result_chars,
                    )
                    save_explanations(engine, portfolio_id, explanations)
                    log.info(
                        "explained", portfolio_id=portfolio_id, stocks=len(explanations.stocks)
                    )
                except Exception as failure:
                    outcome, error = "explanation_failed", f"explain: {failure}"
                    log.exception("explain_failed", portfolio_id=portfolio_id, error=str(failure))
                    mark_explanation_failed(engine, portfolio_id)
        finally:
            engine.dispose()

        end.update(outcome=outcome, portfolio_id=portfolio_id, error=error)
        raise SystemExit(0 if outcome in ("saved", "reexplained") else 1)


if __name__ == "__main__":
    main()
