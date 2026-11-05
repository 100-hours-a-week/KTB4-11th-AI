import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa
from ktb_core.logging import StructuredLogger

AskFn = Callable[[str, dict[str, dict[str, Any]]], dict[str, dict[str, Any]]]

SIGNAL_NAMES = (
    "trend",
    "short_term_move",
    "breakout",
    "year_range",
    "volatility",
    "volume",
    "liquidity",
    "relative_strength",
    "short_term_rank",
)

QUESTION_SET_VERSION = "v2"
MAX_CHOICE_OPTIONS = 255

GATE_RELEVANT_INSTRUCTIONS = (
    "The state is free text a user wrote when asking an AI to build them a stock portfolio."
    " Does this text express any preference, instruction, or constraint that should change how"
    " the portfolio gets built, as opposed to being empty, a greeting, or other content with no"
    " bearing on portfolio construction? Answer with the probability that it does."
)

GATE_SIGNALS_INSTRUCTIONS = (
    "The state is free text a user wrote when asking an AI to build them a stock portfolio."
    " Does this text mention or imply a preference about a stock's price action or trading"
    " activity -- for example its trend, a recent sharp move, a breakout above its recent"
    " trading range, its position within its 52-week range, its volatility, its trading volume,"
    " its liquidity, or how its returns rank against other stocks? Answer with the probability"
    " that it does."
)

GATE_THEMES_INSTRUCTIONS = (
    "The state is free text a user wrote when asking an AI to build them a stock portfolio."
    " Does this text name, or clearly imply, an industry theme or sector that the portfolio"
    " should emphasize or avoid? Answer with the probability that it does."
)

SIGNAL_POLARITY_CRITERIA: dict[str, str] = {
    "favor": "The text wants the portfolio to favor (prefer, hold more of) stocks with this"
    " attribute.",
    "avoid": "The text wants the portfolio to avoid (exclude, hold less of) stocks with this"
    " attribute.",
    "neither": "The text expresses no preference either way about this attribute.",
}

SIGNAL_INSTRUCTIONS: dict[str, str] = {
    "trend": (
        "The state is free text a user wrote when asking an AI to build them a stock"
        " portfolio. Classify its stance toward stocks based on their price trend -- for"
        " example being in an established uptrend, an established downtrend, or trading"
        " sideways. Pick favor, avoid, or neither."
    ),
    "short_term_move": (
        "The state is free text a user wrote when asking an AI to build them a stock"
        " portfolio. Classify its stance toward stocks that have moved sharply in price over"
        ' the last few trading days -- a sudden rally, sell-off, or other abrupt move ("급등락").'
        " Pick favor, avoid, or neither."
    ),
    "breakout": (
        "The state is free text a user wrote when asking an AI to build them a stock"
        " portfolio. Classify its stance toward stocks that have broken out above, or are"
        " trading near, their recent trading-range high. Pick favor, avoid, or neither."
    ),
    "year_range": (
        "The state is free text a user wrote when asking an AI to build them a stock"
        " portfolio. Classify its stance toward stocks based on where their price sits within"
        " its 52-week range -- for example near a 52-week high or far below one. Pick favor,"
        " avoid, or neither."
    ),
    "volatility": (
        "The state is free text a user wrote when asking an AI to build them a stock"
        " portfolio. Classify its stance toward stocks based on how volatile their own price"
        " has been. Pick favor, avoid, or neither."
    ),
    "volume": (
        "The state is free text a user wrote when asking an AI to build them a stock"
        " portfolio. Classify its stance toward stocks based on their trading volume -- for"
        ' example a surge or a drop in volume ("거래량"). Pick favor, avoid, or neither.'
    ),
    "liquidity": (
        "The state is free text a user wrote when asking an AI to build them a stock"
        " portfolio. Classify its stance toward stocks based on how liquid or easy to trade"
        " they are. Pick favor, avoid, or neither."
    ),
    "relative_strength": (
        "The state is free text a user wrote when asking an AI to build them a stock"
        " portfolio. Classify its stance toward stocks based on their price momentum relative"
        " to the other KOSPI 200 stocks over the last year. Pick favor, avoid, or neither."
    ),
    "short_term_rank": (
        "The state is free text a user wrote when asking an AI to build them a stock"
        " portfolio. Classify its stance toward stocks based on how their very recent"
        " (few-day) return ranks against the other KOSPI 200 stocks. Pick favor, avoid, or"
        " neither."
    ),
}

THEME_POLARITY_CRITERIA: dict[str, str] = {
    "emphasize": "The text wants the portfolio to emphasize (favor, prefer) this theme.",
    "avoid": "The text wants the portfolio to avoid (exclude) this theme.",
    "neither": "The text expresses no preference either way about this theme.",
}

