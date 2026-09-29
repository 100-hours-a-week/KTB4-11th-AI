from portfolio_builder.agent.system_prompt import SYSTEM_PROMPT
from portfolio_builder.briefing import load_briefing
from portfolio_builder.portfolio import Holding, Submission, save_portfolio

SAMSUNG, HYNIX = "00126380", "00164779"


def test_the_system_prompt_states_the_goal_grounding_and_tools():
    assert "model portfolio" in SYSTEM_PROMPT
    assert "pre-trained knowledge" in SYSTEM_PROMPT
    assert "submit_portfolio" in SYSTEM_PROMPT
    assert "timeframe" in SYSTEM_PROMPT


def test_first_run_briefs_only_clusters_inside_the_window(engine):
    briefing = load_briefing(engine, 7)

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
    briefing = load_briefing(engine, 60)

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

    briefing = load_briefing(engine, 7)

    assert briefing.previous_portfolio_id == latest
    assert briefing.previous_company_ids == frozenset({SAMSUNG})
    assert briefing.previous_holdings == 1
    assert "newer" in briefing.text
    assert "older" not in briefing.text
