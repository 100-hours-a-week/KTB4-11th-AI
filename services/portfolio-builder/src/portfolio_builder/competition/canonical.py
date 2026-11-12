import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
import sqlalchemy as sa
from ktb_core.logging import StructuredLogger
from pydantic import SecretStr

from portfolio_builder.competition.prompt import (
    GATE_RELEVANT_INSTRUCTIONS,
    GATE_SIGNALS_INSTRUCTIONS,
    GATE_THEMES_INSTRUCTIONS,
    MAX_CHOICE_OPTIONS,
    QUESTION_SET_VERSION,
    SECTOR_CHOICE_INSTRUCTIONS,
    SECTOR_POLARITY_INSTRUCTIONS,
    SIGNAL_INSTRUCTIONS,
    SIGNAL_NAMES,
    SIGNAL_POLARITY_CRITERIA,
    THEME_NAME_INSTRUCTIONS_SINGLE_TEMPLATE,
    THEME_NAME_INSTRUCTIONS_TEMPLATE,
    THEME_POLARITY_CRITERIA,
)

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
            noul = answer.get("noul")
            if isinstance(noul, bool) or not isinstance(noul, int | float):
                raise JevError(f"{name}: noul answer missing a probability")
        elif question_type == "choice":
            confidence = answer.get("confidence")
            if (
                answer.get("choice") is None
                or isinstance(confidence, bool)
                or not isinstance(confidence, int | float)
            ):
                raise JevError(f"{name}: choice answer missing choice or confidence")
        else:
            raise JevError(f"{name}: unsupported question type {question_type!r}")

    return answers


AskFn = Callable[[str, dict[str, dict[str, Any]]], dict[str, dict[str, Any]]]


class CanonicalisationError(Exception):
    pass


@dataclass(frozen=True)
class CanonicalForm:
    signal_polarity: dict[str, str] = field(default_factory=dict)
    theme_polarity: dict[str, str] = field(default_factory=dict)
    sector: str | None = None
    sector_polarity: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "signals": dict(sorted(self.signal_polarity.items())),
            "themes": dict(sorted(self.theme_polarity.items())),
            "sector": self.sector,
            "sector_polarity": self.sector_polarity,
        }


EMPTY_CANONICAL_FORM = CanonicalForm()


def _noul(instructions: str) -> dict[str, Any]:
    return {"type": "noul", "instructions": instructions}


def _choice(instructions: str, criteria: dict[str, str]) -> dict[str, Any]:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def _passes(answer: dict[str, Any], threshold: float) -> bool:
    return float(answer["noul"]) >= threshold


def _polarity(
    answer: dict[str, Any], options: tuple[str, str], choice_min_confidence: float
) -> str | None:
    choice = str(answer["choice"])
    if choice not in options:
        return None
    if float(answer["confidence"]) < choice_min_confidence:
        return None
    return choice


def _theme_criteria(engine: sa.Engine) -> dict[str, str]:
    with engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT theme_code, name FROM themes ORDER BY theme_code"))
        return {row[0]: row[1] for row in rows}


def _version(jev_model: str, noul_threshold: float, choice_min_confidence: float) -> str:
    return f"{jev_model}|{noul_threshold}|{choice_min_confidence}|{QUESTION_SET_VERSION}"


def _classify_signals(ask: AskFn, text: str, choice_min_confidence: float) -> dict[str, str]:
    questions = {
        name: _choice(SIGNAL_INSTRUCTIONS[name], SIGNAL_POLARITY_CRITERIA) for name in SIGNAL_NAMES
    }
    answers = ask(text, questions)
    polarity = {}
    for name in SIGNAL_NAMES:
        picked = _polarity(answers[name], ("favor", "avoid"), choice_min_confidence)
        if picked is not None:
            polarity[name] = picked
    return polarity


