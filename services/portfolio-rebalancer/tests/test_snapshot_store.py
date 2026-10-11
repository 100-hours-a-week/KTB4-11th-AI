from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

NOW = datetime(2026, 10, 11, 1, tzinfo=UTC)


@pytest.fixture
def store():
    from portfolio_rebalancer.snapshot_store import SnapshotStore, metadata, states

    engine = sa.create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(states.insert().values(id=1, usable=False))
    yield SnapshotStore(engine)
    engine.dispose()


def test_a_committed_snapshot_survives_a_new_store_instance(store):
    from portfolio_rebalancer.snapshot_store import SnapshotStore

    saved, result = store.accept("e1", NOW, {"users": []}, "c1", valid_time=True)
    restored = SnapshotStore(store.engine).load()

    assert result == "applied"
    assert restored == saved
    assert restored.payload == {"users": []}
    assert restored.usable


def test_late_older_snapshots_do_not_roll_back_the_watermark(store):
    store.accept("new", NOW, {"users": []}, "c1", valid_time=True)
    saved, result = store.accept(
        "old", NOW - timedelta(seconds=1), {"users": [1]}, "c2", valid_time=True
    )

    assert result == "outdated"
    assert saved.event_id == "new"
    assert store.load().payload == {"users": []}


def test_equal_timestamp_with_a_different_event_does_not_replace_state(store):
    store.accept("first", NOW, {"users": []}, "c1", valid_time=True)
    saved, result = store.accept("second", NOW, {"users": [1]}, "c2", valid_time=True)

    assert result == "outdated"
    assert saved.event_id == "first"


def test_duplicate_event_after_restart_cannot_replace_a_newer_snapshot(store):
    from portfolio_rebalancer.snapshot_store import SnapshotStore

    store.accept("e1", NOW, {"users": []}, "c1", valid_time=True)
    store.accept("e2", NOW + timedelta(seconds=1), {"users": [2]}, "c2", valid_time=True)
    saved, result = SnapshotStore(store.engine).accept(
        "e1", NOW + timedelta(seconds=2), {"users": [1]}, "c1", valid_time=True
    )

    assert result == "duplicate"
    assert saved.event_id == "e2"


def test_claim_makes_snapshot_unusable_before_side_effects(store):
    store.accept("e1", NOW, {"users": []}, "c1", valid_time=True)

    assert store.claim("e1")
    assert not store.load().usable
    assert not store.claim("e1")


def test_a_newer_snapshot_cannot_be_claimed_with_an_old_event_id(store):
    store.accept("e1", NOW, {"users": []}, "c1", valid_time=True)
    store.accept("e2", NOW + timedelta(seconds=1), {"users": []}, "c2", valid_time=True)

    assert not store.claim("e1")
    assert store.load().usable


@pytest.fixture
def postgres_state(pg_engine):
    from portfolio_rebalancer.snapshot_store import SnapshotStore, events, states

    with pg_engine.begin() as conn:
        conn.execute(events.delete())
        conn.execute(states.delete())
        conn.execute(states.insert().values(id=1, usable=False))
    yield SnapshotStore(pg_engine)
    with pg_engine.begin() as conn:
        conn.execute(events.delete())
        conn.execute(states.delete())
        conn.execute(states.insert().values(id=1, usable=False))


def test_postgres_concurrent_claim_allows_only_one_publisher(postgres_state):
    from concurrent.futures import ThreadPoolExecutor

    postgres_state.accept("e1", NOW, {"users": []}, "c1", valid_time=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(postgres_state.claim, ["e1", "e1"]))

    assert sorted(results) == [False, True]
    assert not postgres_state.load().usable


def test_postgres_row_lock_keeps_the_newest_concurrent_snapshot(postgres_state):
    from concurrent.futures import ThreadPoolExecutor

    def accept(index):
        return postgres_state.accept(
            f"e{index}", NOW + timedelta(seconds=index), {"users": []}, f"c{index}", valid_time=True
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(accept, [2, 1]))

    assert postgres_state.load().event_id == "e2"
