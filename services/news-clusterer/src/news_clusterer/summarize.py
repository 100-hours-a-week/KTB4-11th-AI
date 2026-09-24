import json
from collections.abc import Sequence

import httpx

SYSTEM_PROMPT = (
    "당신은 경제 뉴스 편집자입니다. 같은 사건을 다룬 기사들이 주어집니다. "
    "사건을 대표하는 한 줄 제목과 3~5문장 요약을 한국어로 작성하세요."
)

RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "cluster_summary",
        "schema": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "summary": {"type": "string"}},
            "required": ["title", "summary"],
            "additionalProperties": False,
        },
    },
}


def summarize(
    client: httpx.Client,
    articles: Sequence[tuple[str, str]],
    *,
    base_uri: str,
    model: str,
    max_chars: int,
    timeout: float,
) -> tuple[str, str]:
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
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "\n\n---\n\n".join(blocks)},
            ],
            "response_format": RESPONSE_FORMAT,
        },
        timeout=timeout,
    )
    content = response.raise_for_status().json()["choices"][0]["message"]["content"]
    try:
        reply = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError(f"LLM reply is not JSON: {content!r}") from error
    if not (isinstance(reply.get("title"), str) and isinstance(reply.get("summary"), str)):
        raise ValueError(f"LLM reply lacks a string title and summary: {content!r}")
    return reply["title"], reply["summary"]
