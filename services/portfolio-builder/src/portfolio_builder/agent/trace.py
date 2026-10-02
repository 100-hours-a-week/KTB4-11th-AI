from typing import Any, Literal

from pydantic import BaseModel


class TraceEntry(BaseModel):
    turn: int
    kind: Literal["model", "tool"]
    reasoning: str | None = None
    text: str | None = None
    name: str | None = None
    args: dict[str, Any] | None = None
    result: str | None = None
