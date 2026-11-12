import json

import httpx
import pytest
from portfolio_builder.competition.canonical import JevError, ask, classify_additional_request
from pydantic import SecretStr

BASE_URI = "https://jev.example/api/v1"
MODEL = "typesafe/jev-1.14"


@pytest.fixture
def server():
    requests = []
    responses = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        status, body = responses.pop(0)
        return httpx.Response(status, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return requests, responses, client


def test_posts_model_state_and_questions_to_systemone(server):
    requests, responses, client = server
    responses.append((200, {"answers": {"q": {"type": "noul", "noul": 0.9}}}))

    ask(
        BASE_URI,
        MODEL,
        SecretStr("sk"),
        "반도체 위주로 담아줘",
        {"q": {"type": "noul", "instructions": "x"}},
        client=client,
    )

    request = requests[0]
    assert str(request.url) == f"{BASE_URI}/systemone"
    assert request.method == "POST"
    assert json.loads(request.content) == {
        "model": MODEL,
        "state": "반도체 위주로 담아줘",
        "questions": {"q": {"type": "noul", "instructions": "x"}},
    }


def test_strips_a_trailing_slash_from_the_base_uri(server):
    requests, responses, client = server
    responses.append((200, {"answers": {"q": {"type": "noul", "noul": 0.9}}}))

    ask(
        BASE_URI + "/",
        MODEL,
        SecretStr("sk"),
        "x",
        {"q": {"type": "noul", "instructions": "x"}},
        client=client,
    )

    assert str(requests[0].url) == f"{BASE_URI}/systemone"


def test_sends_the_api_key_as_a_bearer_token(server):
    requests, responses, client = server
    responses.append((200, {"answers": {"q": {"type": "noul", "noul": 0.9}}}))

    ask(
        BASE_URI,
        MODEL,
        SecretStr("secret"),
        "x",
        {"q": {"type": "noul", "instructions": "x"}},
        client=client,
    )

    assert requests[0].headers["authorization"] == "Bearer secret"


def test_returns_the_raw_answers_dict(server):
    _, responses, client = server
    responses.append(
        (
            200,
            {
                "answers": {
                    "q1": {"type": "noul", "noul": 0.42},
                    "q2": {
                        "type": "choice",
                        "choice": "550",
                        "probabilities": {"550": 0.8},
                        "confidence": 0.8,
                    },
                }
            },
        )
    )

    answers = ask(
        BASE_URI,
        MODEL,
        SecretStr("sk"),
        "x",
        {
            "q1": {"type": "noul", "instructions": "a"},
            "q2": {"type": "choice", "instructions": "b", "criteria": {"550": "반도체"}},
        },
        client=client,
    )

    assert answers["q1"]["noul"] == 0.42
    assert answers["q2"]["choice"] == "550"
    assert answers["q2"]["confidence"] == 0.8


def test_raises_on_an_http_error_status(server):
    _, responses, client = server
    responses.append((500, {"error": "boom"}))

    with pytest.raises(httpx.HTTPStatusError):
        ask(
            BASE_URI,
            MODEL,
            SecretStr("sk"),
            "x",
            {"q": {"type": "noul", "instructions": "a"}},
            client=client,
        )


@pytest.mark.parametrize("status", [401, 402, 403])
def test_access_failures_stop_classification_without_using_raw_text(server, status):
    _, responses, client = server
    responses.append((status, {"error": "provider response must not be logged"}))
    events = []

    class Log:
        def error(self, event, **fields):
            events.append((event, fields))

    def request(state, questions):
        return ask(BASE_URI, MODEL, SecretStr("jev-key"), state, questions, client=client)

    with pytest.raises(JevError):
        classify_additional_request(None, request, MODEL, 0.5, 0.6, "원문", (), Log())

    assert events == [
        (
            "jev_access_failed",
            {
                "status_code": status,
                "reason": "insufficient_credit" if status == 402 else "authentication_failed",
            },
        )
    ]


def test_raises_when_the_response_has_no_answers_object(server):
    _, responses, client = server
    responses.append((200, {"oops": True}))

    with pytest.raises(JevError):
        ask(
            BASE_URI,
            MODEL,
            SecretStr("sk"),
            "x",
            {"q": {"type": "noul", "instructions": "a"}},
            client=client,
        )


def test_raises_when_an_answer_is_missing_for_a_question(server):
    _, responses, client = server
    responses.append((200, {"answers": {}}))

    with pytest.raises(JevError):
        ask(
            BASE_URI,
            MODEL,
            SecretStr("sk"),
            "x",
            {"q": {"type": "noul", "instructions": "a"}},
            client=client,
        )


def test_raises_when_a_noul_answer_has_no_probability(server):
    _, responses, client = server
    responses.append((200, {"answers": {"q": {"type": "noul"}}}))

    with pytest.raises(JevError):
        ask(
            BASE_URI,
            MODEL,
            SecretStr("sk"),
            "x",
            {"q": {"type": "noul", "instructions": "a"}},
            client=client,
        )


def test_raises_when_a_choice_answer_is_missing_confidence(server):
    _, responses, client = server
    responses.append((200, {"answers": {"q": {"type": "choice", "choice": "550"}}}))

    with pytest.raises(JevError):
        ask(
            BASE_URI,
            MODEL,
            SecretStr("sk"),
            "x",
            {"q": {"type": "choice", "instructions": "a", "criteria": {"550": "x"}}},
            client=client,
        )


def test_raises_when_a_noul_answer_is_a_bool(server):
    _, responses, client = server
    responses.append((200, {"answers": {"q": {"type": "noul", "noul": True}}}))

    with pytest.raises(JevError):
        ask(
            BASE_URI,
            MODEL,
            SecretStr("sk"),
            "x",
            {"q": {"type": "noul", "instructions": "a"}},
            client=client,
        )


def test_raises_when_a_choice_confidence_is_a_bool(server):
    _, responses, client = server
    responses.append(
        (200, {"answers": {"q": {"type": "choice", "choice": "550", "confidence": True}}})
    )

    with pytest.raises(JevError):
        ask(
            BASE_URI,
            MODEL,
            SecretStr("sk"),
            "x",
            {"q": {"type": "choice", "instructions": "a", "criteria": {"550": "x"}}},
            client=client,
        )


def test_raises_when_an_answer_type_does_not_match_the_question(server):
    _, responses, client = server
    responses.append((200, {"answers": {"q": {"type": "choice", "choice": "x", "confidence": 1}}}))

    with pytest.raises(JevError):
        ask(
            BASE_URI,
            MODEL,
            SecretStr("sk"),
            "x",
            {"q": {"type": "noul", "instructions": "a"}},
            client=client,
        )


def test_raises_with_no_questions_at_all(server):
    _, _, client = server

    with pytest.raises(JevError):
        ask(BASE_URI, MODEL, SecretStr("sk"), "x", {}, client=client)
