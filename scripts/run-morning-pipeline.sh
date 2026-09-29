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
  "${compose[@]}" run --rm -T "$service" </dev/null
  echo "completed scheduled job: $service"
}

run_job market-syncer
run_job market-collector
run_job news-preprocessor
run_job news-clusterer
run_job news-graph-builder
run_job portfolio-builder
