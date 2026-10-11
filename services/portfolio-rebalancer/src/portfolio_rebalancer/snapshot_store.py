from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa

metadata = sa.MetaData()
states = sa.Table(
    "rebalancer_account_state",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("event_id", sa.Text),
    sa.Column("occurred_at", sa.DateTime(timezone=True)),
    sa.Column("correlation_id", sa.Text),
    sa.Column("payload", sa.JSON),
    sa.Column("usable", sa.Boolean, nullable=False),
    sa.CheckConstraint("id = 1", name="rebalancer_account_state_singleton"),
)
events = sa.Table(
    "rebalancer_snapshot_events",
    metadata,
    sa.Column("event_id", sa.Text, primary_key=True),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
)


@dataclass(frozen=True)
class StoredSnapshot:
    event_id: str
    occurred_at: datetime
    correlation_id: str | None
    payload: dict
    usable: bool


class SnapshotStore:
    def __init__(self, engine: sa.Engine) -> None:
        self.engine = engine

    def load(self) -> StoredSnapshot | None:
        with self.engine.connect() as conn:
            row = conn.execute(states.select().where(states.c.id == 1)).one()
        return self._snapshot(row)

    def accept(
        self,
        event_id: str,
        occurred_at: datetime,
        payload: dict,
        correlation_id: str | None,
        *,
        valid_time: bool,
    ) -> tuple[StoredSnapshot | None, str]:
        occurred_at = occurred_at.astimezone(UTC)
        with self.engine.begin() as conn:
            row = conn.execute(states.select().where(states.c.id == 1).with_for_update()).one()
            current = self._snapshot(row)
            duplicate = conn.execute(
                sa.select(events.c.event_id).where(events.c.event_id == event_id)
            ).first()
            if duplicate is not None:
                return current, "duplicate"
            if current is not None and occurred_at <= current.occurred_at:
                conn.execute(events.insert().values(event_id=event_id, occurred_at=occurred_at))
                return current, "outdated"
            if not valid_time:
                return current, "invalid_time"
            conn.execute(events.insert().values(event_id=event_id, occurred_at=occurred_at))
            conn.execute(
                states.update()
                .where(states.c.id == 1)
                .values(
                    event_id=event_id,
                    occurred_at=occurred_at,
                    payload=payload,
                    correlation_id=correlation_id,
                    usable=True,
                )
            )
        return StoredSnapshot(event_id, occurred_at, correlation_id, payload, True), "applied"

    def claim(self, event_id: str) -> bool:
        with self.engine.begin() as conn:
            result = conn.execute(
                states.update()
                .where(states.c.id == 1, states.c.event_id == event_id, states.c.usable.is_(True))
                .values(usable=False)
            )
            return result.rowcount == 1

    @staticmethod
    def _snapshot(row) -> StoredSnapshot | None:
        if row.event_id is None:
            return None
        return StoredSnapshot(
            row.event_id,
            row.occurred_at.replace(tzinfo=UTC)
            if row.occurred_at.tzinfo is None
            else row.occurred_at,
            row.correlation_id,
            row.payload,
            row.usable,
        )
