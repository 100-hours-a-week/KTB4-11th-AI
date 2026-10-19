#!/usr/bin/env bash
set -Eeuo pipefail

compose=(
  docker compose
  --env-file .env
  --env-file .image.env
  -f compose.prod.yaml
)

run_job() {
  local service=$1

  echo "starting scheduled job: $service"
  if ! "${compose[@]}" run --rm -T "$service" </dev/null; then
    echo "failed scheduled job: $service" >&2
    return 1
  fi
  echo "completed scheduled job: $service"
}

market_syncer_attempt=1
market_syncer_max_attempts=3
market_syncer_retry_seconds=300

until run_job market-syncer; do
  if (( market_syncer_attempt >= market_syncer_max_attempts )); then
    echo "market-syncer failed after $market_syncer_max_attempts attempts" >&2
    exit 1
  fi

  echo "retrying market-syncer in ${market_syncer_retry_seconds}s" >&2
  sleep "$market_syncer_retry_seconds"
  ((market_syncer_attempt += 1))
done

touch .market-syncer-success

run_job news-clusterer
run_job news-graph-builder
run_job portfolio-builder
