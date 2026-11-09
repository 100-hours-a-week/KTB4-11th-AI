from collections import Counter
from uuid import UUID

import sqlalchemy as sa
from ktb_core.logging import StructuredLogger
from pydantic import BaseModel
from sqlalchemy.exc import MultipleResultsFound, NoResultFound

from portfolio_rebalancer.snapshot import Account, User


class MissingParticipantSchema(ValueError):
    pass


class Reasoning(BaseModel):
    label: str
    body: str


class Explanation(BaseModel):
    id: UUID
    reason: str
    reasonings: list[Reasoning]


class Target(BaseModel):
    stock_code: str
    weight: float
    exiting: bool
    buy: Explanation | None
    sell: Explanation


class Portfolio(BaseModel):
    id: int
    cash_weight: float
    targets: list[Target]
    leftovers: dict[str, Explanation]
    names: dict[str, str]


class Unready(BaseModel):
    id: int
    status: str


LATEST = (
    "SELECT id, status FROM portfolios"
    " WHERE NOT EXISTS ("
    "  SELECT 1 FROM competition_portfolios cp WHERE cp.portfolio_id = portfolios.id"
    " )"
    " ORDER BY id DESC LIMIT 1"
)

CASH_WEIGHT = "SELECT cash_weight FROM portfolios WHERE id = :id"

TARGETS = """
SELECT c.stock_code, c.name, h.weight, false AS exiting
FROM portfolio_holdings h JOIN corporations c ON c.corp_code = h.company_id
WHERE h.portfolio_id = :id
UNION ALL
SELECT c.stock_code, c.name, 0.0, true
FROM portfolio_exits e JOIN corporations c ON c.corp_code = e.company_id
WHERE e.portfolio_id = :id
"""

REASONS = """
SELECT c.stock_code, r.side, r.id, r.reason, r.reasonings
FROM portfolio_reasons r JOIN corporations c ON c.corp_code = r.company_id
WHERE r.portfolio_id = :id
"""

LEGACY_LATEST = "SELECT id, status FROM portfolios ORDER BY id DESC LIMIT 1"

LEGACY_LEFTOVERS = """
SELECT DISTINCT ON (c.stock_code) c.stock_code, c.name, r.id, r.reason, r.reasonings
FROM portfolio_reasons r
JOIN portfolio_exits e ON e.portfolio_id = r.portfolio_id AND e.company_id = r.company_id
JOIN corporations c ON c.corp_code = r.company_id
WHERE r.side = 'sell' AND r.portfolio_id <= :id
ORDER BY c.stock_code, r.portfolio_id DESC
"""

LEFTOVERS = """
SELECT DISTINCT ON (c.stock_code) c.stock_code, c.name, r.id, r.reason, r.reasonings
FROM portfolio_reasons r
JOIN portfolio_exits e ON e.portfolio_id = r.portfolio_id AND e.company_id = r.company_id
JOIN corporations c ON c.corp_code = r.company_id
WHERE r.side = 'sell' AND r.portfolio_id <= :id
AND NOT EXISTS (
  SELECT 1 FROM competition_portfolios cp WHERE cp.portfolio_id = r.portfolio_id
)
ORDER BY c.stock_code, r.portfolio_id DESC
"""


PARTICIPANT_LEFTOVERS = """
SELECT DISTINCT ON (c.stock_code) c.stock_code, c.name, r.id, r.reason, r.reasonings
FROM portfolio_reasons r
JOIN portfolio_exits e ON e.portfolio_id = r.portfolio_id AND e.company_id = r.company_id
JOIN corporations c ON c.corp_code = r.company_id
JOIN competition_portfolios cp ON cp.portfolio_id = r.portfolio_id
JOIN competition_participants p ON p.participant_id = cp.participant_id
WHERE r.side = 'sell' AND r.portfolio_id <= :id
  AND p.participant_id = :participant_id AND p.user_id = :user_id
ORDER BY c.stock_code, r.portfolio_id DESC
"""


