from typing import Annotated, Any
from uuid import UUID

import sqlalchemy as sa
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from langchain_openrouter import ChatOpenRouter
from pydantic import BaseModel, StringConstraints


class EvidenceError(ValueError):
    pass


class EvidenceReason(BaseModel):
    id: UUID
    reason: str
    reasonings: list[dict[str, str]]
    commentary: str


class Evidence(BaseModel):
    reasons: list[EvidenceReason]
    news: list[dict[str, str]]


class Thought(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Thoughts(BaseModel):
    thoughts: list[Thought]


class Report(BaseModel):
    news: list[dict[str, str]]
    thoughts: list[Thought]


def structured_generator(model: str, api_key: str, timeout: float) -> Runnable:
    return ChatOpenRouter(
        model=model, api_key=api_key, timeout=timeout, max_retries=0
    ).with_structured_output(Thoughts)


def load_evidence(engine: sa.Engine, reason_ids: list[UUID]) -> Evidence:
    ids = list(dict.fromkeys(reason_ids))
    if not ids:
        raise EvidenceError("at least one portfolio reason is required")
    reason_query = sa.text(
        "SELECT r.id, r.reason, r.reasonings, p.commentary,"
        " COALESCE(h.cited_cluster_ids, e.cited_cluster_ids, '{}') AS cited_cluster_ids"
        " FROM portfolio_reasons r"
        " JOIN portfolios p ON p.id = r.portfolio_id"
        " LEFT JOIN portfolio_holdings h"
        " ON h.portfolio_id = r.portfolio_id AND h.company_id = r.company_id"
        " LEFT JOIN portfolio_exits e"
        " ON e.portfolio_id = r.portfolio_id AND e.company_id = r.company_id"
        " WHERE r.id IN :reason_ids ORDER BY r.id"
    ).bindparams(sa.bindparam("reason_ids", expanding=True))
    with engine.connect() as conn:
        rows = conn.execute(reason_query, {"reason_ids": ids}).mappings().all()
        found = {row["id"] for row in rows}
        missing = set(ids) - found
        if missing:
            missing_ids = ", ".join(map(str, sorted(missing)))
            raise EvidenceError(f"missing portfolio reasons: {missing_ids}")
        cluster_ids = list(
            dict.fromkeys(cluster_id for row in rows for cluster_id in row["cited_cluster_ids"])
        )
        summaries = []
        if cluster_ids:
            summaries = (
                conn.execute(
                    sa.text(
                        "SELECT cluster_id, title, summary FROM cluster_summaries"
                        " WHERE cluster_id IN :cluster_ids ORDER BY cluster_id"
                    ).bindparams(sa.bindparam("cluster_ids", expanding=True)),
                    {"cluster_ids": cluster_ids},
                )
                .mappings()
                .all()
            )
    summary_by_id = {row["cluster_id"]: row for row in summaries}
    missing_summaries = set(cluster_ids) - summary_by_id.keys()
    if missing_summaries:
        raise EvidenceError(
            f"missing cited summaries: {', '.join(map(str, sorted(missing_summaries)))}"
        )
    reasons = [
        EvidenceReason(
            id=row["id"],
            reason=row["reason"],
            reasonings=row["reasonings"],
            commentary=row["commentary"],
        )
        for row in rows
    ]
    news = [
        {"title": summary_by_id[cid]["title"], "summary": summary_by_id[cid]["summary"]}
        for cid in cluster_ids
    ]
    return Evidence(reasons=reasons, news=news)


REPORT_PROMPT = (
    "선택된 투자 이유와 그 이유가 속한 포트폴리오 코멘터리를 읽고,"
    " 근거에 기반한 한국어 thoughts를 작성하세요."
    " 각 reason과 commentary의 연결을 유지하세요."
    " 제공된 근거 밖의 사실을 만들지 마세요. 뉴스는 생성하지 마세요."
)


def build_report(evidence: Evidence, structured: Runnable) -> Report:
    prompt_data = [
        {
            "reason": reason.reason,
            "reasonings": reason.reasonings,
            "portfolio_commentary": reason.commentary,
        }
        for reason in evidence.reasons
    ]
    answer: Any = structured.invoke(
        [
            SystemMessage(REPORT_PROMPT),
            HumanMessage(str(prompt_data)),
        ]
    )
    if isinstance(answer, dict) and "parsed" in answer:
        answer = answer["parsed"]
    thoughts = Thoughts.model_validate(answer)
    if not thoughts.thoughts:
        raise ValueError("thoughts must not be empty")
    return Report(news=evidence.news, thoughts=thoughts.thoughts)
