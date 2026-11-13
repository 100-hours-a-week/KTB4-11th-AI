import json
import logging
from socketserver import BaseRequestHandler, TCPServer
from threading import Thread
from uuid import uuid4

import httpx
import pytest
from ktb_core.logging import JsonFormatter, get_logger
from portfolio_builder.competition.canonical import JevError, ask, classify_additional_request
from portfolio_builder.settings import CompetitionSettings
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


@pytest.fixture
def local_endpoint():
    class Handler(BaseRequestHandler):
        def handle(self):
            self.request.recv(65536)

    with TCPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}/v1"
        finally:
            server.shutdown()
            thread.join()


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


@pytest.mark.parametrize("status", [401, 402, 403, 429, 500])
def test_access_failures_stop_classification_and_log_the_http_exception(server, status, caplog):
    _, responses, client = server
    responses.append((status, {"error": "provider response must not be logged"}))
    key = SecretStr("jev-key")

    def request(state, questions):
        return ask(BASE_URI, MODEL, key, state, questions, client=client)

    with pytest.raises(httpx.HTTPStatusError):
        classify_additional_request(
            None, request, MODEL, 0.5, 0.6, "원문", (), get_logger(__name__)
        )

    record = next(r for r in caplog.records if r.message == "canonicalisation_failed")
    output = JsonFormatter("competition-portfolio-builder").format(record)
    logged = json.loads(output)
    assert logged["error_type"] == "HTTPStatusError"
    assert "Traceback" in logged["exception"]
    assert str(status) in logged["exception"]
    assert "jev-key" not in output
    assert "provider response must not be logged" not in output


def test_classification_failure_logs_the_exception_and_propagates(caplog):
    def request(state, questions):
        raise RuntimeError("Jev temporarily unavailable")

    with pytest.raises(RuntimeError, match="Jev temporarily unavailable"):
        classify_additional_request(
            None, request, MODEL, 0.5, 0.6, "원문", (), get_logger(__name__)
        )
    record = next(r for r in caplog.records if r.message == "canonicalisation_failed")
    logged = json.loads(JsonFormatter("competition-portfolio-builder").format(record))
    assert logged["error_type"] == "RuntimeError"
    assert "RuntimeError: Jev temporarily unavailable" in logged["exception"]


def test_path_credentials_are_masked_in_httpx_and_exception_logs(server, caplog):
    _, responses, client = server
    marker = uuid4().hex
    settings = CompetitionSettings(
        queue_url="https://sqs.example/settings",
        jev_api_key="ordinary-credential",
        jev_base_uri=f"https://jev.example/{marker}/api/v1",
        jev_model=MODEL,
        jev_timeout=20,
        jev_noul_threshold=0.5,
        jev_choice_min_confidence=0.5,
    )
    responses.append((401, {"error": "unauthorized"}))
    caplog.set_level(logging.INFO, logger="httpx")

    def request(state, questions):
        return ask(
            settings.jev_base_uri,
            settings.jev_model,
            settings.jev_api_key,
            state,
            questions,
            client=client,
        )

    with pytest.raises(httpx.HTTPStatusError):
        classify_additional_request(
            None, request, MODEL, 0.5, 0.5, "원문", (), get_logger(__name__)
        )

    formatter = JsonFormatter("competition-portfolio-builder", redact_urls=True)
    output = "\n".join(formatter.format(record) for record in caplog.records)
    assert any(record.name == "httpx" for record in caplog.records)
    assert marker not in output
    assert "401" in output
    assert "HTTPStatusError" in output
    assert "Traceback" in output


@pytest.mark.parametrize("character", ["\n", "\r", "\x00", "가"])
def test_invalid_authorization_does_not_expose_the_key_in_exception_logs(
    local_endpoint, caplog, character
):
    marker = uuid4().hex
    key = SecretStr(f"synthetic-{marker}{character}credential")
    log = get_logger(__name__)

    with httpx.Client(trust_env=False, timeout=1) as client:

        def request(state, questions):
            return ask(local_endpoint, MODEL, key, state, questions, client=client)

        with pytest.raises((httpx.LocalProtocolError, UnicodeEncodeError)):
            classify_additional_request(None, request, MODEL, 0.5, 0.6, "원문", (), log)

    record = next(r for r in caplog.records if r.message == "canonicalisation_failed")
    output = JsonFormatter(
        "competition-portfolio-builder", sensitive_values=(key.get_secret_value(),)
    ).format(record)
    assert marker not in output
    assert "Traceback" in json.loads(output)["exception"]


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
