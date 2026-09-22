import json
import threading

from market_collector.cursor import Cursor, CursorStore


def test_an_unknown_pair_starts_from_the_beginning(tmp_path):
    store = CursorStore(tmp_path / "c.json")

    assert store.get("005930", "1m") == Cursor(next_key=None, oldest=None, pages=0, done=False)


def test_advancing_records_the_key_and_counts_the_page(tmp_path):
    store = CursorStore(tmp_path / "c.json")

    store.advance("005930", "1m", "NK1", "20260921104300")
    cursor = store.advance("005930", "1m", "NK2", "20260917170500")

    assert cursor.next_key == "NK2"
    assert cursor.oldest == "20260917170500"
    assert cursor.pages == 2
    assert cursor.done is False


def test_state_survives_a_new_store_on_the_same_file(tmp_path):
    path = tmp_path / "c.json"
    CursorStore(path).advance("005930", "1m", "NK1", "20260921104300")

    assert CursorStore(path).get("005930", "1m").next_key == "NK1"


def test_finishing_marks_done_and_clears_the_key(tmp_path):
    store = CursorStore(tmp_path / "c.json")
    store.advance("005930", "1m", "NK1", "20260921104300")

    cursor = store.finish("005930", "1m")

    assert cursor.done is True
    assert cursor.next_key is None


def test_pending_lists_only_unfinished_pairs(tmp_path):
    store = CursorStore(tmp_path / "c.json")
    store.finish("005930", "1m")

    pending = store.pending(["005930", "000660"], ["1m", "1d"])

    assert ("005930", "1m") not in pending
    assert ("005930", "1d") in pending
    assert ("000660", "1m") in pending
    assert len(pending) == 3


def test_timeframes_are_tracked_independently(tmp_path):
    store = CursorStore(tmp_path / "c.json")

    store.advance("005930", "1m", "NKm", "20260921104300")

    assert store.get("005930", "1d").next_key is None


def test_the_file_is_readable_json(tmp_path):
    path = tmp_path / "c.json"
    store = CursorStore(path)
    store.advance("005930", "1m", "NK1", "20260921104300")

    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["005930|1m"]["next_key"] == "NK1"


def test_a_corrupt_file_is_replaced_rather_than_crashing_the_job(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{not json", encoding="utf-8")

    store = CursorStore(path)

    assert store.get("005930", "1m").pages == 0


def test_concurrent_advances_on_different_pairs_do_not_lose_data(tmp_path):
    path = tmp_path / "c.json"
    store = CursorStore(path)

    def worker(symbol_idx, timeframe_idx):
        symbol = f"symbol{symbol_idx}"
        timeframe = f"tf{timeframe_idx}"
        store.advance(symbol, timeframe, f"key{symbol_idx}", "20260921104300")
        store.finish(symbol, timeframe)

    threads = []
    for i in range(5):
        for j in range(2):
            t = threading.Thread(target=worker, args=(i, j))
            threads.append(t)
            t.start()

    for t in threads:
        t.join()

    reloaded = CursorStore(path)
    for i in range(5):
        for j in range(2):
            symbol = f"symbol{i}"
            timeframe = f"tf{j}"
            cursor = reloaded.get(symbol, timeframe)
            assert cursor.done is True, f"Missing or incomplete: {symbol}, {timeframe}"
            assert cursor.pages == 1, f"Wrong page count for {symbol}, {timeframe}"
