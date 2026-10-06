from datetime import datetime

from news_http.controllers.pagination import encode_cursor


def test_lists_the_articles_of_a_cluster_newest_first(client, seed, at):
    older = seed.article(at(1), title="older")
    newer = seed.article(at(2), title="newer")
    cluster = seed.cluster([older, newer])
    seed.cluster([seed.article(at(3))])

    response = client.get(f"/clusters/{cluster}/articles")

    assert response.status_code == 200
    body = response.json()
    assert body["next_cursor"] is None
    assert [item["id"] for item in body["items"]] == [newer, older]
    first = body["items"][0]
    assert set(first) == {"id", "title", "url", "source", "published_at"}
    assert first["title"] == "newer"
    assert first["source"] == "test"
    assert first["url"].startswith("https://example.com/")
    assert datetime.fromisoformat(first["published_at"]) == at(2)


def test_unknown_cluster_is_a_404(client, seed):
    assert client.get("/clusters/999/articles").status_code == 404


def test_paging_past_the_end_of_a_cluster_is_an_empty_page(client, seed, at):
    cluster = seed.cluster([seed.article(at(5))])

    response = client.get(
        f"/clusters/{cluster}/articles", params={"cursor": encode_cursor(at(1), 1)}
    )

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


def test_walking_the_cursor_returns_every_article_once(client, seed, at):
    tied = [seed.article(at(4)) for _ in range(3)]
    others = [seed.article(at(day)) for day in (6, 2)]
    cluster = seed.cluster([*tied, *others])

    seen: list[int] = []
    cursor = None
    while True:
        params: dict[str, str | int] = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        body = client.get(f"/clusters/{cluster}/articles", params=params).json()
        seen += [item["id"] for item in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert seen == [others[0], *sorted(tied, reverse=True), others[1]]


def test_invalid_query_is_a_422(client, seed, at):
    cluster = seed.cluster([seed.article(at(1))])

    for query in ("limit=0", "limit=101", "cursor=!!!"):
        assert client.get(f"/clusters/{cluster}/articles?{query}").status_code == 422
