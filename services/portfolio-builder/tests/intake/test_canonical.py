import sqlalchemy as sa
from portfolio_builder.competition.canonical import (
    EMPTY_CANONICAL_FORM,
    MAX_CHOICE_OPTIONS,
    SIGNAL_NAMES,
    canonical_form_part,
    classify_additional_request,
)

NOUL_THRESHOLD = 0.5

CHOICE_MIN_CONFIDENCE = 0.6

MODEL = "typesafe/jev-1.14"


class _NullLog:
    def __init__(self):
        self.errors = []

    def exception(self, event, **fields):
        self.errors.append((event, fields))


def _noul(value):
    return {"type": "noul", "noul": value}


def _choice(choice, confidence):
    return {"type": "choice", "choice": choice, "probabilities": {}, "confidence": confidence}


class FakeAsk:
    def __init__(self, answers):
        self._answers = answers
        self.calls = []

    def __call__(self, state, questions):
        self.calls.append((state, set(questions)))
        return {name: self._answers[name] for name in questions}


def _classify(engine, ask, text="x", interested=(), log=None):
    return classify_additional_request(
        engine,
        ask,
        MODEL,
        NOUL_THRESHOLD,
        CHOICE_MIN_CONFIDENCE,
        text,
        interested,
        log or _NullLog(),
    )


def test_an_empty_additional_request_makes_no_call_and_returns_the_empty_form(engine):
    ask = FakeAsk({})

    form, version, classified = _classify(engine, ask, text="")

    assert ask.calls == []
    assert form == EMPTY_CANONICAL_FORM.to_json()
    assert classified
    assert version


def test_whitespace_only_also_short_circuits_with_no_call(engine):
    ask = FakeAsk({})

    form, _, _ = _classify(engine, ask, text="   \n ")

    assert ask.calls == []
    assert form == EMPTY_CANONICAL_FORM.to_json()


def test_a_closed_relevance_gate_makes_only_one_call(engine):
    ask = FakeAsk(
        {
            "relevant": _noul(0.1),
            "signals_relevant": _noul(0.9),
            "themes_relevant": _noul(0.9),
        }
    )

    form, _, _ = _classify(engine, ask)

    assert len(ask.calls) == 1
    assert form == EMPTY_CANONICAL_FORM.to_json()


def test_an_open_gate_with_both_detail_gates_closed_returns_the_raw_text(engine):
    ask = FakeAsk(
        {
            "relevant": _noul(0.9),
            "signals_relevant": _noul(0.1),
            "themes_relevant": _noul(0.1),
        }
    )

    form, version, classified = _classify(engine, ask, text="장기 보유할 생각이야")

    assert len(ask.calls) == 1
    assert form == "장기 보유할 생각이야"
    assert classified
    assert version


def test_an_open_signals_gate_asks_all_nine_signals_and_classifies_each(engine):
    answers = {
        "relevant": _noul(0.9),
        "signals_relevant": _noul(0.9),
        "themes_relevant": _noul(0.1),
    }
    for i, name in enumerate(SIGNAL_NAMES):
        answers[name] = _choice("favor" if i % 2 == 0 else "avoid", 0.9)
    ask = FakeAsk(answers)

    form, _, _ = _classify(engine, ask)

    assert len(ask.calls) == 2
    assert ask.calls[1][1] == set(SIGNAL_NAMES)
    expected = {name: ("favor" if i % 2 == 0 else "avoid") for i, name in enumerate(SIGNAL_NAMES)}
    assert form["signals"] == expected


def test_a_signal_confidence_exactly_at_the_floor_is_trusted(engine):
    answers = {
        "relevant": _noul(0.9),
        "signals_relevant": _noul(0.9),
        "themes_relevant": _noul(0.1),
    }
    for name in SIGNAL_NAMES:
        answers[name] = _choice("favor", CHOICE_MIN_CONFIDENCE)
    ask = FakeAsk(answers)

    form, _, _ = _classify(engine, ask)

    assert form["signals"] == {name: "favor" for name in SIGNAL_NAMES}


def test_a_signal_just_below_the_confidence_floor_is_omitted(engine):
    answers = {
        "relevant": _noul(0.9),
        "signals_relevant": _noul(0.9),
        "themes_relevant": _noul(0.1),
    }
    for name in SIGNAL_NAMES:
        answers[name] = _choice("neither", 0.9)
    answers["trend"] = _choice("favor", 0.9)
    answers["volatility"] = _choice("favor", CHOICE_MIN_CONFIDENCE - 0.01)
    ask = FakeAsk(answers)

    form, _, _ = _classify(engine, ask)

    assert form["signals"] == {"trend": "favor"}


