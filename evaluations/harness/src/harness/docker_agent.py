import hashlib
from collections.abc import Mapping
from dataclasses import asdict
from uuid import uuid4

import httpx
from ktb_core.krx import KST

from harness.contracts import (
    DecisionContext,
    DecisionFailed,
    InformationSession,
    PortfolioProposal,
)
from harness.information import SPEC_PATH, openapi_entry
from harness.submission import SubmissionDesk

PROMPT = """Decision time: {as_of} (KST).
Universe ({count} stocks): {universe}

Read your account with get_portfolio, research the universe with the information tools, then
call submit_portfolio exactly once with target weights for the next trading session."""


def render_prompt(context: DecisionContext) -> str:
    return PROMPT.format(
        as_of=context.as_of.astimezone(KST).isoformat(timespec="minutes"),
        count=len(context.universe),
        universe=", ".join(context.universe),
    )


class DockerAgent:
    requires: frozenset[str] = frozenset()

    def __init__(
        self,
        worker: httpx.Client,
        desk: SubmissionDesk,
        submission_url: str,
        timeout_seconds: float,
        http: httpx.Client,
    ):
        self._worker = worker
        self._desk = desk
        self._desk_spec = submission_url.rstrip("/") + SPEC_PATH
        self._timeout = timeout_seconds
        self._http = http
        self._manifest: Mapping[str, object] | None = None

    @property
    def manifest(self) -> Mapping[str, object]:
        if self._manifest is None:
            response = self._worker.get("/manifest")
            response.raise_for_status()
            self._manifest = {
                **response.json(),
                "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
                "submission": openapi_entry(self._http, self._desk_spec),
            }
        return self._manifest

    def decide(
        self, context: DecisionContext, information: InformationSession, audit: dict[str, object]
    ) -> PortfolioProposal:
        decision_id = uuid4().hex
        prompt = render_prompt(context)
        openapi = [*information.openapi, self._desk_spec]
        audit.update(decision_id=decision_id, prompt=prompt, openapi=openapi)
        run: dict[str, object] | None = None
        error = None
        self._desk.open(decision_id, context)
        try:
            response = self._worker.post(
                "/runs",
                json={
                    "decision_id": decision_id,
                    "prompt": prompt,
                    "openapi": openapi,
                    "timeout_seconds": self._timeout,
                },
                timeout=self._timeout + 60,
            )
            response.raise_for_status()
            run = response.json()
        except httpx.HTTPError as failure:
            error = f"{type(failure).__name__}: {failure}"
        finally:
            window = self._desk.close()
        audit["agent"] = run
        audit["submissions"] = [asdict(attempt) for attempt in window.attempts]
        if window.accepted is not None:
            return window.accepted
        if run is None:
            raise DecisionFailed("tool_error", f"agent worker: {error}")
        if run.get("timed_out"):
            raise DecisionFailed("timeout", f"agent did not submit within {self._timeout}s")
        invalid = [a for a in window.attempts if a.status == "invalid"]
        if invalid:
            raise DecisionFailed("invalid_submission", "; ".join(invalid[-1].errors))
        if run.get("exit_code") != 0:
            raise DecisionFailed("tool_error", f"agent exited with {run.get('exit_code')}")
        raise DecisionFailed("no_submission", "agent finished without submitting a portfolio")
