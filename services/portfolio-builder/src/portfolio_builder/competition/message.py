from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field, StrictInt

MAX_ADDITIONAL_REQUEST_CHARS = 4000
MAX_REQUEST_ID_CHARS = 128
MAX_AGENTS = 16


def _null_as_empty(value: object) -> object:
    return "" if value is None else value


AdditionalRequest = Annotated[
    str,
    BeforeValidator(_null_as_empty),
    Field(max_length=MAX_ADDITIONAL_REQUEST_CHARS),
]

RequestId = Annotated[
    str,
    BeforeValidator(_null_as_empty),
    Field(max_length=MAX_REQUEST_ID_CHARS),
]

RISK_LEVEL_TO_PROFILE: dict[int, str] = {
    1: "안정형",
    2: "안정추구형",
    3: "위험중립형",
    4: "성장추구형",
    5: "성장형",
}


class AgentSettings(BaseModel):
    agent_id: Annotated[StrictInt, Field(gt=0)]
    participant_id: Annotated[StrictInt, Field(gt=0)]
    risk_level: Annotated[StrictInt, Field(ge=1, le=5)]
    preferred_themes: list[str] = Field(default_factory=list)
    excluded_themes: list[str] = Field(default_factory=list)
    additional_request: AdditionalRequest = ""

    @property
    def risk_profile(self) -> str:
        return RISK_LEVEL_TO_PROFILE[self.risk_level]


class Request(BaseModel):
    request_id: RequestId = ""
    competition_id: Annotated[StrictInt, Field(gt=0)]
    user_id: Annotated[StrictInt, Field(gt=0)]
    agents: Annotated[list[AgentSettings], Field(min_length=1, max_length=MAX_AGENTS)]
