import math
from typing import Annotated

import sqlalchemy as sa
from pydantic import BaseModel, Field

from portfolio_builder.database import portfolio_exits, portfolio_holdings, portfolios
from portfolio_builder.errors import PortfolioRejected

# No numeric constraints here on purpose: validate_portfolio reports every problem at once,
# which a Pydantic ValidationError on the first bad field would not.
CitedClusters = Annotated[list[int], Field(description="cluster_ids this decision relies on")]


class Holding(BaseModel):
    company_id: str
    weight: float = Field(description="relative, non-negative")
    reason: str | None = Field(
        default=None, description="required for a company entering the portfolio"
    )
    cited_cluster_ids: CitedClusters


class Exit(BaseModel):
    company_id: str
    reason: str
    cited_cluster_ids: CitedClusters


class Submission(BaseModel):
    holdings: list[Holding]
    exits: list[Exit]
    cash_weight: float
    commentary: str


def validate_portfolio(submission: Submission, previous: frozenset[str]) -> list[str]:
    errors: list[str] = []
    held: set[str] = set()
    for holding in submission.holdings:
        company = holding.company_id
        if not (math.isfinite(holding.weight) and holding.weight >= 0):
            errors.append(f"holdings: {company} weight must be a finite number >= 0")
        if company not in previous and not (holding.reason or "").strip():
            errors.append(f"holdings: {company} is entering the portfolio and needs a reason")
        if company in held:
            errors.append(f"holdings: {company} appears more than once")
        held.add(company)

    exited: set[str] = set()
    for exit_ in submission.exits:
        company = exit_.company_id
        if company in exited:
            errors.append(f"exits: {company} appears more than once")
        exited.add(company)
        if company in held:
            errors.append(f"exits: {company} is both held and exited")
        if company not in previous:
            errors.append(f"exits: {company} was not in the previous portfolio")
        if not exit_.reason.strip():
            errors.append(f"exits: {company} needs a reason")
    for company in sorted(previous):
        if company not in held and company not in exited:
            errors.append(
                f"exits: {company} was held and is dropped, so it needs an exit with a reason"
            )

    if not (math.isfinite(submission.cash_weight) and submission.cash_weight >= 0):
        errors.append("cash_weight must be a finite number >= 0")
    total = sum(h.weight for h in submission.holdings) + submission.cash_weight
    if not math.isfinite(total):
        errors.append("weights and cash_weight must add up to a finite number")
    elif not total > 0:
        errors.append("weights and cash_weight sum to 0 or less; at least one must be positive")
    if not submission.commentary.strip():
        errors.append("commentary must not be empty")
    return errors


def normalize_weights(submission: Submission) -> Submission:
    total = sum(h.weight for h in submission.holdings) + submission.cash_weight
    return submission.model_copy(
        update={
            "holdings": [
                h.model_copy(update={"weight": h.weight / total}) for h in submission.holdings
            ],
            "cash_weight": submission.cash_weight / total,
        }
    )


def save_portfolio(engine: sa.Engine, submission: Submission, model: str) -> int:
    cited = sorted(
        {c for item in [*submission.holdings, *submission.exits] for c in item.cited_cluster_ids}
    )
    try:
        with engine.begin() as conn:
            portfolio_id = conn.execute(
                sa.insert(portfolios)
                .values(
                    cash_weight=submission.cash_weight,
                    commentary=submission.commentary,
                    model=model,
                )
                .returning(portfolios.c.id)
            ).scalar_one()
            if submission.holdings:
                conn.execute(
                    sa.insert(portfolio_holdings),
                    [{"portfolio_id": portfolio_id, **h.model_dump()} for h in submission.holdings],
                )
            if submission.exits:
                conn.execute(
                    sa.insert(portfolio_exits),
                    [{"portfolio_id": portfolio_id, **e.model_dump()} for e in submission.exits],
                )
            found = set(
                conn.execute(
                    sa.text("SELECT id FROM clusters WHERE id = ANY(CAST(:ids AS bigint[]))"),
                    {"ids": cited},
                ).scalars()
            )
            missing = [c for c in cited if c not in found]
            if missing:
                raise PortfolioRejected(
                    [f"cited_cluster_ids not found: {', '.join(map(str, missing))}"]
                )
            return portfolio_id
    except sa.exc.IntegrityError as error:
        diag = getattr(error.orig, "diag", None)
        detail = getattr(diag, "message_detail", None) or str(error.orig)
        raise PortfolioRejected([detail]) from error
