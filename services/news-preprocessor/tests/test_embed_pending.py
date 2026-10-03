from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import news_preprocessor.embed_pending as embed_pending_module
import sqlalchemy as sa
from news_preprocessor.embed_pending import embed_pending
from news_preprocessor.sources import NewsItem
from news_preprocessor.storage import articles, embedding_failures, insert_new

VECTOR = [1.0] + [0.0] * 1999


def _url(number: int) -> str:
    return f"https://example.test/{number}"


def fake_embedder(texts: list[str]) -> list[list[float]]:
    return [VECTOR for _ in texts]


def failing_embedder(texts: list[str]) -> list[list[float]]:
    raise httpx.ConnectError("embedding host unreachable")


def _insert(engine, count: int) -> None:
    with engine.begin() as conn:
        for number in range(count):
            insert_new(
                conn,
                NewsItem(
                    source="hankyung_economy",
                    external_id=_url(number),
                    url=_url(number),
                    title=f"제목 {number}",
                    published_at=datetime(2026, 9, 22, tzinfo=UTC),
                    raw_payload="<item/>",
                    body=f"본문 {number}",
                ),
            )


def _embeddings(engine):
    with engine.connect() as conn:
        query = sa.select(articles.c.embedding).order_by(articles.c.id)
        return list(conn.execute(query).scalars())


def test_embeds_every_pending_article_and_reports_their_ids(engine):
    _insert(engine, 20)

    result = embed_pending(engine, fake_embedder, limit=100)

    assert result.succeed == [_url(number) for number in range(20)]
    assert result.failed == []
    assert all(len(vector) == 2000 for vector in _embeddings(engine))


def test_sends_title_and_body_together(engine):
    _insert(engine, 1)
    sent = []

    embed_pending(engine, lambda texts: sent.extend(texts) or fake_embedder(texts), limit=100)

    assert sent == ["제목 0\n\n본문 0"]


def test_failure_reports_the_pending_ids_until_a_later_run(engine):
    _insert(engine, 3)

    result = embed_pending(engine, failing_embedder, limit=100)

    assert result.succeed == []
    assert result.failed == [_url(number) for number in range(3)]
    assert _embeddings(engine) == [None, None, None]

    assert embed_pending(engine, fake_embedder, limit=100).failed == []
    assert all(vector is not None for vector in _embeddings(engine))


def test_third_embedding_failure_moves_articles_to_dead_letter(engine):
    _insert(engine, 1)

    for _ in range(3):
        assert embed_pending(engine, failing_embedder, limit=100).failed == [_url(0)]

    with engine.connect() as conn:
        state = conn.execute(
            sa.select(
                articles.c.embedding_attempts,
                articles.c.embedding_error,
                articles.c.embedding_failed_at,
                articles.c.embedding_status,
            )
        ).one()
        failures = conn.execute(
            sa.select(embedding_failures.c.attempt, embedding_failures.c.error).order_by(
                embedding_failures.c.attempt
            )
        ).all()

    assert state.embedding_attempts == 3
    assert state.embedding_status == "dead_letter"
    assert state.embedding_error == "embedding host unreachable"
    assert state.embedding_failed_at is not None
    assert failures == [
        (1, "embedding host unreachable"),
        (2, "embedding host unreachable"),
        (3, "embedding host unreachable"),
    ]


def test_processes_every_pending_article_in_batch_sized_pages(engine):
    _insert(engine, 101)
    calls: list[list[str]] = []

    result = embed_pending(
        engine, lambda texts: calls.append(texts) or fake_embedder(texts), limit=100
    )

    assert len(result.succeed) == 101
    assert result.failed == []
    assert len(calls) == 8
    assert sum(vector is not None for vector in _embeddings(engine)) == 101


def test_fetches_next_page_until_the_initial_high_watermark(monkeypatch):
    rows = [
        SimpleNamespace(
            id=number,
            external_id=_url(number),
            title=f"제목 {number}",
            body=f"본문 {number}",
        )
        for number in range(101)
    ]
    pending = list(rows)

    class Context:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def begin_nested(self):
            return self

    class Engine:
        def connect(self):
            return Context()

        def begin(self):
            return Context()

    saved = False

    def get_high_watermark(_):
        return 100

    def get_pending(_, limit, high_watermark):
        return [row for row in pending if row.id <= high_watermark][:limit]

    def save_embeddings(_, ids, __):
        nonlocal saved
        pending[:] = [row for row in pending if row.id not in ids]
        if not saved:
            pending.append(
                SimpleNamespace(id=101, external_id=_url(101), title="제목 101", body="본문 101")
            )
            saved = True

    monkeypatch.setattr(
        embed_pending_module,
        "pending_embedding_high_watermark",
        get_high_watermark,
        raising=False,
    )
    monkeypatch.setattr(embed_pending_module, "pending_embedding", get_pending)
    monkeypatch.setattr(embed_pending_module, "set_embedding", save_embeddings)

    result = embed_pending_module.embed_pending(Engine(), fake_embedder, limit=100)

    assert result.succeed == [row.external_id for row in rows]
    assert [row.external_id for row in pending] == [_url(101)]


def test_marks_only_the_failed_embedding_request_for_retry(monkeypatch):
    rows = [
        SimpleNamespace(
            id=number,
            external_id=_url(number),
            title=f"제목 {number}",
            body=f"본문 {number}",
        )
        for number in range(32)
    ]
    saved: list[int] = []
    failed: list[int] = []
    calls = 0

    class Context:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def begin_nested(self):
            return self

    class Engine:
        def connect(self):
            return Context()

        def begin(self):
            return Context()

    def get_pending(_, __, ___):
        return rows if not saved and not failed else []

    def embedder(texts):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("embedding host unreachable")
        return fake_embedder(texts)

    monkeypatch.setattr(embed_pending_module, "pending_embedding_high_watermark", lambda _: 31)
    monkeypatch.setattr(embed_pending_module, "pending_embedding", get_pending)
    monkeypatch.setattr(embed_pending_module, "set_embedding", lambda _, ids, __: saved.extend(ids))
    monkeypatch.setattr(
        embed_pending_module, "mark_embedding_failed", lambda _, ids, __: failed.extend(ids)
    )

    result = embed_pending_module.embed_pending(Engine(), embedder, limit=100)

    assert result.succeed == [_url(number) for number in range(16)]
    assert result.failed == [_url(number) for number in range(16, 32)]
    assert saved == list(range(16))
    assert failed == list(range(16, 32))


def test_records_failure_after_a_vector_write_error_rolls_back_to_savepoint(monkeypatch):
    row = SimpleNamespace(id=1, external_id=_url(1), title="제목", body="본문")
    failed: list[int] = []
    nested_calls = 0

    class Context:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def begin_nested(self):
            nonlocal nested_calls
            nested_calls += 1
            return Context()

    class Engine:
        def connect(self):
            return Context()

        def begin(self):
            return Context()

    monkeypatch.setattr(embed_pending_module, "pending_embedding_high_watermark", lambda _: 1)
    monkeypatch.setattr(
        embed_pending_module, "pending_embedding", lambda _, __, ___: [row] if not failed else []
    )

    def fail_to_write(*_):
        raise ValueError("bad vector")

    monkeypatch.setattr(embed_pending_module, "set_embedding", fail_to_write)
    monkeypatch.setattr(
        embed_pending_module, "mark_embedding_failed", lambda _, ids, __: failed.extend(ids)
    )

    result = embed_pending_module.embed_pending(Engine(), fake_embedder, limit=100)

    assert result.failed == [_url(1)]
    assert nested_calls == 1
    assert failed == [1]
