import uuid

import sqlalchemy as sa
from ktb_core.logging import emit_run_logs, get_logger, setup_logging
from langchain_openrouter import ChatOpenRouter

from portfolio_builder.agent.run import run_agent
from portfolio_builder.agent.system_prompt import SYSTEM_PROMPT
from portfolio_builder.briefing import load_briefing
from portfolio_builder.market import QuestDBMarket
from portfolio_builder.settings import Settings
from portfolio_builder.tools.graph.tools import graph_tools
from portfolio_builder.tools.news.tools import news_tools
from portfolio_builder.tools.submit import submit_tool
from portfolio_builder.tools.technicals import technicals_tool


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="portfolio-builder")
    log = get_logger(__name__, run_id=str(uuid.uuid4()))
    with emit_run_logs(
        log,
        provider="openrouter",
        model=settings.llm_model,
        reasoning_level=settings.thinking_level,
        max_turns=settings.max_turns,
        news_window_days=settings.news_window_days,
    ) as end:
        engine = sa.create_engine(settings.postgres_dsn)
        try:
            briefing = load_briefing(engine, settings.news_window_days)
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
            log.info("prompt", system_prompt=SYSTEM_PROMPT, briefing=briefing.text)
            model = ChatOpenRouter(
                model=settings.llm_model,
                api_key=settings.openrouter_api_key,
                reasoning={"effort": settings.thinking_level},
            )
            tools = [
                *news_tools(engine),
                *graph_tools(engine),
                technicals_tool(engine, QuestDBMarket(settings.questdb_conf, engine)),
                submit_tool(
                    engine,
                    briefing.previous_company_ids,
                    f"openrouter/{settings.llm_model}",
                    log,
                ),
            ]
            result = run_agent(
                model=model,
                tools=tools,
                system_prompt=SYSTEM_PROMPT,
                briefing=briefing.text,
                max_turns=settings.max_turns,
                log=log,
            )
        finally:
            engine.dispose()

        end.update(
            outcome=result.outcome,
            portfolio_id=result.portfolio_id,
            turns=result.turns,
            usage=result.usage,
            error=result.error,
        )
        raise SystemExit(0 if result.outcome == "saved" else 1)


if __name__ == "__main__":
    main()
