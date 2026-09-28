from langchain_core.messages import AIMessage

TOKEN_KEYS = ("input", "output", "cache_read", "reasoning", "total")


def empty_usage() -> dict[str, float | None]:
    return {key: 0 for key in TOKEN_KEYS} | {"cost": None}


def message_usage(message: AIMessage) -> dict[str, float]:
    usage = message.usage_metadata or {}
    tokens: dict[str, float] = {
        "input": usage.get("input_tokens", 0),
        "output": usage.get("output_tokens", 0),
        "cache_read": (usage.get("input_token_details") or {}).get("cache_read") or 0,
        "reasoning": (usage.get("output_token_details") or {}).get("reasoning") or 0,
        "total": usage.get("total_tokens", 0),
    }
    cost = message.response_metadata.get("cost")
    return tokens if cost is None else tokens | {"cost": cost}


def add_usage(totals: dict[str, float | None], turn: dict[str, float]) -> None:
    for key in TOKEN_KEYS:
        totals[key] = (totals[key] or 0) + turn[key]
    if "cost" in turn:
        totals["cost"] = (totals["cost"] or 0.0) + turn["cost"]
