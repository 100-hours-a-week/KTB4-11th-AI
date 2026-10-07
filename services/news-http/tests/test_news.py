from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa


def test_recent_news_returns_only_recent_clusters_and_aggregates_mentions(client, engine, seed):
    now = datetime.now(UTC)
    recent_article = seed.article(now)
    recent_id = seed.cluster([recent_article], "recent")
    duplicate_article = seed.article(now)
    duplicate_cluster_id = seed.cluster([duplicate_article], "second")
    stale_article = seed.article(now)
    stale_id = seed.cluster([stale_article], "stale")
    company = seed.stock("005930")
    seed.mention(recent_id, company)
    seed.mention(duplicate_cluster_id, company)
    with engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE clusters SET updated_at = now() - interval '8 days' WHERE id = :id"),
            {"id": stale_id},
        )
        major_code, other_code = f"{uuid4()}-major", f"{uuid4()}-other"
        conn.execute(
            sa.text(
                "INSERT INTO themes (theme_code, name)"
                " VALUES (:major, 'A-theme'), (:other, 'Z-theme')"
            ),
            {"major": major_code, "other": other_code},
        )
        conn.execute(
            sa.text(
                "INSERT INTO theme_companies (theme_code, stock_code, is_major)"
                " VALUES (:other, '005930', false), (:major, '005930', true)"
            ),
            {"major": major_code, "other": other_code},
        )

    response = client.get("/news/recent", params={"days": 7})

    assert response.status_code == 200
    assert [row["id"] for row in response.json()["clusters"]] == [
        duplicate_cluster_id,
        recent_id,
    ]
    assert response.json()["companies"] == [
        {
            "company_id": "005930",
            "name": "name 005930",
            "stock_code": "005930",
            "cluster_ids": [recent_id, duplicate_cluster_id],
            "themes": ["A-theme (main)", "Z-theme"],
        }
    ]
    assert response.json()["theme_count"] == 2


def test_recent_news_empty_window_returns_empty_values(client, engine, seed):
    article_id = seed.article(datetime.now(UTC))
    cluster_id = seed.cluster([article_id], "stale")
    with engine.begin() as conn:
        conn.execute(
            sa.text("UPDATE clusters SET updated_at = now() - interval '8 days' WHERE id = :id"),
            {"id": cluster_id},
        )

    assert client.get("/news/recent", params={"days": 7}).json() == {
        "clusters": [],
        "companies": [],
        "theme_count": 0,
    }


def test_recent_news_requires_positive_days(client):
    for days in (0, -1):
        assert client.get("/news/recent", params={"days": days}).status_code == 422