def load_portfolio(
    engine: sa.Engine, user_id: int | None = None, participant_id: int | None = None
) -> Portfolio | Unready | None:
    with engine.connect() as conn:
        has_links = conn.scalar(sa.text("SELECT to_regclass('competition_portfolios')")) is not None
        params = {"user_id": user_id, "participant_id": participant_id}
        leftovers_sql = LEFTOVERS if has_links else LEGACY_LEFTOVERS
        latest_sql = LATEST if has_links else LEGACY_LATEST
        if participant_id is not None:
            if (
                not has_links
                or conn.scalar(sa.text("SELECT to_regclass('competition_participants')")) is None
            ):
                raise MissingParticipantSchema("participant schema unavailable")
            leftovers_sql = PARTICIPANT_LEFTOVERS
            latest_sql = """
SELECT f.id, f.status FROM portfolios f
JOIN competition_portfolios cp ON cp.portfolio_id = f.id
JOIN competition_participants p ON p.participant_id = cp.participant_id
WHERE p.participant_id = :participant_id AND p.user_id = :user_id
ORDER BY f.id DESC LIMIT 1
"""
        elif has_links:
            latest_sql = """
SELECT id, status FROM portfolios f
WHERE NOT EXISTS (SELECT 1 FROM competition_portfolios cp WHERE cp.portfolio_id = f.id)
ORDER BY id DESC LIMIT 1
"""
            leftovers_sql = LEFTOVERS.replace(
                "ORDER BY",
                "AND NOT EXISTS (SELECT 1 FROM competition_portfolios cp"
                " WHERE cp.portfolio_id = r.portfolio_id)\nORDER BY",
            )
        latest = conn.execute(sa.text(latest_sql), params).first()
        if latest is None:
            return None
        if latest.status != "ready":
            return Unready(id=latest.id, status=latest.status)
        portfolio_id = latest.id
        cash_weight = conn.execute(sa.text(CASH_WEIGHT), {"id": portfolio_id}).scalar_one()
        reasons = {
            (row.stock_code, row.side): Explanation(
                id=row.id, reason=row.reason, reasonings=row.reasonings
            )
            for row in conn.execute(sa.text(REASONS), {"id": portfolio_id})
        }
        target_rows = conn.execute(sa.text(TARGETS), {"id": portfolio_id}).all()
        leftover_rows = conn.execute(sa.text(leftovers_sql), {**params, "id": portfolio_id}).all()
    targets = [
        Target(
            stock_code=row.stock_code,
            weight=row.weight,
            exiting=row.exiting,
            buy=None if row.exiting else reasons[(row.stock_code, "buy")],
            sell=reasons[(row.stock_code, "sell")],
        )
        for row in target_rows
    ]
    leftovers = {
        row.stock_code: Explanation(id=row.id, reason=row.reason, reasonings=row.reasonings)
        for row in leftover_rows
    }
    names = {row.stock_code: row.name for row in [*target_rows, *leftover_rows]}
    return Portfolio(
        id=portfolio_id,
        cash_weight=cash_weight,
        targets=targets,
        leftovers=leftovers,
        names=names,
    )


def select_portfolios(
    engine: sa.Engine,
    users: list[User],
    common: Portfolio | Unready | None,
    log: StructuredLogger,
    counts: Counter[str],
) -> list[tuple[int, Account, Portfolio]]:
    account_counts = Counter(a.account_id for u in users for a in u.accounts)
    participant_counts = Counter(
        a.participant_id
        for u in users
        for a in u.accounts
        if a.is_active and a.participant_id is not None
    )
    selected: list[tuple[int, Account, Portfolio]] = []
    for user in users:
        for account in user.accounts:
            fields = {
                "user_id": user.user_id,
                "account_id": account.account_id,
                "participant_id": account.participant_id,
            }
            reason = None
            chosen = common
            if account_counts[account.account_id] > 1:
                reason = "duplicate_account_id"
            elif not account.is_active:
                reason = "inactive"
            elif account._participant_id_invalid:
                reason = "participant_id_invalid"
                chosen = None
            elif "participant_id" not in account.model_fields_set:
                reason = "participant_id_missing"
            elif account.participant_id is not None:
                if participant_counts[account.participant_id] > 1:
                    reason = "duplicate_participant_id"
                else:
                    try:
                        chosen = load_portfolio(engine, user.user_id, account.participant_id)
                    except (
                        KeyError,
                        ValueError,
                        TypeError,
                        NoResultFound,
                        MultipleResultsFound,
                    ) as error:
                        counts["portfolio_failed"] += 1
                        log.error(
                            "portfolio_load_failed", **fields, error_type=type(error).__name__
                        )
                        continue
            if reason is None and not isinstance(chosen, Portfolio):
                reason = chosen.status if isinstance(chosen, Unready) else "no_portfolio"
            if reason is not None:
                if reason in {
                    "participant_id_missing",
                    "participant_id_invalid",
                    "duplicate_account_id",
                    "duplicate_participant_id",
                }:
                    counts["identity_failed"] += 1
                if reason == "explanation_failed":
                    counts["explanation_failed"] += 1
                log.info(
                    "account_skipped",
                    **fields,
                    reason=reason,
                    portfolio_id=chosen.id if chosen is not None else None,
                )
                continue
            assert isinstance(chosen, Portfolio)
            selected.append((user.user_id, account, chosen))
            log.info("portfolio_selected", **fields, portfolio_id=chosen.id)
    return selected
