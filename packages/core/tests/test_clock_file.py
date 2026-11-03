from datetime import datetime

import pytest
from ktb_core.clock_file import read_clock, write_clock
from ktb_core.krx import KST


def test_clock_round_trips_and_replaces(tmp_path):
    path = tmp_path / "as_of"
    write_clock(path, datetime(2026, 7, 6, 8, tzinfo=KST))
    write_clock(path, datetime(2026, 7, 7, 8, tzinfo=KST))
    assert read_clock(path) == datetime(2026, 7, 7, 8, tzinfo=KST)
    assert [p.name for p in tmp_path.iterdir()] == ["as_of"]


def test_clock_rejects_naive_times(tmp_path):
    path = tmp_path / "as_of"
    with pytest.raises(ValueError):
        write_clock(path, datetime(2026, 7, 6, 8))
    path.write_text("2026-07-06T08:00:00")
    with pytest.raises(ValueError):
        read_clock(path)


def test_missing_clock_raises(tmp_path):
    with pytest.raises(OSError):
        read_clock(tmp_path / "as_of")
