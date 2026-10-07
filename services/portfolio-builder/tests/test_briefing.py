from contextlib import nullcontext
from datetime import datetime

import pytest
from portfolio_builder.agent.system_prompt import SYSTEM_PROMPT
from portfolio_builder.briefing import load_briefing, service
from portfolio_builder.portfolio import Holding, Submission, save_portfolio

SAMSUNG, HYNIX = "00126380", "00164779"


class NewsClientStub:
    def recent_news(self, days):
        assert days in (7, 60)
        clusters = [
            {
                "id": 1,
                "title": "삼성전자 HBM 공급 확대",
                "summary": "삼성전자가 엔비디아에 HBM을 공급한다.",
                "updated_at": datetime.now().astimezone(),
            }
        ]
        companies = [
            {
                "company_id": SAMSUNG,
                "name": "삼성전자",
                "stock_code": "005930",
                "cluster_ids": [1],
                "themes": ["HBM (main)"],
            }
        ]
        if days == 60:
            clusters.append(
                {
                    "id": 2,
                    "title": "반도체 수출 둔화",
                    "summary": "SK하이닉스가 HBM 생산을 늘린다.",
                    "updated_at": datetime.now().astimezone(),
                }
            )
            companies.append(
                {
                    "company_id": HYNIX,
                    "name": "SK하이닉스",
                    "stock_code": "000660",
                    "cluster_ids": [2],
                    "themes": ["HBM"],
                }
            )
        return {"clusters": clusters, "companies": companies, "theme_count": len(companies)}


def test_the_system_prompt_states_the_goal_grounding_and_tools():
    assert "model portfolio" in SYSTEM_PROMPT
    assert "pre-trained knowledge" in SYSTEM_PROMPT
    assert "submit_portfolio" in SYSTEM_PROMPT
    assert "timeframe" in SYSTEM_PROMPT


def test_first_run_briefs_only_clusters_inside_the_window(engine):
    briefing = load_briefing(engine, NewsClientStub(), 7)

    assert briefing.previous_portfolio_id is None
    assert briefing.previous_company_ids == frozenset()
    assert briefing.cluster_ids == [1]
    assert briefing.company_count == 1
    assert briefing.theme_count == 1
    assert "first portfolio" in briefing.text
    assert "[cluster 1] 삼성전자 HBM 공급 확대" in briefing.text
    assert f"삼성전자 (company_id {SAMSUNG}, stock_code 005930)" in briefing.text
    assert "HBM (main)" in briefing.text
    assert "반도체 수출 둔화" not in briefing.text


def test_each_mentioned_company_lists_its_own_themes(engine):
    briefing = load_briefing(engine, NewsClientStub(), 60)

    assert briefing.company_count == 2
    assert briefing.theme_count == 2
    assert (
        f"- 삼성전자 (company_id {SAMSUNG}, stock_code 005930): clusters 1; themes: HBM (main)"
        in briefing.text.splitlines()
    )
    assert (
        f"- SK하이닉스 (company_id {HYNIX}, stock_code 000660): clusters 2; themes: HBM"
        in briefing.text.splitlines()
    )


def _submission(company_id, reason, commentary, clusters):
    return Submission(
        holdings=[
            Holding(company_id=company_id, weight=1, reason=reason, cited_cluster_ids=clusters)
        ],
        exits=[],
        cash_weight=0,
        commentary=commentary,
    )


def test_the_most_recently_created_portfolio_is_the_previous_one(engine):
    save_portfolio(engine, _submission(HYNIX, "old", "older", []), "m")
    latest = save_portfolio(engine, _submission(SAMSUNG, "new", "newer", [1]), "m")

    briefing = load_briefing(engine, NewsClientStub(), 7)

    assert briefing.previous_portfolio_id == latest
    assert briefing.previous_company_ids == frozenset({SAMSUNG})
    assert briefing.previous_holdings == 1
    assert "newer" in briefing.text
    assert "older" not in briefing.text


def test_empty_news_is_rendered_without_postgres_news_queries(monkeypatch):
    class Engine:
        def connect(self):
            return nullcontext(object())

    class EmptyNewsClient:
        def recent_news(self, days):
            return {"clusters": [], "companies": [], "theme_count": 0}

    monkeypatch.setattr(service, "find_previous_portfolio", lambda conn: None)

    briefing = load_briefing(Engine(), EmptyNewsClient(), 7)

    assert briefing.cluster_ids == []
    assert briefing.company_count == 0
    assert briefing.theme_count == 0
    assert briefing.text.endswith("# Companies mentioned in these clusters\nNone.")


def test_news_failure_aborts_before_a_briefing_is_returned(monkeypatch):
    class Engine:
        def connect(self):
            return nullcontext(object())

    class FailedNewsClient:
        def recent_news(self, days):
            raise RuntimeError("news-http unavailable")

    monkeypatch.setattr(service, "find_previous_portfolio", lambda conn: None)

    with pytest.raises(RuntimeError, match="news-http unavailable"):
        load_briefing(Engine(), FailedNewsClient(), 7)
