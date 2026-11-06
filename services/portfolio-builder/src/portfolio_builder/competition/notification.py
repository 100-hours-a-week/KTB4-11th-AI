import sqlalchemy as sa
from ktb_core.logging import StructuredLogger

from portfolio_builder.competition.backend import Backend
from portfolio_builder.competition.repository import mark_notified, unnotified_notifications
from portfolio_builder.settings import Settings

INTERRUPTED_ERROR = "generation was interrupted before it reported an outcome"


def notify_finished_jobs(
    engine: sa.Engine, backend: Backend, log: StructuredLogger, settings: Settings
) -> None:
    pending = unnotified_notifications(
        engine, settings.competition_max_attempts, settings.competition_stale_running_seconds
    )
    for notification in pending:
        try:
            if notification.portfolio_id is not None:
                backend.completed(notification.participant, notification.portfolio_id)
            else:
                backend.failed(notification.participant, notification.error or INTERRUPTED_ERROR)
        except Exception as error:
            log.error(
                "notify_failed",
                participant_id=notification.participant.participant_id,
                error_type=type(error).__name__,
            )
            continue
        mark_notified(engine, notification)
