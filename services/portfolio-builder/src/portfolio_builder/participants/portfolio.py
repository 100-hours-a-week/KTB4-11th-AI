import math
from collections.abc import Sequence

import sqlalchemy as sa
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert

from portfolio_builder.news_client import NewsClient
from portfolio_builder.participants.database import competition_portfolios
from portfolio_builder.portfolio import (
    Submission,
    insert_portfolio,
    normalize_weights,
    validate_citations,
)
from portfolio_builder.portfolio import (
    validate_portfolio as validate_common_portfolio,
)


class Constraints(BaseModel):
    max_holdings: int | None = None
    max_weight_per_holding: float | None = None
    min_cash_weight: float | None = None


class CompetitionSubmission(Submission):
    constraints: Constraints = Field(default_factory=Constraints)


def excluded_company_ids(engine: sa.Engine, excluded_theme_codes: Sequence[str]) -> frozenset[str]:
    if not excluded_theme_codes:
        return frozenset()
    query = sa.text(
        "SELECT corporations.corp_code FROM theme_corporations"
        " JOIN corporations ON corporations.stock_code = theme_corporations.stock_code"
        " WHERE theme_corporations.theme_code IN :theme_codes"
    ).bindparams(sa.bindparam("theme_codes", expanding=True))
    with engine.connect() as conn:
        rows = conn.execute(query, {"theme_codes": list(excluded_theme_codes)})
        return frozenset(row[0] for row in rows)


FLOAT_TOLERANCE = 1e-9


def validate_constraints(submission: CompetitionSubmission) -> list[str]:
    errors: list[str] = []
    constraints = submission.constraints
    holdings = submission.holdings
    if constraints.max_holdings is not None and len(holdings) > constraints.max_holdings:
        errors.append(
            f"constraints: {len(holdings)} holdings exceeds max_holdings {constraints.max_holdings}"
        )
    if constraints.max_weight_per_holding is not None:
        for holding in holdings:
            if holding.weight > constraints.max_weight_per_holding + FLOAT_TOLERANCE:
                errors.append(
                    f"constraints: {holding.company_id} weight {holding.weight} exceeds"
                    f" max_weight_per_holding {constraints.max_weight_per_holding}"
                )
    min_cash_weight = constraints.min_cash_weight
    if min_cash_weight is not None and submission.cash_weight < min_cash_weight - FLOAT_TOLERANCE:
        errors.append(
            f"constraints: cash_weight {submission.cash_weight} is below"
            f" min_cash_weight {constraints.min_cash_weight}"
        )
    return errors


def validate_portfolio(
    submission: CompetitionSubmission,
    previous: frozenset[str],
    excluded: frozenset[str] = frozenset(),
) -> list[str]:
    errors = validate_common_portfolio(submission, previous)
    errors.extend(
        f"holdings: {holding.company_id} is in an excluded theme"
        for holding in submission.holdings
        if holding.company_id in excluded
    )
    total = submission.cash_weight + sum(h.weight for h in submission.holdings)
    if math.isfinite(total) and total > 0:
        errors.extend(validate_constraints(normalize_weights(submission)))
    return errors


def save_portfolio(
    engine: sa.Engine,
    submission: CompetitionSubmission,
    model: str,
    news_client: NewsClient,
    participant_id: int,
) -> int:
    validate_citations(submission, news_client)
    constraints = submission.constraints.model_dump()
    acknowledged = None if all(v is None for v in constraints.values()) else constraints
    with engine.begin() as conn:
        portfolio_id = insert_portfolio(conn, submission, model, acknowledged)
        conn.execute(
            pg_insert(competition_portfolios)
            .values(participant_id=participant_id, portfolio_id=portfolio_id)
            .on_conflict_do_nothing(index_elements=["participant_id", "portfolio_id"])
        )
        return portfolio_id
