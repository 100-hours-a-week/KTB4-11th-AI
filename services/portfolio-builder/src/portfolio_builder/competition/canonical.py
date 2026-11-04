import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
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

QUESTION_SET_VERSION = "v1"
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
    " or its liquidity? Answer with the probability that it does."
)

GATE_THEMES_INSTRUCTIONS = (
    "The state is free text a user wrote when asking an AI to build them a stock portfolio."
    " Does this text name, or clearly imply, an industry theme or sector that the portfolio"
    " should emphasize or avoid? Answer with the probability that it does."
)

SIGNAL_INSTRUCTIONS: dict[str, str] = {
    "trend": (
        "The state is free text a user wrote when asking an AI to build them a stock"
        " portfolio. Does it ask the portfolio to favor or avoid stocks based on their price"
        " trend -- for example being in an established uptrend, an established downtrend, or"
        " trading sideways? Answer with the probability that it does."
    ),
    "short_term_move": (
        "The state is free text a user wrote when asking an AI to build them a stock"
        " portfolio. Does it ask the portfolio to favor or avoid stocks that have moved sharply"
        " in price over the last few trading days -- a sudden rally, sell-off, or other abrupt"
        ' move ("급등락")? Answer with the probability that it does.'
    ),
    "breakout": (
        "The state is free text a user wrote when asking an AI to build them a stock"
        " portfolio. Does it ask the portfolio to favor or avoid stocks that have broken out"
        " above, or are trading near, their recent trading-range high? Answer with the"
        " probability that it does."
    ),
    "year_range": (
        "The state is free text a user wrote when asking an AI to build them a stock"
        " portfolio. Does it ask the portfolio to favor or avoid stocks based on where their"
        " price sits within its 52-week range -- for example near a 52-week high or far below"
        " one? Answer with the probability that it does."
    ),
    "volatility": (
        "The state is free text a user wrote when asking an AI to build them a stock"
        " portfolio. Does it ask the portfolio to favor or avoid stocks based on how volatile"
        " their own price has been? Answer with the probability that it does."
    ),
    "volume": (
        "The state is free text a user wrote when asking an AI to build them a stock"
        " portfolio. Does it ask the portfolio to favor or avoid stocks based on their trading"
        ' volume -- for example a surge or a drop in volume ("거래량")? Answer with the'
        " probability that it does."
    ),
    "liquidity": (
        "The state is free text a user wrote when asking an AI to build them a stock"
        " portfolio. Does it ask the portfolio to favor or avoid stocks based on how liquid or"
        " easy to trade they are? Answer with the probability that it does."
    ),
    "relative_strength": (
        "The state is free text a user wrote when asking an AI to build them a stock"
        " portfolio. Does it ask the portfolio to favor or avoid stocks based on their price"
        " momentum relative to the other KOSPI 200 stocks over the last year? Answer with the"
        " probability that it does."
    ),
    "short_term_rank": (
        "The state is free text a user wrote when asking an AI to build them a stock"
        " portfolio. Does it ask the portfolio to favor or avoid stocks based on how their very"
        " recent (few-day) return ranks against the other KOSPI 200 stocks? Answer with the"
        " probability that it does."
    ),
}

THEME_CODE_INSTRUCTIONS_TEMPLATE = (
    "The state is free text a user wrote when asking an AI to build them a stock portfolio."
    ' The user already chose the theme "{name}" (code {code}) as one of their interests. Does'
    " this text specifically call out or emphasize that theme more than the user's other chosen"
    " interests? Answer with the probability that it does."
)

SECTOR_CHOICE_INSTRUCTIONS = (
    "The state is free text a user wrote when asking an AI to build them a stock portfolio."
    " The user chose no specific theme from the menu. Pick whichever theme in criteria this"
    " text most concerns. If the text does not concern any of them, pick your best guess and"
    " report low confidence."
)


class CanonicalisationError(Exception):
    pass


@dataclass(frozen=True)
class CanonicalForm:
    signals: tuple[str, ...] = ()
    theme_codes: tuple[str, ...] = ()
    sector: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "signals": sorted(self.signals),
            "theme_codes": sorted(self.theme_codes),
            "sector": self.sector,
        }


EMPTY_CANONICAL_FORM = CanonicalForm()


def _noul(instructions: str) -> dict[str, Any]:
    return {"type": "noul", "instructions": instructions}


def _choice(instructions: str, criteria: dict[str, str]) -> dict[str, Any]:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def _passes(answer: dict[str, Any], threshold: float) -> bool:
    return float(answer["noul"]) >= threshold


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


def _classify_signals(ask: AskFn, text: str, noul_threshold: float) -> tuple[str, ...]:
    answers = ask(text, {name: _noul(SIGNAL_INSTRUCTIONS[name]) for name in SIGNAL_NAMES})
    return tuple(sorted(name for name in SIGNAL_NAMES if _passes(answers[name], noul_threshold)))


def _classify_themes(
    engine: sa.Engine,
    ask: AskFn,
    text: str,
    interested_theme_codes: Sequence[str],
    noul_threshold: float,
    choice_min_confidence: float,
) -> tuple[tuple[str, ...], str | None]:
    codes = sorted(set(interested_theme_codes))
    if codes:
        names = _theme_names(engine, codes)
        answers = ask(
            text,
            {
                code: _noul(
                    THEME_CODE_INSTRUCTIONS_TEMPLATE.format(name=names.get(code, code), code=code)
                )
                for code in codes
            },
        )
        theme_codes = tuple(
            sorted(code for code in codes if _passes(answers[code], noul_threshold))
        )
        return theme_codes, None

    criteria = _theme_criteria(engine)
    if not criteria:
        return (), None
    if len(criteria) > MAX_CHOICE_OPTIONS:
        raise CanonicalisationError(
            f"theme list has {len(criteria)} options, over the {MAX_CHOICE_OPTIONS} limit"
        )
    answer = ask(text, {"sector": _choice(SECTOR_CHOICE_INSTRUCTIONS, criteria)})["sector"]
    if float(answer["confidence"]) >= choice_min_confidence:
        return (), str(answer["choice"])
    return (), None


def _classify_text(
    engine: sa.Engine,
    ask: AskFn,
    text: str,
    interested_theme_codes: Sequence[str],
    noul_threshold: float,
    choice_min_confidence: float,
) -> CanonicalForm:
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

    signals = _classify_signals(ask, text, noul_threshold) if signals_relevant else ()
    theme_codes, sector = (
        _classify_themes(
            engine, ask, text, interested_theme_codes, noul_threshold, choice_min_confidence
        )
        if themes_relevant
        else ((), None)
    )

    return CanonicalForm(signals=signals, theme_codes=theme_codes, sector=sector)


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