def test_selected_interested_themes_ask_one_choice_per_received_name(engine):
    answers = {
        "relevant": _noul(0.9),
        "signals_relevant": _noul(0.1),
        "themes_relevant": _noul(0.9),
        "반도체": _choice("emphasize", 0.9),
        "2차전지": _choice("neither", 0.9),
        "sector": _choice("T2", 0.9),
        "sector_polarity": _choice("emphasize", 0.9),
    }
    ask = FakeAsk(answers)

    form, _, _ = _classify(engine, ask, interested=("반도체", "2차전지"))

    assert len(ask.calls) == 2
    assert ask.calls[1][1] == {"반도체", "2차전지", "sector", "sector_polarity"}
    assert form["themes"] == {"반도체": "emphasize"}
    assert form["sector"] == "T2"


def test_a_group_name_selection_asks_one_question_not_one_per_resolved_code(engine):
    answers = {
        "relevant": _noul(0.9),
        "signals_relevant": _noul(0.1),
        "themes_relevant": _noul(0.9),
        "반도체": _choice("emphasize", 0.9),
        "sector": _choice("T2", 0.9),
        "sector_polarity": _choice("emphasize", 0.9),
    }
    ask = FakeAsk(answers)

    form, _, _ = _classify(engine, ask, interested=("반도체",))

    assert len(ask.calls) == 2
    assert ask.calls[1][1] == {"반도체", "sector", "sector_polarity"}
    assert form["themes"] == {"반도체": "emphasize"}


def test_no_interested_themes_asks_a_choice_over_the_themes_table_and_its_polarity(engine):
    answers = {
        "relevant": _noul(0.9),
        "signals_relevant": _noul(0.1),
        "themes_relevant": _noul(0.9),
        "sector": _choice("T2", 0.9),
        "sector_polarity": _choice("emphasize", 0.9),
    }
    ask = FakeAsk(answers)

    form, _, _ = _classify(engine, ask, interested=())

    assert len(ask.calls) == 2
    assert ask.calls[1][1] == {"sector", "sector_polarity"}
    assert form["sector"] == "T2"
    assert form["sector_polarity"] == "emphasize"
    assert form["themes"] == {}


def test_a_low_confidence_sector_choice_with_nothing_else_matched_returns_the_raw_text(engine):
    answers = {
        "relevant": _noul(0.9),
        "signals_relevant": _noul(0.1),
        "themes_relevant": _noul(0.9),
        "sector": _choice("T2", CHOICE_MIN_CONFIDENCE - 0.01),
        "sector_polarity": _choice("emphasize", 0.9),
    }
    ask = FakeAsk(answers)

    form, _, classified = _classify(engine, ask, interested=(), text="아무 테마")

    assert form == "아무 테마"
    assert classified


def test_a_low_confidence_sector_choice_alongside_a_matched_signal_returns_the_raw_text(engine):
    answers = {
        "relevant": _noul(0.9),
        "signals_relevant": _noul(0.9),
        "themes_relevant": _noul(0.9),
        "sector": _choice("T2", CHOICE_MIN_CONFIDENCE - 0.01),
        "sector_polarity": _choice("emphasize", 0.9),
    }
    for name in SIGNAL_NAMES:
        answers[name] = _choice("neither", 0.9)
    answers["trend"] = _choice("favor", 0.9)
    ask = FakeAsk(answers)

    form, _, classified = _classify(engine, ask, interested=(), text="반도체 비중 높이고 상승추세")

    assert form == "반도체 비중 높이고 상승추세"
    assert classified


def _selected_theme_answers(sector, sector_confidence=0.9):
    answers = {
        "relevant": _noul(0.9),
        "signals_relevant": _noul(0.9),
        "themes_relevant": _noul(0.9),
        "HBM": _choice("emphasize", 0.9),
        "sector": _choice(sector, sector_confidence),
        "sector_polarity": _choice("emphasize", 0.9),
    }
    for name in SIGNAL_NAMES:
        answers[name] = _choice("neither", 0.9)
    answers["trend"] = _choice("favor", 0.9)
    return answers


def test_a_low_confidence_sector_beside_a_resolved_selected_theme_returns_the_raw_text(engine):
    ask = FakeAsk(_selected_theme_answers("T2", CHOICE_MIN_CONFIDENCE - 0.01))

    form, version, classified = _classify(
        engine,
        ask,
        text="HBM 비중 늘리고 상승추세 종목 담아줘",
        interested=("HBM",),
    )

    assert form == "HBM 비중 늘리고 상승추세 종목 담아줘"
    assert classified
    assert version