THEME_CODE_INSTRUCTIONS_SINGLE_TEMPLATE = (
    "The state is free text a user wrote when asking an AI to build them a stock portfolio."
    ' The user already chose the theme "{name}" (code {code}) as their one selected interest.'
    " Classify the text's stance toward that theme. Pick emphasize, avoid, or neither."
)

THEME_CODE_INSTRUCTIONS_TEMPLATE = (
    "The state is free text a user wrote when asking an AI to build them a stock portfolio."
    ' The user already chose the theme "{name}" (code {code}) as one of several interests.'
    " Classify the text's stance toward that theme specifically, as distinct from the user's"
    " other chosen interests. Pick emphasize if the text calls out or asks to emphasize that"
    " theme more than the others, avoid if it asks to avoid or exclude that theme, or neither"
    " if it does not single that theme out either way."
)

SECTOR_CHOICE_INSTRUCTIONS = (
    "The state is free text a user wrote when asking an AI to build them a stock portfolio."
    " The user chose no specific theme from the menu. Pick whichever theme in criteria this"
    " text most concerns. If the text does not concern any of them, pick your best guess and"
    " report low confidence."
)

SECTOR_POLARITY_INSTRUCTIONS = (
    "The state is free text a user wrote when asking an AI to build them a stock portfolio."
    " The user chose no specific theme from the menu; a separate question already picked"
    " which theme this text most concerns. Classify this text's stance toward that theme. Pick"
    " emphasize, avoid, or neither."
)


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


def _theme_names(engine: sa.Engine, codes: Sequence[str]) -> dict[str, str]:
    if not codes:
        return {}
    query = sa.text("SELECT theme_code, name FROM themes WHERE theme_code IN :codes").bindparams(
        sa.bindparam("codes", expanding=True)
    )
    with engine.connect() as conn:
        rows = conn.execute(query, {"codes": list(codes)})
        return {row[0]: row[1] for row in rows}


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
    interested_theme_codes: Sequence[str],
    choice_min_confidence: float,
) -> tuple[dict[str, str], str | None, str | None]:
    codes = sorted(set(interested_theme_codes))
    if codes:
        names = _theme_names(engine, codes)
        template = (
            THEME_CODE_INSTRUCTIONS_SINGLE_TEMPLATE
            if len(codes) == 1
            else THEME_CODE_INSTRUCTIONS_TEMPLATE
        )
        answers = ask(
            text,
            {
                code: _choice(
                    template.format(name=names.get(code, code), code=code), THEME_POLARITY_CRITERIA
                )
                for code in codes
            },
        )
        theme_polarity = {}
        for code in codes:
            picked = _polarity(answers[code], ("emphasize", "avoid"), choice_min_confidence)
            if picked is not None:
                theme_polarity[code] = picked
        return theme_polarity, None, None

    criteria = _theme_criteria(engine)
    if not criteria:
        return {}, None, None
    if len(criteria) > MAX_CHOICE_OPTIONS:
        raise CanonicalisationError(
            f"theme list has {len(criteria)} options, over the {MAX_CHOICE_OPTIONS} limit"
        )
    answers = ask(
        text,
        {
            "sector": _choice(SECTOR_CHOICE_INSTRUCTIONS, criteria),
            "sector_polarity": _choice(SECTOR_POLARITY_INSTRUCTIONS, THEME_POLARITY_CRITERIA),
        },
    )
    sector_answer = answers["sector"]
    if float(sector_answer["confidence"]) < choice_min_confidence:
        return {}, None, None
    sector = str(sector_answer["choice"])
    sector_polarity = _polarity(
        answers["sector_polarity"], ("emphasize", "avoid"), choice_min_confidence
    )
    return {}, sector, sector_polarity


def _classify_text(
    engine: sa.Engine,
    ask: AskFn,
    text: str,
    interested_theme_codes: Sequence[str],
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
        _classify_themes(engine, ask, text, interested_theme_codes, choice_min_confidence)
        if themes_relevant
        else ({}, None, None)
    )

    if not signal_polarity and not theme_polarity and sector is None:
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
    interested_theme_codes: Sequence[str],
    log: StructuredLogger,
) -> tuple[Any, str | None, bool]:
    version = _version(jev_model, noul_threshold, choice_min_confidence)
    text = additional_request.strip()
    if not text:
        return EMPTY_CANONICAL_FORM.to_json(), version, True

    try:
        form = _classify_text(
            engine, ask, text, interested_theme_codes, noul_threshold, choice_min_confidence
        )
        if isinstance(form, str):
            return form, version, True
        return form.to_json(), version, True
    except Exception as error:
        log.error("canonicalisation_failed", error_type=type(error).__name__)
        return additional_request, None, False


def canonical_form_part(canonical_form: Any) -> str:
    if canonical_form is None:
        return ""
    if isinstance(canonical_form, str):
        return canonical_form
    return json.dumps(canonical_form, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
