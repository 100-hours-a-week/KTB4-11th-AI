import functools
import uuid
from datetime import UTC, datetime

import httpx
import sqlalchemy as sa
from ktb_core.logging import get_logger, start_logging
from langchain_openrouter import ChatOpenRouter

from portfolio_builder.competition.accounts import active_participants
from portfolio_builder.competition.generation import generate_scheduled_portfolios
from portfolio_builder.settings import Settings


def run_scheduled(settings: Settings) -> bool:
    log = get_logger(__name__, run_id=str(uuid.uuid4()))
    with start_logging(log, phase="scheduled_participants") as end:
        secret = settings.backend_jwt_secret
        if secret is None:
            end.update(outcome="missing_backend_configuration")
            return False
        try:
            with httpx.Client(base_url=settings.backend_base_uri, timeout=10.0) as client:
                active = active_participants(
                    client, secret.get_secret_value(), settings.backend_jwt_issuer
                )
        except (httpx.HTTPError, ValueError) as error:
            log.error("participant_snapshot_failed", error_type=type(error).__name__)
            end.update(outcome="participant_snapshot_failed")
            return False
        engine = sa.create_engine(settings.postgres_dsn)
        model_factory = functools.partial(
            ChatOpenRouter, model=settings.llm_model, api_key=settings.llm_api_key
        )
        try:
            succeeded = generate_scheduled_portfolios(
                engine, model_factory, settings, log, active, datetime.now(UTC)
            )
        except Exception as error:
            log.exception("scheduled_generation_failed", error_type=type(error).__name__)
            end.update(outcome="scheduled_generation_failed")
            return False
        finally:
            engine.dispose()
        end.update(outcome="generated" if succeeded else "failed", active_participants=len(active))
        return succeeded
