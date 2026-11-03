#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  echo "usage: $0 SPEC_JSON none|market|raw|enriched equal-weight|buy-and-hold|jev-stub|docker-agent" >&2
  exit 64
}

[[ $# -eq 3 ]] || usage
spec=$(realpath "$1")
information=$2
decision=$3

case "$information" in
  none) layers=(information.none.yaml) ;;
  market) layers=(information.market.yaml) ;;
  raw) layers=(information.market.yaml information.news.yaml) ;;
  enriched) layers=(information.market.yaml information.news.yaml information.features.yaml) ;;
  *) usage ;;
esac
case "$decision" in
  equal-weight | buy-and-hold | jev-stub)
    layers+=(decision.rule.yaml)
    export EVALUATION_DECISION=$decision
    ;;
  docker-agent) layers+=(decision.docker-agent.yaml) ;;
  *) usage ;;
esac

cd "$(dirname "$0")/.."
results=${EVALUATION_RESULTS_DIR:-evaluations/results}
mkdir -p "$results"
dirty=$(git diff --quiet HEAD -- || echo "-dirty")
export EVALUATION_SPEC=$spec
export EVALUATION_RESULTS_DIR=$(realpath "$results")
export EVALUATION_RUN_ID=${EVALUATION_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$information-$decision-$RANDOM}
export EVALUATION_CODE_COMMIT=$(git rev-parse HEAD)$dirty
export EVALUATION_UID=$(id -u)
export EVALUATION_GID=$(id -g)

project=ktb4-eval-$(tr -c 'a-z0-9\n' '-' <<<"${EVALUATION_RUN_ID,,}")
compose=(docker compose -p "$project" -f compose.evaluate.yaml)
if [[ -f ${EVALUATION_ENV_FILE:-.env} ]]; then
  compose+=(--env-file "${EVALUATION_ENV_FILE:-.env}")
fi
for layer in "${layers[@]}"; do
  compose+=(-f "evaluations/compose/$layer")
done

cleanup() {
  "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

"${compose[@]}" build
mapfile -t images < <("${compose[@]}" config --images | sort -u)
EVALUATION_IMAGE_DIGEST=$(docker image inspect --format '{{index .RepoTags 0}}={{.Id}}' "${images[@]}" | paste -sd, -)
export EVALUATION_IMAGE_DIGEST

status=0
"${compose[@]}" up --no-build --abort-on-container-exit --exit-code-from runner || status=$?
echo "run $EVALUATION_RUN_ID exited with $status; records in $EVALUATION_RESULTS_DIR"
exit "$status"
