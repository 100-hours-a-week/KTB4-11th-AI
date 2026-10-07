import sqlalchemy as sa
from fastapi.testclient import TestClient
from news_http.app import create_app


def test_health_does_not_touch_the_database():
    engine = sa.create_engine("postgresql+psycopg://nobody@127.0.0.1:1/none")

    with TestClient(create_app(engine)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
