import base64
import hashlib
import hmac
import json
import time
from http.cookies import SimpleCookie
from typing import Any


class BackendAuth:
    def __init__(self, client: Any, secret: str, issuer: str) -> None:
        self._client = client
        self._secret = secret
        self._issuer = issuer
        self._csrf: tuple[str, str, str] | None = None

    def _token(self, subject: str) -> str:
        issued = int(time.time())
        header = self._encode({"alg": "HS256", "typ": "JWT"})
        payload = self._encode(
            {
                "iss": self._issuer,
                "sub": subject,
                "type": "access",
                "actor": "AI",
                "iat": issued,
                "exp": issued + 300,
            }
        )
        signing_input = f"{header}.{payload}"
        signature = hmac.new(self._secret.encode(), signing_input.encode(), hashlib.sha256).digest()
        return f"{signing_input}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"

    @staticmethod
    def _encode(value: dict[str, object]) -> str:
        encoded = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode()

    def get(self, path: str, subject: str) -> Any:
        response = self._client.get(
            path,
            headers={"Cookie": f"access_token={self._token(subject)}"},
        )
        response.raise_for_status()
        return response

    def _fresh_csrf(self) -> tuple[str, str, str]:
        self._client.cookies.delete("XSRF-TOKEN")
        response = self._client.get("/api/v1/auth/csrf")
        response.raise_for_status()
        cookie = SimpleCookie()
        for header in response.headers.get_list("set-cookie"):
            cookie.load(header)
        body = response.json()
        self._csrf = (cookie["XSRF-TOKEN"].value, body["header_name"], body["token"])
        return self._csrf

    def request(
        self, method: str, path: str, subject: str, json: dict[str, object] | None = None
    ) -> Any:
        csrf = self._csrf or self._fresh_csrf()
        response: Any = None
        for attempt in range(2):
            cookie, header, token = csrf
            response = self._client.request(
                method,
                path,
                json=json,
                headers={
                    "Cookie": f"access_token={self._token(subject)}; XSRF-TOKEN={cookie}",
                    header: token,
                },
            )
            if attempt or response.status_code != 403 or "INVALID_CSRF_TOKEN" not in response.text:
                break
            csrf = self._fresh_csrf()
        assert response is not None
        response.raise_for_status()
        return response
