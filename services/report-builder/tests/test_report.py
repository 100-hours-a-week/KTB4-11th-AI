from uuid import UUID

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from report_builder.report import EvidenceError, build_report, load_evidence

FIRST = UUID("00000000-0000-0000-0000-000000000001")
SECOND = UUID("00000000-0000-0000-0000-000000000002")
PARTICIPANT_1 = 1


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class Connection:
    def __init__(self, reason_rows, summary_rows):
        self.reason_rows = reason_rows
        self.summary_rows = summary_rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, query, params):
        if "portfolio_reasons" in str(query):
            requested = params["reason_ids"]
            return Result([row for row in self.reason_rows if row["id"] in requested])
        requested = params["cluster_ids"]
        return Result([row for row in self.summary_rows if row["cluster_id"] in requested])


class Engine:
    def __init__(self, reason_rows, summary_rows):
        self.reason_rows = reason_rows
        self.summary_rows = summary_rows

    def connect(self):
        return Connection(self.reason_rows, self.summary_rows)


def reason(reason_id, commentary, citations):
    return {
        "id": reason_id,
        "reason": f"reason {reason_id}",
        "reasonings": [{"label": "근거", "body": "설명"}],
        "commentary": commentary,
        "cited_cluster_ids": citations,
    }


def test_loads_only_requested_reasons_and_their_commentary_and_deduplicates_cited_news():
    engine = Engine(
        [
            reason(FIRST, "portfolio one", [3, 4]),
            reason(SECOND, "portfolio two", [3]),
            reason(UUID(int=3), "unselected", [5]),
        ],
        [
            {"cluster_id": 3, "title": "news", "summary": "summary"},
            {"cluster_id": 4, "title": "other", "summary": "summary 2"},
            {"cluster_id": 5, "title": "unused", "summary": "unused"},
        ],
    )

    evidence = load_evidence(engine, [FIRST, SECOND, FIRST], PARTICIPANT_1)

    assert [item.id for item in evidence.reasons] == [FIRST, SECOND]
    assert [item.commentary for item in evidence.reasons] == ["portfolio one", "portfolio two"]
    assert [item["title"] for item in evidence.news] == ["news", "other"]


def test_missing_reason_fails():
    with pytest.raises(EvidenceError, match="missing portfolio reasons"):
        load_evidence(Engine([], []), [FIRST], PARTICIPANT_1)


def test_missing_cited_summary_fails():
    with pytest.raises(EvidenceError, match="missing cited summaries"):
        load_evidence(Engine([reason(FIRST, "comment", [9])], []), [FIRST], PARTICIPANT_1)


def test_empty_reason_selection_fails():
    with pytest.raises(EvidenceError, match="at least one"):
        load_evidence(Engine([], []), [], PARTICIPANT_1)


def _insert_portfolio(engine, commentary="c"):
    with engine.begin() as conn:
        return conn.execute(
            sa.text(
                "INSERT INTO portfolios (cash_weight, commentary, model) VALUES"
                " (0.1, :commentary, 'm') RETURNING id"
            ),
            {"commentary": commentary},
        ).scalar_one()


def _insert_participant(engine, participant_id):
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO competition_participants"
                " (participant_id, competition_id, user_id, risk_profile) VALUES"
                " (:pid, 1, 1, '성장형')"
            ),
            {"pid": participant_id},
        )


def _link(engine, participant_id, portfolio_id):
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO competition_portfolios (participant_id, portfolio_id) VALUES"
                " (:pid, :pf)"
            ),
            {"pid": participant_id, "pf": portfolio_id},
        )


def _insert_reason(engine, reason_id, portfolio_id):
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO portfolio_reasons"
                " (id, portfolio_id, company_id, side, reason, reasonings) VALUES"
                " (:id, :pf, '00126380', 'buy', 'r', '[]')"
            ),
            {"id": str(reason_id), "pf": portfolio_id},
        )


def test_a_reason_belonging_to_another_participant_is_reported_as_missing(engine):
    other_participant = _insert_portfolio(engine, commentary="다른 참가자")
    _insert_participant(engine, 2)
    _link(engine, 2, other_participant)
    _insert_participant(engine, PARTICIPANT_1)
    reason_id = FIRST
    _insert_reason(engine, reason_id, other_participant)

    with pytest.raises(EvidenceError, match="missing portfolio reasons"):
        load_evidence(engine, [reason_id], PARTICIPANT_1)


def test_a_reason_belonging_to_the_requesting_participant_resolves(engine):
    _insert_participant(engine, PARTICIPANT_1)
    mine = _insert_portfolio(engine, commentary="내 포트폴리오")
    _link(engine, PARTICIPANT_1, mine)
    reason_id = FIRST
    _insert_reason(engine, reason_id, mine)

    evidence = load_evidence(engine, [reason_id], PARTICIPANT_1)

    assert [item.id for item in evidence.reasons] == [reason_id]
    assert evidence.reasons[0].commentary == "내 포트폴리오"


def test_a_reason_on_an_unlinked_common_portfolio_resolves(engine):
    _insert_participant(engine, PARTICIPANT_1)
    common = _insert_portfolio(engine, commentary="공통 포트폴리오")
    reason_id = FIRST
    _insert_reason(engine, reason_id, common)

    evidence = load_evidence(engine, [reason_id], PARTICIPANT_1)

    assert [item.id for item in evidence.reasons] == [reason_id]
    assert evidence.reasons[0].commentary == "공통 포트폴리오"


class StubRunnable:
    def __init__(self, answer):
        self.answer = answer
        self.messages = None

    def invoke(self, messages):
        self.messages = messages
        return self.answer


def test_build_report_keeps_reason_commentary_pairs_and_supplied_news():
    from report_builder.report import Evidence

    evidence = Evidence.model_validate(
        {
            "reasons": [
                {"id": str(FIRST), "reason": "R1", "reasonings": [], "commentary": "C1"},
                {"id": str(SECOND), "reason": "R2", "reasonings": [], "commentary": "C2"},
            ],
            "news": [{"title": "N", "summary": "S"}],
        }
    )
    runnable = StubRunnable({"thoughts": [{"title": "제목", "text": "생각"}]})

    report = build_report(evidence, runnable)

    assert report.model_dump() == {
        "news": [{"title": "N", "summary": "S"}],
        "thoughts": [{"title": "제목", "text": "생각"}],
    }
    prompt = runnable.messages[1].content
    assert "C1" in prompt and "C2" in prompt
    assert prompt.index("R1") < prompt.index("C1") < prompt.index("R2") < prompt.index("C2")


@pytest.mark.parametrize("answer", [{"thoughts": []}, {"thoughts": [{"title": "", "text": "x"}]}])
def test_malformed_or_empty_thoughts_fail(answer):
    from report_builder.report import Evidence

    evidence = Evidence(reasons=[], news=[])
    with pytest.raises((ValidationError, ValueError)):
        build_report(evidence, StubRunnable(answer))
