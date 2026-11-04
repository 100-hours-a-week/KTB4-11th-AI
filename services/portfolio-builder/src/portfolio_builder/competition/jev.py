from typing import Any

import httpx
from pydantic import SecretStr

Questions = dict[str, dict[str, Any]]
Answers = dict[str, dict[str, Any]]


class JevError(Exception):
    pass


def ask(
    base_uri: str,
    model: str,
    api_key: SecretStr,
    state: str,
    questions: Questions,
    timeout: float = 20,
    client: httpx.Client | None = None,
) -> Answers:
    if not questions:
        raise JevError("no questions to ask")

    response = (client or httpx).post(
        f"{base_uri.rstrip('/')}/systemone",
        json={"model": model, "state": state, "questions": questions},
        headers={"Authorization": f"Bearer {api_key.get_secret_value()}"},
        timeout=timeout,
    )
    body = response.raise_for_status().json()
    answers = body.get("answers")
    if not isinstance(answers, dict):
        raise JevError("response has no answers object")

    missing = set(questions) - set(answers)
    if missing:
        raise JevError(f"missing answers for {sorted(missing)}")

    for name, question in questions.items():
        answer = answers[name]
        question_type = question["type"]
        if not isinstance(answer, dict) or answer.get("type") != question_type:
            raise JevError(f"{name}: unexpected answer shape")
        if question_type == "noul":
            if not isinstance(answer.get("noul"), int | float):
                raise JevError(f"{name}: noul answer missing a probability")
        elif question_type == "choice":
            if answer.get("choice") is None or not isinstance(
                answer.get("confidence"), int | float
            ):
                raise JevError(f"{name}: choice answer missing choice or confidence")
        else:
            raise JevError(f"{name}: unsupported question type {question_type!r}")

    return answers
