import importlib.util
from io import BytesIO
from pathlib import Path

import pytest


@pytest.fixture
def inspect_module():
    path = Path(__file__).parents[1] / "inspect_checkpoint.py"
    spec = importlib.util.spec_from_file_location("inspect_checkpoint", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "options",
    [{"table": "bars'; DROP TABLE bars"}, {"repeats": 0}, {"minute_days": -1}, {"daily_days": 0}],
)
def test_invalid_inspection_arguments_do_not_query_the_database(inspect_module, options):
    with pytest.raises(ValueError):
        inspect_module.inspect_checkpoint(None, **options)


def test_metrics_snapshot_reports_heap_rss_and_gc(inspect_module, monkeypatch):
    body = b"""# HELP questdb_memory_jvm_total Total heap
questdb_memory_jvm_total 1024
questdb_memory_jvm_free 256
questdb_memory_rss 4096
questdb_jvm_major_gc_count_total 2
other_metric 100
"""
    monkeypatch.setattr(inspect_module, "urlopen", lambda url, timeout: BytesIO(body))
    samples, errors = [], []
    inspect_module._metrics("http://metrics", samples, errors)
    assert not errors
    assert samples == [
        {
            "questdb_memory_jvm_total": 1024.0,
            "questdb_memory_jvm_free": 256.0,
            "questdb_memory_rss": 4096.0,
            "questdb_jvm_major_gc_count_total": 2.0,
            "jvm_used_bytes": 768.0,
        }
    ]


def test_unavailable_metrics_are_reported_without_aborting(inspect_module, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError("metrics unavailable")

    monkeypatch.setattr(inspect_module, "urlopen", unavailable)
    samples, errors = [], []
    inspect_module._metrics("http://metrics", samples, errors)
    assert samples == []
    assert errors == ["metrics unavailable"]
