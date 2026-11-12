from portfolio_builder.competition.canonical import (
    EMPTY_CANONICAL_FORM,
    SIGNAL_NAMES,
    classify_additional_request,
)
from portfolio_builder.competition.repository import ParticipantSettings, generation_key

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


def test_theme_emphasise_and_theme_avoid_produce_different_generation_keys(engine):
    def _answers(choice):
        return {
            "relevant": _noul(0.9),
            "signals_relevant": _noul(0.1),
            "themes_relevant": _noul(0.9),
            "반도체": _choice(choice, 0.9),
            "sector": _choice("T2", 0.9),
            "sector_polarity": _choice("emphasize", 0.9),
        }

    emphasise_ask = FakeAsk(_answers("emphasize"))
    avoid_ask = FakeAsk(_answers("avoid"))

    emphasise_form, version, _ = _classify(
        engine, emphasise_ask, text="HBM 관련 요청", interested=("반도체",)
    )
    avoid_form, _, _ = _classify(engine, avoid_ask, text="HBM 관련 요청", interested=("반도체",))

    assert emphasise_form != avoid_form

    a = _settings(canonical_form=emphasise_form, canonical_version=version)
    b = _settings(canonical_form=avoid_form, canonical_version=version)
    assert generation_key(a, None, "openrouter/m") != generation_key(b, None, "openrouter/m")


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


def test_two_prompts_naming_different_sectors_produce_different_generation_keys(engine):
    def _answers():
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
        return answers

    semiconductor_form, version, _ = _classify(
        engine, FakeAsk(_answers()), text="반도체 비중 높이고 상승추세 종목 담아줘"
    )
    battery_form, _, _ = _classify(
        engine, FakeAsk(_answers()), text="배터리 비중 높이고 상승추세 종목 담아줘"
    )

    assert semiconductor_form != battery_form

    a = _settings(canonical_form=semiconductor_form, canonical_version=version)
    b = _settings(canonical_form=battery_form, canonical_version=version)
    assert generation_key(a, None, "openrouter/m") != generation_key(b, None, "openrouter/m")


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


def test_a_theme_named_only_in_the_free_text_cannot_merge_two_participants(engine):
    semiconductor_form, version, _ = _classify(
        engine,
        FakeAsk(_selected_theme_answers("T2")),
        text="HBM 비중 늘리고 반도체 위주로 상승추세 종목 담아줘",
        interested=("HBM",),
    )
    battery_form, _, _ = _classify(
        engine,
        FakeAsk(_selected_theme_answers("T1")),
        text="HBM 비중 늘리고 배터리 위주로 상승추세 종목 담아줘",
        interested=("HBM",),
    )

    assert semiconductor_form["themes"] == battery_form["themes"] == {"HBM": "emphasize"}
    assert semiconductor_form["sector"] == "T2"
    assert battery_form["sector"] == "T1"

    a = _settings(canonical_form=semiconductor_form, canonical_version=version)
    b = _settings(canonical_form=battery_form, canonical_version=version)
    assert generation_key(a, None, "openrouter/m") != generation_key(b, None, "openrouter/m")


def test_selected_themes_naming_the_same_sector_still_share_one_generation_key(engine):
    a_form, version, _ = _classify(
        engine,
        FakeAsk(_selected_theme_answers("T2")),
        text="HBM 비중 늘리고 반도체 위주로 상승추세 종목 담아줘",
        interested=("HBM",),
    )
    b_form, _, _ = _classify(
        engine,
        FakeAsk(_selected_theme_answers("T2")),
        text="반도체 중심으로 HBM 늘려서 추세 좋은 종목 담아줘",
        interested=("HBM",),
    )

    assert a_form == b_form

    a = _settings(canonical_form=a_form, canonical_version=version)
    b = _settings(canonical_form=b_form, canonical_version=version)
    assert generation_key(a, None, "openrouter/m") == generation_key(b, None, "openrouter/m")


def _settings(**overrides):
    base = {
        "participant_id": 1,
        "competition_id": 10,
        "user_id": 100,
        "risk_profile": "성장형",
        "interested_theme_codes": (),
        "excluded_theme_codes": (),
        "interested_theme_names": (),
        "excluded_theme_names": (),
        "additional_request": "x",
        "canonical_form": None,
        "canonical_version": "v1",
    }
    return ParticipantSettings(**{**base, **overrides})
