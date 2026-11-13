import argparse
import asyncio
import json
import math


def call_timings(call, entry) -> dict:
    startup = entry.task_startup_time
    return {
        "message": "modal_platform_timing",
        "function_call_id": call.function_call_id,
        "input_id": entry.input_id,
        "container_id": entry.task_id,
        "first_request": entry.task_first_input,
        "cold_start_ms": round(startup * 1000, 3)
        if entry.task_first_input and math.isfinite(startup) and startup >= 0
        else 0
        if not entry.task_first_input
        else None,
        "provider_execution_ms": round((entry.finished_at - entry.started_at) * 1000, 3)
        if entry.finished_at >= entry.started_at > 0
        else None,
        "provider_wait_ms": round((entry.started_at - call.created_at) * 1000, 3)
        if entry.started_at >= call.created_at > 0
        else None,
        "source": "modal-sdk-1.3.5-FunctionCallList",
    }


async def main() -> None:
    import modal
    from modal_proto import api_pb2

    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--class-name", choices=("Embedder", "Reranker"), required=True)
    parser.add_argument("--method", required=True)
    parser.add_argument("--call-id")
    args = parser.parse_args()
    method = getattr(modal.Cls.from_name(args.app, args.class_name)(), args.method)
    await method.hydrate.aio()
    client = await modal.Client.from_env.aio()
    try:
        response = await asyncio.wait_for(
            client.stub.FunctionCallList(
                api_pb2.FunctionCallListRequest(function_id=method.object_id),
                retry=None,
                timeout=10,
            ),
            timeout=15,
        )
    except Exception as error:
        print(
            json.dumps(
                {
                    "message": "modal_platform_timing_unavailable",
                    "cold_start_ms": None,
                    "error_type": type(error).__name__,
                }
            ),
            flush=True,
        )
        return
    for call in response.function_calls:
        if args.call_id and call.function_call_id != args.call_id:
            continue
        for category in ("succeeded_inputs", "failed_inputs", "timeout_inputs"):
            for entry in getattr(call, category).latest:
                print(json.dumps({**call_timings(call, entry), "outcome": category}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
