from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from market_collector.kiwoom.parse import KST
from market_collector.settings import KiwoomAccount

__all__ = ["KiwoomAuthError", "TokenStore", "Transport"]


class KiwoomAuthError(Exception):
    pass


class Transport(Protocol):
    def post(
        self, path: str, body: dict[str, object], headers: dict[str, str]
    ) -> tuple[dict[str, str], dict[str, object]]: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TokenStore:
    REFRESH_MARGIN = timedelta(minutes=5)

    def __init__(
        self,
        account: KiwoomAccount,
        transport: Transport,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._account = account
        self._transport = transport
        self._now = now
        self._token: str | None = None
        self._expires_at: datetime | None = None

    def token(self) -> str:
        if self._token is not None and self._expires_at is not None:
            if self._now() + self.REFRESH_MARGIN < self._expires_at:
                return self._token
        return self._issue()

    def _issue(self) -> str:
        _, body = self._transport.post(
            "/oauth2/token",
            {
                "grant_type": "client_credentials",
                "appkey": self._account.app_key,
                "secretkey": self._account.secret_key,
            },
            {},
        )
        if body.get("return_code") != 0:
            raise KiwoomAuthError(
                f"token request failed: return_code={body.get('return_code')} "
                f"return_msg={body.get('return_msg')}"
            )

        token = body.get("token")
        if not isinstance(token, str) or not token:
            raise KiwoomAuthError(f"token missing from response: {body!r}")

        self._token = token
        self._expires_at = self._parse_expiry(body.get("expires_dt"))
        return token

    @staticmethod
    def _parse_expiry(raw: object) -> datetime:
        if not isinstance(raw, str) or len(raw) != 14:
            raise KiwoomAuthError(f"unusable expires_dt: {raw!r}")
        naive = datetime.strptime(raw, "%Y%m%d%H%M%S")
        return naive.replace(tzinfo=KST).astimezone(UTC)
