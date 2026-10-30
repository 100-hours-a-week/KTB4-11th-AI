import httpx
import jwt
import pytest
from portfolio_rebalancer.backend import Backend


@pytest.mark.parametrize(
    "payload",
    [
        {"users": []},
        {"users": [{"user_id": 7, "accounts": []}]},
        {
            "users": [
                {
                    "user_id": 7,
                    "accounts": [],
                    "investment_profile": {
                        "risk_level": 2,
                        "strategy": "배당 우선",
                    },
                }
            ]
        },
    ],
)
def test_users_uses_existing_http_get_and_ai_server_auth(payload):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/api/v1/users/ai-server"
        return httpx.Response(200, json=payload)

    with httpx.Client(base_url="http://backend", transport=httpx.MockTransport(respond)) as client:
        actual = Backend(client, "s" * 32, "river-be").users()

    assert [user.model_dump(exclude_none=True) for user in actual] == payload["users"]
    cookies = dict(part.strip().split("=", 1) for part in requests[0].headers["cookie"].split(";"))
    claims = jwt.decode(cookies["access_token"], "s" * 32, algorithms=["HS256"], issuer="river-be")
    assert (claims["sub"], claims["actor"]) == ("ai-server", "AI")