def test_an_open_signals_gate_matching_nothing_returns_the_raw_text(engine):
    answers = {
        "relevant": _noul(0.9),
        "signals_relevant": _noul(0.9),
        "themes_relevant": _noul(0.9),
        "sector": _choice("T2", 0.9),
        "sector_polarity": _choice("emphasize", 0.9),
    }
    for name in SIGNAL_NAMES:
        answers[name] = _choice("neither", 0.9)
    ask = FakeAsk(answers)

    form, _, classified = _classify(engine, ask, interested=(), text="반도체 비중 높여줘")

    assert form == "반도체 비중 높여줘"
    assert classified


def test_a_sector_confidence_exactly_at_the_floor_is_trusted(engine):
    answers = {
        "relevant": _noul(0.9),
        "signals_relevant": _noul(0.1),
        "themes_relevant": _noul(0.9),
        "sector": _choice("T2", CHOICE_MIN_CONFIDENCE),
        "sector_polarity": _choice("avoid", 0.9),
    }
    ask = FakeAsk(answers)

    form, _, _ = _classify(engine, ask, interested=())

    assert form["sector"] == "T2"
    assert form["sector_polarity"] == "avoid"


def test_a_low_confidence_sector_polarity_is_treated_as_unknown(engine):
    answers = {
        "relevant": _noul(0.9),
        "signals_relevant": _noul(0.1),
        "themes_relevant": _noul(0.9),
        "sector": _choice("T2", 0.9),
        "sector_polarity": _choice("avoid", CHOICE_MIN_CONFIDENCE - 0.01),
    }
    ask = FakeAsk(answers)

    form, _, _ = _classify(engine, ask, interested=())

    assert form["sector"] == "T2"
    assert form["sector_polarity"] is None


def test_the_choice_criteria_is_built_from_the_themes_table(engine):
    seen = {}

    def ask(state, questions):
        seen.update(questions)
        return {
            "relevant": _noul(0.9),
            "signals_relevant": _noul(0.1),
            "themes_relevant": _noul(0.9),
            "sector": _choice("T1", 0.9),
            "sector_polarity": _choice("neither", 0.9),
        }

    _classify(engine, ask, interested=())

    assert seen["sector"]["type"] == "choice"
    assert seen["sector"]["criteria"] == {"T1": "HBM", "T2": "AI반도체"}


def test_more_than_255_themes_fails_classification_cleanly_instead_of_truncating(engine):
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO themes (theme_code, name) VALUES (:code, :name)"),
            [{"code": f"X{i}", "name": f"theme{i}"} for i in range(MAX_CHOICE_OPTIONS)],
        )
    ask = FakeAsk(
        {
            "relevant": _noul(0.9),
            "signals_relevant": _noul(0.1),
            "themes_relevant": _noul(0.9),
        }
    )
    log = _NullLog()

    form, version, classified = _classify(engine, ask, text="아무 테마나", interested=(), log=log)

    assert form == "아무 테마나"
    assert version is None
    assert not classified
    assert log.errors


def test_a_classification_failure_stores_the_raw_text(engine):
    def raising_ask(state, questions):
        raise RuntimeError("jev is down")

    form, version, classified = _classify(engine, raising_ask, text="원문")

    assert form == "원문"
    assert version is None
    assert not classified


def test_at_most_three_calls_are_ever_made(engine):
    answers = {
        "relevant": _noul(0.9),
        "signals_relevant": _noul(0.9),
        "themes_relevant": _noul(0.9),
        "T1": _choice("emphasize", 0.9),
        "sector": _choice("T2", 0.9),
        "sector_polarity": _choice("emphasize", 0.9),
    }
    for name in SIGNAL_NAMES:
        answers[name] = _choice("neither", 0.9)
    ask = FakeAsk(answers)

    _classify(engine, ask, interested=("T1",))

    assert len(ask.calls) == 3


def test_canonical_form_part_serializes_regardless_of_key_order():
    a = canonical_form_part({"b": 1, "a": 2})
    b = canonical_form_part({"a": 2, "b": 1})
    assert a == b


def test_canonical_form_part_treats_none_as_empty():
    assert canonical_form_part(None) == ""


def test_canonical_form_part_passes_through_a_raw_string():
    assert canonical_form_part("원문") == "원문"
