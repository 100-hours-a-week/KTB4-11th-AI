from pathlib import Path

UNITS = Path(__file__).resolve().parents[1]


def test_one_daily_collection_starts_indexing_after_completion_or_final_failure():
    collector = (UNITS / "ktb-disclosure-collector.service.in").read_text()
    timer = (UNITS / "ktb-disclosure-collector.timer").read_text()
    indexer = (UNITS / "ktb-disclosure-indexer.service.in").read_text()

    assert "OnCalendar=*-*-* 05:00:00 Asia/Seoul" in timer
    assert "Unit=ktb-disclosure-collector.service" in timer
    for key in ["OnSuccess", "OnFailure"]:
        values = next(
            line.split("=", 1)[1].split()
            for line in collector.splitlines()
            if line.startswith(key + "=")
        )
        assert "ktb-disclosure-indexer.service" in values
    assert "ktb-disclosure-collector.service" not in indexer
    assert not (UNITS / "ktb-disclosure-indexer.timer").exists()
