from datetime import datetime


def test_cluster_detail_includes_news_graph_and_nullable_company(client, seed, at):
    stock = seed.stock("005930")
    concept = seed.entity("HBM")
    article = seed.article(at(3), title="HBM demand")
    cluster = seed.cluster([article], title="Semiconductor")
    seed.mention(cluster, stock)
    seed.mention(cluster, concept)
    with seed.engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO relations"
            " (cluster_id, source_entity_id, target_entity_id, type, description)"
            " VALUES (%s, %s, %s, 'drives', 'demand increases')",
            (cluster, stock, concept),
        )

    response = client.get(f"/clusters/{cluster}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == cluster
    assert body["title"] == "Semiconductor"
    assert body["summary"] == "Semiconductor summary"
    assert datetime.fromisoformat(body["updated_at"]).utcoffset() is not None
    [item] = body["articles"]
    assert item["title"] == "HBM demand"
    assert item["source"] == "test"
    assert datetime.fromisoformat(item["published_at"]) == at(3)
    assert body["entities"] == [
        {"id": stock, "name": "name 005930", "type": "company", "company_id": "005930"},
        {"id": concept, "name": "HBM", "type": "concept", "company_id": None},
    ]
    assert body["relations"] == [
        {
            "source": "name 005930",
            "type": "drives",
            "target": "HBM",
            "description": "demand increases",
        }
    ]


def test_missing_or_unsummarized_cluster_is_404(client, seed, at):
    unsummarized = seed.cluster([seed.article(at(1))], title=None)

    assert client.get(f"/clusters/{unsummarized}").status_code == 404
    assert client.get("/clusters/999999").status_code == 404


def test_cluster_detail_requires_positive_bigint_id(client):
    assert client.get("/clusters/0").status_code == 422
    assert client.get(f"/clusters/{2**63}").status_code == 422


def test_existing_article_route_is_unchanged(client, seed, at):
    article = seed.article(at(1))
    cluster = seed.cluster([article])

    response = client.get(f"/clusters/{cluster}/articles")

    assert response.status_code == 200
    assert set(response.json()["items"][0]) == {
        "id",
        "title",
        "url",
        "source",
        "published_at",
    }
