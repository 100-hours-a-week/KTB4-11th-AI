from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class MarketEvent:
    data: str
    received_at: float
    received: datetime
    payload_bytes: int = 0
