import json
from typing import Any

from pydantic import BaseModel


def sse_openapi(
    event: str,
    model: type[BaseModel],
    example: dict[str, Any],
    live_model: type[BaseModel] | None = None,
) -> dict[str, Any]:
    wire_example = f"event: {event}\ndata: {json.dumps(example, ensure_ascii=False)}\n\n"
    return {
        "responses": {
            "200": {
                "content": {
                    "text/event-stream": {
                        "itemSchema": {
                            "type": "object",
                            "required": ["event", "data"],
                            "properties": {
                                "event": {"type": "string", "const": event},
                                "data": {
                                    "type": "string",
                                    "contentMediaType": "application/json",
                                    "contentSchema": (
                                        {
                                            "oneOf": [
                                                {"$ref": f"#/components/schemas/{model.__name__}"},
                                                live_model.model_json_schema(),
                                            ]
                                        }
                                        if live_model
                                        else {"$ref": f"#/components/schemas/{model.__name__}"}
                                    ),
                                },
                            },
                        },
                        "schema": {"type": "string", "examples": [wire_example]},
                        "example": wire_example,
                    }
                }
            }
        }
    }
