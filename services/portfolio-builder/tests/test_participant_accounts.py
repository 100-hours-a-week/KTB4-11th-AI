import httpx
import jwt
import pytest
from portfolio_builder.competition.accounts import active_participants


def account(account_id=11, participant_id=1, active=True):
    return {"account_id": account_id, "participant_id": participant_id, "is_active": active}


def lookup(accounts, user_id=100):
    payload = {"users": [{"user_id": user_id, "accounts": accounts}]}
    with httpx.Client(
        base_url="https://backend",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    ) as client:
        return active_participants(client, "s" * 32, "issuer")


def test_only_active_participant_accounts_are_returned():
    assert lookup([account(), account(12, None), account(13, 2, False)]) == {1: 100}


def test_lookup_uses_the_existing_cookie_authenticated_endpoint():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"users": []})

    with httpx.Client(base_url="https://backend", transport=httpx.MockTransport(respond)) as client:
        assert active_participants(client, "s" * 32, "issuer") == {}

    assert requests[0].url.path == "/api/v1/users/ai-server"
    token = requests[0].headers["cookie"].split("=", 1)[1]
    claims = jwt.decode(token, "s" * 32, algorithms=["HS256"], issuer="issuer")
    assert (claims["sub"], claims["actor"]) == ("ai-server", "AI")


@pytest.mark.parametrize("participant_id", [0, -1, True, "1", 1.5])
def test_invalid_participant_identity_is_rejected(participant_id):
    with pytest.raises(ValueError):
        lookup([account(participant_id=participant_id)])


def test_missing_participant_identity_is_not_interpreted_as_common():
    with pytest.raises(ValueError):
        lookup([{"account_id": 1, "is_active": True}])


@pytest.mark.parametrize("accounts", [[account(), account(12)], [account(), account(11, 2)]])
def test_duplicate_participant_or_account_identity_is_rejected(accounts):
    with pytest.raises(ValueError):
        lookup(accounts)
