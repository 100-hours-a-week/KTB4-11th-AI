import json
from collections.abc import Sequence
from typing import NamedTuple

import httpx

SYSTEM_PROMPT = (
    "당신은 경제 뉴스 편집자입니다. 같은 사건을 다룬 기사들이 주어집니다. "
    "사건을 대표하는 한 줄 제목과 3~5문장 요약을 한국어로 작성하세요. "
    "기사에 나오는 주요 개체(기업, 인물, 기관, 국가, 정책, 제품 등)를 최대 {max_entities}개, "
    "개체 사이의 관계를 최대 {max_relations}개 한국어로 추출하세요. "
    "관계의 source와 target에는 entities의 name을 그대로 쓰세요."
)

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


class Entity(NamedTuple):
    name: str
    type: str


class Relation(NamedTuple):
    source: str
    target: str
    type: str
    description: str


class Extraction(NamedTuple):
    title: str
    summary: str
    entities: list[Entity]
    relations: list[Relation]


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

    response = client.post(
        f"{base_uri.rstrip('/')}/chat/completions",
        json={
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT.format(
                        max_entities=max_entities, max_relations=max_relations
                    ),
                },
                {"role": "user", "content": "\n\n---\n\n".join(blocks)},
            ],
            "response_format": RESPONSE_FORMAT,
        },
        timeout=timeout,
    )
    content = response.raise_for_status().json()["choices"][0]["message"]["content"]
    try:
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
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(f"LLM reply does not match the schema: {content!r}") from error
    fields = [
        extraction.title,
        extraction.summary,
        *(field for entity in extraction.entities for field in entity),
        *(field for relation in extraction.relations for field in relation),
    ]
    if not all(isinstance(field, str) for field in fields):
        raise ValueError(f"LLM reply has non-string fields: {content!r}")
    return extraction
