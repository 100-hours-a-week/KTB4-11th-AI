"""OpenAPI documentation for named SSE events."""

import json
from typing import Any

from pydantic import BaseModel


def sse_openapi(event: str, model: type[BaseModel], example: dict[str, Any]) -> dict[str, Any]:
    item_schema = {
        "type": "object",
        "required": ["event", "data"],
        "properties": {
            "event": {"type": "string", "const": event},
            "data": {
                "type": "string",
                "contentMediaType": "application/json",
                "contentSchema": {"$ref": f"#/components/schemas/{model.__name__}"},
            },
        },
    }
    wire_example = f"event: {event}\ndata: {json.dumps(example, ensure_ascii=False)}\n\n"
    return {
        "responses": {
            "200": {
                "content": {
                    "text/event-stream": {
                        "itemSchema": item_schema,
                        "schema": {"type": "string", "examples": [wire_example]},
                        "example": wire_example,
                    }
                }
            }
        }
    }
