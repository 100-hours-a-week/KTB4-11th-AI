import sqlalchemy as sa
from ktb_core.logging import StructuredLogger

from portfolio_builder.competition.backend import Backend
from portfolio_builder.competition.repository import mark_notified, unnotified_finished_jobs


def notify_finished_jobs(engine: sa.Engine, backend: Backend, log: StructuredLogger) -> None:
    for job in unnotified_finished_jobs(engine):
        delivered = True
        for participant in job.participants:
            try:
                if job.status == "succeeded" and job.portfolio_id is not None:
                    backend.completed(participant, job.portfolio_id)
                else:
                    backend.failed(participant, job.error or "")
            except Exception as error:
                log.error(
                    "notify_failed",
                    participant_id=participant.participant_id,
                    error_type=type(error).__name__,
                )
                delivered = False
        if delivered:
            mark_notified(engine, job.generation_key)
