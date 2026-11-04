import sqlalchemy as sa
from portfolio_builder.competition.canonical import (
    EMPTY_CANONICAL_FORM,
    MAX_CHOICE_OPTIONS,
    SIGNAL_NAMES,
    canonical_form_part,
    classify_additional_request,
)
from portfolio_builder.competition.repository import ParticipantSettings, generation_key

NOUL_THRESHOLD = 0.5
CHOICE_MIN_CONFIDENCE = 0.6
MODEL = "typesafe/jev-1.14"


class _NullLog:
    def __init__(self):
        self.errors = []

    def error(self, event, **fields):
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


def test_an_open_gate_with_both_detail_gates_closed_costs_one_call(engine):
    ask = FakeAsk(
        {
            "relevant": _noul(0.9),
            "signals_relevant": _noul(0.1),
            "themes_relevant": _noul(0.1),
        }
    )

    form, _, _ = _classify(engine, ask)

    assert len(ask.calls) == 1
    assert form["relevant"] is True
    assert form["signals"] == []
    assert form["theme_codes"] == []
    assert form["sector"] is None


def test_an_open_signals_gate_asks_all_nine_signals_and_thresholds_each(engine):
    answers = {
        "relevant": _noul(0.9),
        "signals_relevant": _noul(0.9),
        "themes_relevant": _noul(0.1),
    }
    for i, name in enumerate(SIGNAL_NAMES):
        answers[name] = _noul(0.9 if i % 2 == 0 else 0.1)
    ask = FakeAsk(answers)

    form, _, _ = _classify(engine, ask)

    assert len(ask.calls) == 2
    assert ask.calls[1][1] == set(SIGNAL_NAMES)
    expected = sorted(name for i, name in enumerate(SIGNAL_NAMES) if i % 2 == 0)
    assert form["signals"] == expected


def test_a_noul_value_exactly_at_the_threshold_passes(engine):
    answers = {
        "relevant": _noul(0.9),
        "signals_relevant": _noul(0.9),
        "themes_relevant": _noul(0.1),
    }
    for name in SIGNAL_NAMES:
        answers[name] = _noul(NOUL_THRESHOLD)
    ask = FakeAsk(answers)

    form, _, _ = _classify(engine, ask)

    assert form["signals"] == sorted(SIGNAL_NAMES)


def test_a_noul_value_just_below_the_threshold_fails(engine):
    answers = {
        "relevant": _noul(0.9),
        "signals_relevant": _noul(0.9),
        "themes_relevant": _noul(0.1),
    }
    for name in SIGNAL_NAMES:
        answers[name] = _noul(NOUL_THRESHOLD - 0.001)
    ask = FakeAsk(answers)

    form, _, _ = _classify(engine, ask)

    assert form["signals"] == []


def test_selected_interested_themes_ask_one_noul_per_code(engine):
    answers = {
        "relevant": _noul(0.9),
        "signals_relevant": _noul(0.1),
        "themes_relevant": _noul(0.9),
        "T1": _noul(0.9),
        "T2": _noul(0.1),
    }
    ask = FakeAsk(answers)

    form, _, _ = _classify(engine, ask, interested=("T1", "T2"))

    assert len(ask.calls) == 2
    assert ask.calls[1][1] == {"T1", "T2"}
    assert form["theme_codes"] == ["T1"]
    assert form["sector"] is None


def test_no_interested_themes_asks_a_choice_over_the_themes_table(engine):
    answers = {
        "relevant": _noul(0.9),
        "signals_relevant": _noul(0.1),
        "themes_relevant": _noul(0.9),
        "sector": _choice("T2", 0.9),
    }
    ask = FakeAsk(answers)

    form, _, _ = _classify(engine, ask, interested=())

    assert len(ask.calls) == 2
    assert ask.calls[1][1] == {"sector"}
    assert form["sector"] == "T2"
    assert form["theme_codes"] == []


def test_a_low_confidence_choice_is_no_theme_identified(engine):
    answers = {
        "relevant": _noul(0.9),
        "signals_relevant": _noul(0.1),
        "themes_relevant": _noul(0.9),
        "sector": _choice("T2", CHOICE_MIN_CONFIDENCE - 0.01),
    }
    ask = FakeAsk(answers)

    form, _, _ = _classify(engine, ask, interested=())

    assert form["sector"] is None


def test_a_confidence_exactly_at_the_floor_is_trusted(engine):
    answers = {
        "relevant": _noul(0.9),
        "signals_relevant": _noul(0.1),
        "themes_relevant": _noul(0.9),
        "sector": _choice("T2", CHOICE_MIN_CONFIDENCE),
    }
    ask = FakeAsk(answers)

    form, _, _ = _classify(engine, ask, interested=())

    assert form["sector"] == "T2"


def test_the_choice_criteria_is_built_from_the_themes_table(engine):
    seen = {}

    def ask(state, questions):
        seen.update(questions)
        return {
            "relevant": _noul(0.9),
            "signals_relevant": _noul(0.1),
            "themes_relevant": _noul(0.9),
            "sector": _choice("T1", 0.9),
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
        "T1": _noul(0.9),
    }
    for name in SIGNAL_NAMES:
        answers[name] = _noul(0.1)
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


def _settings(**overrides):
    base = {
        "participant_id": 1,
        "competition_id": 10,
        "user_id": 100,
        "risk_profile": "성장형",
        "interested_theme_codes": (),
        "excluded_theme_codes": (),
        "additional_request": "x",
        "canonical_form": None,
        "canonical_version": "v1",
    }
    return ParticipantSettings(**{**base, **overrides})


def test_semiconductor_and_battery_prompts_produce_different_generation_keys(engine):
    semiconductor_ask = FakeAsk(
        {
            "relevant": _noul(0.9),
            "signals_relevant": _noul(0.1),
            "themes_relevant": _noul(0.9),
            "sector": _choice("T2", 0.9),
        }
    )
    battery_ask = FakeAsk(
        {
            "relevant": _noul(0.9),
            "signals_relevant": _noul(0.1),
            "themes_relevant": _noul(0.9),
            "sector": _choice("T1", 0.9),
        }
    )

    semiconductor_form, version, _ = _classify(
        engine, semiconductor_ask, text="반도체 테마만 담아줘", interested=()
    )
    battery_form, _, _ = _classify(engine, battery_ask, text="2차전지 테마만 담아줘", interested=())

    assert semiconductor_form["sector"] != battery_form["sector"]

    a = _settings(canonical_form=semiconductor_form, canonical_version=version)
    b = _settings(canonical_form=battery_form, canonical_version=version)
    assert generation_key(a, None, "openrouter/m") != generation_key(b, None, "openrouter/m")
