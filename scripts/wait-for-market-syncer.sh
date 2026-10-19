#!/usr/bin/env bash
set -Eeuo pipefail

timeout_seconds="${1:-0}"
mode="${2:-always}"
poll_seconds=5
marker=".market-syncer-success"

if ! [[ "$timeout_seconds" =~ ^[0-9]+$ ]]; then
  echo "timeout must be a non-negative integer: $timeout_seconds" >&2
  exit 2
fi

if [[ "$mode" != "always" && "$mode" != "after-weekday-sync" ]]; then
  echo "unknown freshness mode: $mode" >&2
  exit 2
fi

now_epoch="$(date +%s)"
today_start_epoch="$(TZ=Asia/Seoul date -d 'today 00:00:00' +%s)"
sync_due_epoch="$(TZ=Asia/Seoul date -d 'today 05:55:00' +%s)"
weekday="$(TZ=Asia/Seoul date +%u)"
deadline_epoch="$(( now_epoch + timeout_seconds ))"

if [[ "$mode" == "after-weekday-sync" ]]; then
  if (( 10#$weekday >= 6 )) || (( now_epoch < sync_due_epoch )); then
    echo "market-syncer current-day freshness is not required yet"
    exit 0
  fi
fi

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
