import sqlalchemy as sa
from fastapi.testclient import TestClient
from news_http.app import create_app


def test_health_does_not_touch_the_database():
    engine = sa.create_engine("postgresql+psycopg://nobody@127.0.0.1:1/none")

    with TestClient(create_app(engine)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_news_and_graph_openapi_responses_are_structured_and_exampled():
    engine = sa.create_engine("postgresql+psycopg://nobody@127.0.0.1:1/none")
    spec = create_app(engine).openapi()

    schemas = spec["components"]["schemas"]
    routes = {
        "/clusters/search": "ClusterSearchResult",
        "/clusters/{cluster_id}": "ClusterDetail",
        "/news/recent": "RecentNews",
        "/graph/neighborhood": "GraphNeighborhood",
        "/graph/paths": "GraphPaths",
    }
    for path, expected in routes.items():
        response_schema = spec["paths"][path]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        reference = response_schema.get("$ref") or response_schema["items"]["$ref"]
        assert reference == f"#/components/schemas/{expected}"
        schema = schemas[expected]
        assert schema["properties"]
        assert schema.get("additionalProperties") is not True
        example = schema["examples"][0]
        assert example
        assert example.keys() >= schema["properties"].keys()

    assert schemas["ClusterDetail"]["properties"]["updated_at"]["format"] == "date-time"
    assert schemas["ClusterEntity"]["properties"]["company_id"]["anyOf"][-1]["type"] == "null"
    assert schemas["GraphNode"]["properties"]["company_id"]["anyOf"][-1]["type"] == "null"
