#!/usr/bin/env bash
set -Eeuo pipefail

timeout_seconds="${1:-0}"
poll_seconds=5
marker=".market-syncer-success"

if ! [[ "$timeout_seconds" =~ ^[0-9]+$ ]]; then
  echo "timeout must be a non-negative integer: $timeout_seconds" >&2
  exit 2
fi

today_start_epoch="$(TZ=Asia/Seoul date -d 'today 00:00:00' +%s)"
deadline_epoch="$(( $(date +%s) + timeout_seconds ))"

while true; do
  if [[ -f "$marker" ]]; then
    marker_epoch="$(stat -c %Y "$marker")"
    if (( marker_epoch >= today_start_epoch )); then
      echo "market-syncer freshness check passed: $marker"
      exit 0
    fi
  fi

  now_epoch="$(date +%s)"
  if (( now_epoch >= deadline_epoch )); then
    echo "market-syncer has no successful run for the current Asia/Seoul date" >&2
    exit 1
  fi

  sleep "$poll_seconds"
done
