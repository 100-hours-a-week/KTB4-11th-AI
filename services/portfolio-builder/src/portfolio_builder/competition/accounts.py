import httpx
from ktb_core.backend_auth import BackendAuth
from pydantic import BaseModel, Field


class Account(BaseModel):
    account_id: int = Field(gt=0, strict=True)
    participant_id: int | None = Field(gt=0, strict=True)
    is_active: bool


class User(BaseModel):
    user_id: int = Field(gt=0, strict=True)
    accounts: list[Account]


class Snapshot(BaseModel):
    users: list[User]


def active_participants(client: httpx.Client, secret: str, issuer: str) -> dict[int, int]:
    response = BackendAuth(client, secret, issuer).get("/api/v1/users/ai-server", "ai-server")
    snapshot = Snapshot.model_validate(response.json())
    active: dict[int, int] = {}
    accounts: set[int] = set()
    participants: set[int] = set()
    for user in snapshot.users:
        for account in user.accounts:
            if account.account_id in accounts:
                raise ValueError("duplicate account identity")
            accounts.add(account.account_id)
            if account.participant_id is None:
                continue
            if account.participant_id in participants:
                raise ValueError("duplicate participant identity")
            participants.add(account.participant_id)
            if account.is_active:
                active[account.participant_id] = user.user_id
    return active
