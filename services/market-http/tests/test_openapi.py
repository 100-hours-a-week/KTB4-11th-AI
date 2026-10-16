import json

from market_http.app import create_app
from market_http.repositories.orderbooks import ORDERBOOK_EXAMPLE
from market_http.repositories.prices import PRICE_EXAMPLE
from redis.asyncio import Redis


def _schema():
    return create_app(Redis(host="127.0.0.1", port=1), "unused").openapi()


def test_sse_openapi_documents_named_event_payload_and_example():
    schema = _schema()
    for path, event, model, example in [
        ("/prices/stream", "price", "Price", PRICE_EXAMPLE),
        ("/orderbooks/stream", "orderbook", "Orderbook", ORDERBOOK_EXAMPLE),
    ]:
        response = schema["paths"][path]["get"]["responses"]["200"]
        media = response["content"]["text/event-stream"]
        item = media["itemSchema"]
        assert item["properties"]["event"]["const"] == event
        assert item["properties"]["data"]["contentMediaType"] == "application/json"
        assert item["properties"]["data"]["contentSchema"]["$ref"] == (
            f"#/components/schemas/{model}"
        )
        assert media["schema"] == {"type": "array", "items": item}
        shown = media["examples"][event]["value"][0]
        assert shown["event"] == event
        assert json.loads(shown["data"]) == example


def test_all_market_http_response_models_have_concrete_examples():
    schema = _schema()
    for model in (
        "Price",
        "OrderbookLevel",
        "Orderbook",
        "Bar",
        "Page",
        "IndexQuote",
        "RankingItem",
        "RankingResponse",
        "HealthResponse",
    ):
        examples = schema["components"]["schemas"][model]["examples"]
        assert examples
        assert isinstance(examples[0], dict)
        assert "property1" not in examples[0]

    ranking = schema["paths"]["/rankings/{kind}"]["get"]["responses"]["200"]
    assert ranking["content"]["application/json"]["schema"]["$ref"] == (
        "#/components/schemas/RankingResponse"
    )
