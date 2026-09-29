import logging
import uuid

import sqlalchemy as sa
from ktb_core.logging import bind_logger, setup_logging
from langchain_openrouter import ChatOpenRouter

from portfolio_builder.agent.run import run_agent
from portfolio_builder.agent.system_prompt import SYSTEM_PROMPT
from portfolio_builder.briefing import load_briefing
from portfolio_builder.market import QuestDBMarket
from portfolio_builder.settings import Settings
from portfolio_builder.stopwatch import Stopwatch
from portfolio_builder.tools.graph.tools import graph_tools
from portfolio_builder.tools.news.tools import news_tools
from portfolio_builder.tools.submit import submit_tool
from portfolio_builder.tools.technicals import technicals_tool


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, service_name="portfolio-builder")
    log = bind_logger(logging.getLogger("portfolio_builder"), run_id=str(uuid.uuid4()))
    stopwatch = Stopwatch.start()
    engine = sa.create_engine(settings.postgres_dsn)
    try:
        log(
            "run_start",
            provider="openrouter",
            model=settings.llm_model,
            reasoning_level=settings.thinking_level,
            max_turns=settings.max_turns,
            news_window_days=settings.news_window_days,
        )
        briefing = load_briefing(engine, settings.news_window_days)
        log(
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
        log("prompt", system_prompt=SYSTEM_PROMPT, briefing=briefing.text)
        model = ChatOpenRouter(
            model=settings.llm_model,
            api_key=settings.openrouter_api_key,
            reasoning={"effort": settings.thinking_level},
        )
        tools = [
            *news_tools(engine),
            *graph_tools(engine),
            technicals_tool(engine, QuestDBMarket(settings.questdb_conf)),
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
    except Exception as error:
        log(
            "run_end",
            logging.ERROR,
            outcome="error",
            error=f"{type(error).__name__}: {error}",
            elapsed_ms=stopwatch.elapsed_ms,
        )
        raise SystemExit(1) from error
    finally:
        engine.dispose()

    saved = result.outcome == "saved"
    log(
        "run_end",
        logging.INFO if saved else logging.ERROR,
        outcome=result.outcome,
        portfolio_id=result.portfolio_id,
        turns=result.turns,
        usage=result.usage,
        error=result.error,
        elapsed_ms=stopwatch.elapsed_ms,
    )
    raise SystemExit(0 if saved else 1)


if __name__ == "__main__":
    main()
