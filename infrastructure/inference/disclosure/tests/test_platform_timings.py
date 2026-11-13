from types import SimpleNamespace

from platform_timings import call_timings


def test_provider_startup_is_separate_from_execution_and_queue():
    entry = SimpleNamespace(
        input_id="in-1",
        task_id="ta-1",
        started_at=120.0,
        finished_at=125.0,
        task_first_input=True,
        task_startup_time=7.5,
    )
    call = SimpleNamespace(function_call_id="fc-1", created_at=100.0)
    result = call_timings(call, entry)
    assert result["cold_start_ms"] == 7500
    assert result["provider_execution_ms"] == 5000
    assert result["provider_wait_ms"] == 20000
    assert result["function_call_id"] == "fc-1"


def test_warm_input_does_not_recount_container_startup():
    entry = SimpleNamespace(
        input_id="in-2",
        task_id="ta-1",
        started_at=130.0,
        finished_at=131.0,
        task_first_input=False,
        task_startup_time=7.5,
    )
    call = SimpleNamespace(function_call_id="fc-2", created_at=129.0)
    assert call_timings(call, entry)["cold_start_ms"] == 0
