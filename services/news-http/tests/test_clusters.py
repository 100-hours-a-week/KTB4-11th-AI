from datetime import datetime

import pytest


def ids(response) -> list[int]:
    return [item["id"] for item in response.json()["items"]]


def test_lists_a_cluster_that_mentions_the_stock(client, seed, at):
    stock = seed.stock("005930")
    cluster = seed.cluster([seed.article(at(1)), seed.article(at(3))], title="반도체")
    seed.mention(cluster, stock)
    seed.cluster([seed.article(at(2))])

    response = client.get("/stocks/005930/clusters")

    assert response.status_code == 200
    body = response.json()
    assert body["next_cursor"] is None
    [item] = body["items"]
    assert item["id"] == cluster
    assert item["title"] == "반도체"
    assert item["summary"] == "반도체 summary"
    assert item["article_count"] == 2
    assert datetime.fromisoformat(item["latest_published_at"]) == at(3)
    assert datetime.fromisoformat(item["earliest_published_at"]) == at(1)


def test_newest_first_and_unsummarized_clusters_are_left_out(client, seed, at):
    stock = seed.stock("005930")
    older = seed.cluster([seed.article(at(1))])
    newer = seed.cluster([seed.article(at(5))])
    unsummarized = seed.cluster([seed.article(at(9))], title=None)
    for cluster in (older, newer, unsummarized):
        seed.mention(cluster, stock)

    response = client.get("/stocks/005930/clusters")

    assert ids(response) == [newer, older]


def test_other_entities_do_not_inflate_the_article_count(client, seed, at):
    stock = seed.stock("005930")
    cluster = seed.cluster([seed.article(at(1)), seed.article(at(2))])
    seed.mention(cluster, stock)
    seed.mention(cluster, seed.entity("HBM"))
    seed.mention(cluster, seed.entity("AI"))

    [item] = client.get("/stocks/005930/clusters").json()["items"]

    assert item["article_count"] == 2


def test_unknown_stock_is_an_empty_page(client, seed):
    response = client.get("/stocks/000000/clusters")

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


def test_walking_the_cursor_returns_every_cluster_once(client, seed, at):
    stock = seed.stock("005930")
    tied = [seed.cluster([seed.article(at(4))]) for _ in range(3)]
    others = [seed.cluster([seed.article(at(day))]) for day in (6, 2)]
    for cluster in (*tied, *others):
        seed.mention(cluster, stock)

    seen: list[int] = []
    cursor = None
    while True:
        params: dict[str, str | int] = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        body = client.get("/stocks/005930/clusters", params=params).json()
        seen += [item["id"] for item in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert seen == [others[0], *sorted(tied, reverse=True), others[1]]


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "cursor=!!!"])
def test_invalid_query_is_a_422(client, query):
    assert client.get(f"/stocks/005930/clusters?{query}").status_code == 422


def test_search_matches_every_prefix_and_orders_by_rank(client, seed, at):
    first = seed.cluster([seed.article(at(1))], title="삼성전자 반도체 실적")
    second = seed.cluster([seed.article(at(2))], title="삼성전자 반도체 반도체 반도체 전망")
    seed.cluster([seed.article(at(3))], title="삼성전자 배터리")

    response = client.get("/clusters/search", params={"q": "삼성전자,반도"})

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [second, first]
    assert response.json()[0]["excerpt"] == "삼성전자 반도체 반도체 반도체 전망 summary"
    assert response.json()[0]["rank"] > response.json()[1]["rank"]
    assert set(response.json()[0]) == {"id", "title", "excerpt", "rank"}


@pytest.mark.parametrize("query", ["q=삼성전자,", "q=삼성전자%20반도체", "q=,삼성전자"])
def test_search_rejects_invalid_terms(client, query):
    assert client.get(f"/clusters/search?{query}").status_code == 422


def test_search_with_empty_query_is_422(client):
    response = client.get("/clusters/search", params={"q": ""})

    assert response.status_code == 422


def test_search_with_no_matches_returns_empty_list(client):
    response = client.get("/clusters/search", params={"q": "unmatched"})

    assert response.status_code == 200
    assert response.json() == []
