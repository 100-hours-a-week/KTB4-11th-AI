import json
from collections.abc import Sequence

import httpx

from news_graph_builder.graph.dto import Entity, Extraction, Relation

ENTITY_TYPES = ("기업", "인물", "기관", "국가", "정책", "제품", "산업", "지표")

# Structure and rules follow langchain-neo4j's LLMGraphTransformer prompt.
SYSTEM_PROMPT = """\
# 경제 뉴스 지식 그래프 추출 지침
## 1. 개요
당신은 경제 뉴스를 구조화해 지식 그래프를 만드는 편집자입니다. \
같은 사건을 다룬 기사 여러 건이 주어집니다.
- 사건을 대표하는 한 줄 제목과 3~5문장 요약을 한국어로 작성하세요.
- 기사에 명시된 정보만 사용하세요. 기사에 없는 내용을 추측하거나 덧붙이지 마세요.
- 정확성을 해치지 않는 선에서 최대한 많은 정보를 담되, 그래프는 단순하고 명확해야 합니다.
## 2. 개체
- 기사에 나온 기업, 인물, 기관, 국가, 정책, 제품 같은 대상을 최대 {max_entities}개 추출하세요.
- name에는 기사에 나온 사람이 읽을 수 있는 이름을 쓰세요. 숫자 ID를 만들지 마세요.
- type에는 기본적이고 일반적인 유형을 쓰세요. {entity_types} 중에서 고르고, 맞는 것이 없을 때만 \
같은 수준의 일반적인 유형을 새로 쓰세요. 인물은 '반도체 전문가'가 아니라 '인물'로, \
기업은 '반도체 제조사'가 아니라 '기업'으로 쓰세요.
## 3. 관계
- 두 개체 사이의 관계를 최대 {max_relations}개 추출하세요.
- source와 target에는 entities의 name을 글자 그대로 쓰세요.
- type에는 일반적이고 시간이 지나도 유효한 짧은 관계 유형을 쓰세요. '공급 계약을 체결함'보다 \
'공급', '대표로 선임됨'보다 '대표'처럼 쓰되, 일반화 때문에 정확성을 잃으면 안 됩니다.
- description에는 기사에 근거한 관계 설명을 한 문장으로 쓰세요.
## 4. 동일 개체 유지
- 같은 개체가 다른 이름이나 지칭으로 여러 번 나오면(예: '삼성', '이 회사') 가장 완전한 이름 \
하나(예: '삼성전자')만 쓰세요.
- 인물은 직함 없이 이름만 쓰세요(예: '이재용 회장' → '이재용').
## 5. 엄격 준수
- 주어진 JSON 형식으로만 답하고 설명을 덧붙이지 마세요."""

EXAMPLE_ARTICLE = (
    "SK하이닉스가 엔비디아에 HBM3E를 공급한다. 곽노정 SK하이닉스 사장은 3일 실적 발표에서 "
    '"올해 HBM 물량은 이미 매진됐다"고 말했다. 회사는 청주 공장 증설에 20조원을 투자한다.'
)

EXAMPLE_REPLY = {
    "title": "SK하이닉스, 엔비디아에 HBM3E 공급…청주 공장에 20조원 투자",
    "summary": (
        "SK하이닉스가 엔비디아에 HBM3E를 공급한다. 곽노정 사장은 올해 HBM 물량이 이미 "
        "매진됐다고 밝혔다. SK하이닉스는 청주 공장 증설에 20조원을 투자한다."
    ),
    "entities": [
        {"name": "SK하이닉스", "type": "기업"},
        {"name": "엔비디아", "type": "기업"},
        {"name": "HBM3E", "type": "제품"},
        {"name": "곽노정", "type": "인물"},
    ],
    "relations": [
        {
            "source": "SK하이닉스",
            "target": "엔비디아",
            "type": "공급",
            "description": "SK하이닉스가 엔비디아에 HBM3E를 공급한다.",
        },
        {
            "source": "곽노정",
            "target": "SK하이닉스",
            "type": "대표",
            "description": "곽노정은 SK하이닉스의 사장이다.",
        },
    ],
}

USER_PROMPT = """\
다음 예시처럼 기사에서 제목, 요약, 개체, 관계를 추출하세요.

# 예시 기사
{example_article}

# 예시 출력
{example_reply}

# 기사
{articles}"""

STRING = {"type": "string"}


def _object(*names: str) -> dict:
    return {
        "type": "object",
        "properties": dict.fromkeys(names, STRING),
        "required": list(names),
        "additionalProperties": False,
    }


RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "cluster_graph",
        "schema": {
            "type": "object",
            "properties": {
                "title": STRING,
                "summary": STRING,
                "entities": {"type": "array", "items": _object("name", "type")},
                "relations": {
                    "type": "array",
                    "items": _object("source", "target", "type", "description"),
                },
            },
            "required": ["title", "summary", "entities", "relations"],
            "additionalProperties": False,
        },
    },
}


def extract(
    client: httpx.Client,
    articles: Sequence[tuple[str, str]],
    *,
    base_uri: str,
    model: str,
    max_chars: int,
    timeout: float,
    max_entities: int,
    max_relations: int,
) -> Extraction:
    blocks: list[str] = []
    used = 0
    for title, body in articles:
        block = f"{title}\n\n{body}"
        if blocks and used + len(block) > max_chars:
            break
        blocks.append(block[:max_chars])
        used += len(block)

    system_prompt = SYSTEM_PROMPT.format(
        max_entities=max_entities,
        max_relations=max_relations,
        entity_types=", ".join(f"'{entity_type}'" for entity_type in ENTITY_TYPES),
    )
    user_prompt = USER_PROMPT.format(
        example_article=EXAMPLE_ARTICLE,
        example_reply=json.dumps(EXAMPLE_REPLY, ensure_ascii=False, indent=2),
        articles="\n\n---\n\n".join(blocks),
    )
    response = client.post(
        f"{base_uri.rstrip('/')}/chat/completions",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": RESPONSE_FORMAT,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    content = None
    try:
        content = response.json()["choices"][0]["message"]["content"]
        reply = json.loads(content)
        extraction = Extraction(
            reply["title"],
            reply["summary"],
            [Entity(item["name"], item["type"]) for item in reply["entities"]][:max_entities],
            [
                Relation(item["source"], item["target"], item["type"], item["description"])
                for item in reply["relations"]
            ][:max_relations],
        )
    except (ValueError, KeyError, TypeError, IndexError) as error:
        msg = content if content is not None else response.text
        raise ValueError(f"LLM reply does not match the schema: {msg!r}") from error
    fields = [
        extraction.title,
        extraction.summary,
        *(field for entity in extraction.entities for field in entity),
        *(field for relation in extraction.relations for field in relation),
    ]
    if not all(isinstance(field, str) for field in fields):
        raise ValueError(f"LLM reply has non-string fields: {content!r}")
    return extraction
