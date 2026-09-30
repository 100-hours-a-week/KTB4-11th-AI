from dataclasses import dataclass


@dataclass(frozen=True)
class AccountState:
    account_id: int
    cash: float
    held: dict[str, int]
