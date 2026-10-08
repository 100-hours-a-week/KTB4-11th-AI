import time
from http.cookies import SimpleCookie

import httpx
import jwt


class BackendAuth:
    def __init__(self, client: httpx.Client, secret: str, issuer: str) -> None:
        self._client = client
        self._secret = secret
        self._issuer = issuer
        self._csrf: tuple[str, str, str] | None = None

    def _token(self, subject: str) -> str:
        issued = int(time.time())
        return jwt.encode(
            {
                "iss": self._issuer,
                "sub": subject,
                "type": "access",
                "actor": "AI",
                "iat": issued,
                "exp": issued + 300,
            },
            self._secret,
            algorithm="HS256",
        )

    def get(self, path: str, subject: str) -> httpx.Response:
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
    ) -> httpx.Response:
        csrf = self._csrf or self._fresh_csrf()
        response: httpx.Response | None = None
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
