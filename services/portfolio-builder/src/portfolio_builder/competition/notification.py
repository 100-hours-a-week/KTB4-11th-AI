import sqlalchemy as sa
from ktb_core.logging import StructuredLogger

from portfolio_builder.competition.backend import Backend
from portfolio_builder.competition.repository import mark_notified, unnotified_notifications


def notify_finished_jobs(engine: sa.Engine, backend: Backend, log: StructuredLogger) -> None:
    for notification in unnotified_notifications(engine):
        try:
            if notification.portfolio_id is not None:
                backend.completed(notification.participant, notification.portfolio_id)
            else:
                backend.failed(notification.participant, notification.error or "")
        except Exception as error:
            log.error(
                "notify_failed",
                participant_id=notification.participant.participant_id,
                error_type=type(error).__name__,
            )
            continue
        mark_notified(engine, notification)
