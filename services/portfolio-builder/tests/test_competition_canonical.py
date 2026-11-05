import sqlalchemy as sa
from portfolio_builder.competition.canonical import (
    EMPTY_CANONICAL_FORM,
    MAX_CHOICE_OPTIONS,
    SIGNAL_NAMES,
    canonical_form_part,
    classify_additional_request,
)
from portfolio_builder.competition.repository import ParticipantSettings, generation_key
from portfolio_builder.interpretation import RULES

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


def test_signal_names_matches_the_real_signal_set():
    assert set(SIGNAL_NAMES) == set(RULES)


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


def test_a_closed_relevance_gate_and_an_empty_prompt_share_identity(engine):
    closed_ask = FakeAsk(
        {
            "relevant": _noul(0.1),
            "signals_relevant": _noul(0.9),
            "themes_relevant": _noul(0.9),
        }
    )
    empty_ask = FakeAsk({})

    closed_form, version, _ = _classify(engine, closed_ask, text="아무 말")
    empty_form, _, _ = _classify(engine, empty_ask, text="")

    assert closed_form == empty_form

    a = _settings(canonical_form=closed_form, canonical_version=version)
    b = _settings(canonical_form=empty_form, canonical_version=version)
    assert generation_key(a, None, "openrouter/m") == generation_key(b, None, "openrouter/m")


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


def test_an_uncategorisable_instruction_does_not_share_with_the_empty_prompt(engine):
    open_answers = {
        "relevant": _noul(0.9),
        "signals_relevant": _noul(0.9),
        "themes_relevant": _noul(0.1),
    }
    for name in SIGNAL_NAMES:
        open_answers[name] = _choice("neither", 0.9)
    open_ask = FakeAsk(open_answers)

    form, version, classified = _classify(engine, open_ask, text="최대 5종목만 담아줘")

    assert form == "최대 5종목만 담아줘"
    assert form != EMPTY_CANONICAL_FORM.to_json()
    assert classified
    assert version

    a = _settings(canonical_form=form, canonical_version=version)
    b = _settings(canonical_form=EMPTY_CANONICAL_FORM.to_json(), canonical_version=version)
    assert generation_key(a, None, "openrouter/m") != generation_key(b, None, "openrouter/m")


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


def test_volatility_favor_and_volatility_avoid_produce_different_generation_keys(engine):
    def _answers(polarity):
        answers = {
            "relevant": _noul(0.9),
            "signals_relevant": _noul(0.9),
            "themes_relevant": _noul(0.1),
        }
        for name in SIGNAL_NAMES:
            answers[name] = _choice("neither", 0.9)
        answers["volatility"] = _choice(polarity, 0.9)
        return answers

    favor_ask = FakeAsk(_answers("favor"))
    avoid_ask = FakeAsk(_answers("avoid"))

    favor_form, version, _ = _classify(engine, favor_ask, text="변동성 관련 요청")
    avoid_form, _, _ = _classify(engine, avoid_ask, text="변동성 관련 요청")

    assert favor_form != avoid_form

    a = _settings(canonical_form=favor_form, canonical_version=version)
    b = _settings(canonical_form=avoid_form, canonical_version=version)
    assert generation_key(a, None, "openrouter/m") != generation_key(b, None, "openrouter/m")


def test_polarity_differing_in_one_signal_among_several_still_differs(engine):
    def _answers(volatility_choice):
        answers = {
            "relevant": _noul(0.9),
            "signals_relevant": _noul(0.9),
            "themes_relevant": _noul(0.1),
        }
        for name in SIGNAL_NAMES:
            answers[name] = _choice("neither", 0.9)
        answers["trend"] = _choice("favor", 0.9)
        answers["volume"] = _choice("avoid", 0.9)
        answers["volatility"] = _choice(volatility_choice, 0.9)
        return answers

    a_ask = FakeAsk(_answers("favor"))
    b_ask = FakeAsk(_answers("avoid"))

    a_form, version, _ = _classify(engine, a_ask, text="같은 문장")
    b_form, _, _ = _classify(engine, b_ask, text="같은 문장")

    assert a_form != b_form

    a = _settings(canonical_form=a_form, canonical_version=version)
    b = _settings(canonical_form=b_form, canonical_version=version)
    assert generation_key(a, None, "openrouter/m") != generation_key(b, None, "openrouter/m")


def test_selected_interested_themes_ask_one_choice_per_code(engine):
    answers = {
        "relevant": _noul(0.9),
        "signals_relevant": _noul(0.1),
        "themes_relevant": _noul(0.9),
        "T1": _choice("emphasize", 0.9),
        "T2": _choice("neither", 0.9),
    }
    ask = FakeAsk(answers)

    form, _, _ = _classify(engine, ask, interested=("T1", "T2"))

    assert len(ask.calls) == 2
    assert ask.calls[1][1] == {"T1", "T2"}
    assert form["themes"] == {"T1": "emphasize"}
    assert form["sector"] is None


def test_theme_emphasise_and_theme_avoid_produce_different_generation_keys(engine):
    def _answers(choice):
        return {
            "relevant": _noul(0.9),
            "signals_relevant": _noul(0.1),
            "themes_relevant": _noul(0.9),
            "T1": _choice(choice, 0.9),
        }

    emphasise_ask = FakeAsk(_answers("emphasize"))
    avoid_ask = FakeAsk(_answers("avoid"))

    emphasise_form, version, _ = _classify(
        engine, emphasise_ask, text="HBM 관련 요청", interested=("T1",)
    )
    avoid_form, _, _ = _classify(engine, avoid_ask, text="HBM 관련 요청", interested=("T1",))

    assert emphasise_form != avoid_form

    a = _settings(canonical_form=emphasise_form, canonical_version=version)
    b = _settings(canonical_form=avoid_form, canonical_version=version)
    assert generation_key(a, None, "openrouter/m") != generation_key(b, None, "openrouter/m")


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


def test_sector_emphasise_and_sector_avoid_produce_different_generation_keys(engine):
    def _answers(polarity):
        return {
            "relevant": _noul(0.9),
            "signals_relevant": _noul(0.1),
            "themes_relevant": _noul(0.9),
            "sector": _choice("T2", 0.9),
            "sector_polarity": _choice(polarity, 0.9),
        }

    emphasise_ask = FakeAsk(_answers("emphasize"))
    avoid_ask = FakeAsk(_answers("avoid"))

    emphasise_form, version, _ = _classify(engine, emphasise_ask, text="반도체 위주로 담아줘")
    avoid_form, _, _ = _classify(engine, avoid_ask, text="반도체는 빼줘")

    assert emphasise_form != avoid_form

    a = _settings(canonical_form=emphasise_form, canonical_version=version)
    b = _settings(canonical_form=avoid_form, canonical_version=version)
    assert generation_key(a, None, "openrouter/m") != generation_key(b, None, "openrouter/m")


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


def test_a_low_confidence_sector_choice_alongside_a_matched_signal_omits_only_the_sector(engine):
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

    form, _, _ = _classify(engine, ask, interested=())

    assert form["sector"] is None
    assert form["sector_polarity"] is None
    assert form["signals"] == {"trend": "favor"}


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
