import json
import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from harness.contracts import DecisionContext, HttpTool, PortfolioProposal
from harness.validation import validate

DECISION_HEADER = "X-Decision-Id"


class HoldingIn(BaseModel):
    code: str
    weight: float
    reason: str | None = None


class SubmissionIn(BaseModel):
    holdings: list[HoldingIn]
    cash_weight: float
    commentary: str = ""


@dataclass(frozen=True)
class Attempt:
    status: Literal["accepted", "invalid", "duplicate"]
    errors: tuple[str, ...]
    payload: Any


@dataclass
class Window:
    decision_id: str
    context: DecisionContext
    attempts: list[Attempt] = field(default_factory=list)
    accepted: PortfolioProposal | None = None


class SubmissionDesk:
    def __init__(self):
        self._lock = threading.Lock()
        self._window: Window | None = None

    def open(self, decision_id: str, context: DecisionContext) -> None:
        with self._lock:
            if self._window is not None:
                raise RuntimeError(f"decision {self._window.decision_id} is still open")
            self._window = Window(decision_id, context)

    def close(self) -> Window:
        with self._lock:
            window, self._window = self._window, None
        if window is None:
            raise RuntimeError("no decision is open")
        return window

    def context(self, decision_id: str | None) -> DecisionContext | None:
        with self._lock:
            window = self._window
            return window.context if window and window.decision_id == decision_id else None

    def submit(self, decision_id: str | None, payload: Any) -> tuple[int, dict[str, object]]:
        with self._lock:
            window = self._window
            if window is None or window.decision_id != decision_id:
                return 409, {"status": "closed", "errors": ["no open decision has this id"]}
            if window.accepted is not None:
                errors = ("a portfolio was already accepted for this decision",)
                window.attempts.append(Attempt("duplicate", errors, payload))
                return 409, {"status": "duplicate", "errors": list(errors)}
            proposal, errors = _parse(payload, window.context.universe)
            if proposal is None:
                window.attempts.append(Attempt("invalid", errors, payload))
                return 422, {"status": "invalid", "errors": list(errors)}
            window.accepted = proposal
            window.attempts.append(Attempt("accepted", (), payload))
            return 200, {"status": "accepted"}


def _parse(
    payload: Any, universe: tuple[str, ...]
) -> tuple[PortfolioProposal | None, tuple[str, ...]]:
    try:
        submission = SubmissionIn.model_validate(payload)
    except ValidationError as error:
        return None, tuple(
            f"{'.'.join(map(str, e['loc'])) or 'body'}: {e['msg']}" for e in error.errors()
        )
    counts = Counter(holding.code for holding in submission.holdings)
    errors = [f"{code}: appears more than once" for code, n in sorted(counts.items()) if n > 1]
    proposal = PortfolioProposal(
        {holding.code: holding.weight for holding in submission.holdings},
        submission.cash_weight,
    )
    errors.extend(validate(proposal, universe))
    return (None, tuple(errors)) if errors else (proposal, ())


def submission_tools(base_url: str) -> tuple[HttpTool, ...]:
    base = base_url.rstrip("/")
    return (
        HttpTool(
            "get_portfolio",
            "Your account at the current decision time: decision time, universe, cash, share"
            " quantities held, and pending order target values. No prices.",
            "GET",
            f"{base}/portfolio",
        ),
        HttpTool(
            "submit_portfolio",
            "Submit the target portfolio once. Weights are relative and non-negative; the"
            " harness scales holdings and cash_weight to sum to 1. Held stocks you omit are"
            " sold. Invalid submissions return every error; fix them and submit again.",
            "POST",
            f"{base}/portfolio",
            {
                "holdings": {
                    "type": "array",
                    "description": "target holdings",
                    "items": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string", "description": "6-character stock code"},
                            "weight": {"type": "number", "description": "relative weight >= 0"},
                            "reason": {"type": "string", "description": "why it is held"},
                        },
                        "required": ["code", "weight"],
                    },
                },
                "cash_weight": {"type": "number", "description": "relative cash weight >= 0"},
                "commentary": {"type": "string", "description": "overall reasoning"},
            },
            ("holdings", "cash_weight"),
        ),
    )


def create_submission_app(desk: SubmissionDesk) -> FastAPI:
    app = FastAPI(title="harness-submission", docs_url=None, redoc_url=None)
    DecisionId = Annotated[str | None, Header(alias=DECISION_HEADER)]

    @app.get("/portfolio")
    def get_portfolio(decision_id: DecisionId = None) -> JSONResponse:
        context = desk.context(decision_id)
        if context is None:
            return JSONResponse({"errors": ["no open decision has this id"]}, 409)
        return JSONResponse(
            {
                "as_of": context.as_of.isoformat(),
                "universe": list(context.universe),
                "cash": context.state.cash,
                "holdings": dict(context.state.holdings),
                "pending_order_values": dict(context.state.pending),
            }
        )

    @app.post("/portfolio")
    async def submit_portfolio(request: Request, decision_id: DecisionId = None) -> JSONResponse:
        try:
            payload = json.loads(await request.body())
        except ValueError:
            payload = None
        status, body = desk.submit(decision_id, payload)
        return JSONResponse(body, status)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