def _classify_themes(
    engine: sa.Engine,
    ask: AskFn,
    text: str,
    interested_theme_names: Sequence[str],
    choice_min_confidence: float,
) -> tuple[dict[str, str], str | None, str | None]:
    criteria = _theme_criteria(engine)
    if not criteria:
        return {}, None, None
    if len(criteria) > MAX_CHOICE_OPTIONS:
        raise CanonicalisationError(
            f"theme list has {len(criteria)} options, over the {MAX_CHOICE_OPTIONS} limit"
        )

    names = sorted(set(interested_theme_names))
    template = (
        THEME_NAME_INSTRUCTIONS_SINGLE_TEMPLATE
        if len(names) == 1
        else THEME_NAME_INSTRUCTIONS_TEMPLATE
    )
    questions = {
        name: _choice(template.format(name=name), THEME_POLARITY_CRITERIA) for name in names
    }
    questions["sector"] = _choice(SECTOR_CHOICE_INSTRUCTIONS, criteria)
    questions["sector_polarity"] = _choice(SECTOR_POLARITY_INSTRUCTIONS, THEME_POLARITY_CRITERIA)
    answers = ask(text, questions)

    theme_polarity = {}
    for name in names:
        picked = _polarity(answers[name], ("emphasize", "avoid"), choice_min_confidence)
        if picked is not None:
            theme_polarity[name] = picked

    sector_answer = answers["sector"]
    if float(sector_answer["confidence"]) < choice_min_confidence:
        return theme_polarity, None, None
    sector_polarity = _polarity(
        answers["sector_polarity"], ("emphasize", "avoid"), choice_min_confidence
    )
    return theme_polarity, str(sector_answer["choice"]), sector_polarity


def _classify_text(
    engine: sa.Engine,
    ask: AskFn,
    text: str,
    interested_theme_names: Sequence[str],
    noul_threshold: float,
    choice_min_confidence: float,
) -> CanonicalForm | str:
    gate_answers = ask(
        text,
        {
            "relevant": _noul(GATE_RELEVANT_INSTRUCTIONS),
            "signals_relevant": _noul(GATE_SIGNALS_INSTRUCTIONS),
            "themes_relevant": _noul(GATE_THEMES_INSTRUCTIONS),
        },
    )
    if not _passes(gate_answers["relevant"], noul_threshold):
        return EMPTY_CANONICAL_FORM

    signals_relevant = _passes(gate_answers["signals_relevant"], noul_threshold)
    themes_relevant = _passes(gate_answers["themes_relevant"], noul_threshold)

    signal_polarity = (
        _classify_signals(ask, text, choice_min_confidence) if signals_relevant else {}
    )
    theme_polarity, sector, sector_polarity = (
        _classify_themes(engine, ask, text, interested_theme_names, choice_min_confidence)
        if themes_relevant
        else ({}, None, None)
    )

    nothing_matched = not signal_polarity and not theme_polarity and sector is None
    signals_unclassified = signals_relevant and not signal_polarity
    themes_unclassified = themes_relevant and sector is None
    if nothing_matched or signals_unclassified or themes_unclassified:
        return text

    return CanonicalForm(
        signal_polarity=signal_polarity,
        theme_polarity=theme_polarity,
        sector=sector,
        sector_polarity=sector_polarity,
    )


def classify_additional_request(
    engine: sa.Engine,
    ask: AskFn,
    jev_model: str,
    noul_threshold: float,
    choice_min_confidence: float,
    additional_request: str,
    interested_theme_names: Sequence[str],
    log: StructuredLogger,
) -> tuple[Any, str | None, bool]:
    version = _version(jev_model, noul_threshold, choice_min_confidence)
    text = additional_request.strip()
    if not text:
        return EMPTY_CANONICAL_FORM.to_json(), version, True

    try:
        form = _classify_text(
            engine, ask, text, interested_theme_names, noul_threshold, choice_min_confidence
        )
        if isinstance(form, str):
            return form, version, True
        return form.to_json(), version, True
    except Exception as error:
        log.exception("canonicalisation_failed", error_type=type(error).__name__)
        if isinstance(error, httpx.HTTPStatusError) and error.response.status_code in {
            401,
            402,
            403,
        }:
            raise
        return additional_request, None, False


def canonical_form_part(canonical_form: Any) -> str:
    if canonical_form is None:
        return ""
    if isinstance(canonical_form, str):
        return canonical_form
    return json.dumps(canonical_form, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
