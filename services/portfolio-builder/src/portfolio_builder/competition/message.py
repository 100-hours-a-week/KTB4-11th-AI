from typing import Annotated, Literal

from pydantic import BaseModel, Field, StrictInt

RiskProfile = Literal["성장형", "성장추구형", "위험중립형", "안정추구형", "안정형"]

MAX_ADDITIONAL_REQUEST_CHARS = 4000


class Request(BaseModel):
    competition_id: Annotated[StrictInt, Field(gt=0)]
    participant_id: Annotated[StrictInt, Field(gt=0)]
    user_id: Annotated[StrictInt, Field(gt=0)]
    risk_profile: RiskProfile
    interested_theme_codes: list[str] = Field(default_factory=list)
    excluded_theme_codes: list[str] = Field(default_factory=list)
    additional_request: str = Field(default="", max_length=MAX_ADDITIONAL_REQUEST_CHARS)

    @property
    def effective_interested(self) -> list[str]:
        excluded_set = set(self.excluded_theme_codes)
        return sorted(set(code for code in self.interested_theme_codes if code not in excluded_set))

    @property
    def effective_excluded(self) -> list[str]:
        return sorted(set(self.excluded_theme_codes))
