# HTTP User Polling Restoration Implementation Plan

> **For agentic workers:** Use executing-plans inline for this authorized correction.

**Goal:** Restore existing HTTP user/account reads and cancellation; retain SQS order creation and HTTP read failure notifications.

**Architecture:** Backend.users and Backend.cancel keep the dev implementation. Each tick polls HTTP, cancels pending orders, polls HTTP again after successful cancellation, and recalculates SQS replacement orders. Account queue consumption and durable snapshot recovery are removed.

**Tech Stack:** Python 3.13/3.14, httpx, boto3, existing ktb_core auth/logging/queue.

**Spec:** Latest user instruction in this session: 사용자 정보 조회도 HTTP로 그대로 유지하자.

## Global Constraints
- Keep cancellation and user fetch methods identical to dev.
- Keep account-based FIFO groups and eventId deduplication for SQS order requests.
- Notify HTTP user fetch failures through the ordinary failure queue; never a DLQ.
- No runtime migration or access keys; keep merged report-builder unchanged.
- Update both open PRs and preserve #245 Draft.

## Review Focus
- Empty users is valid and produces no trades.
- HTTP timeout/status/invalid JSON stop order creation and report failure.
- Refresh failure after cancellation stops replacement orders.
- Pending orders remaining after refresh defer that account.
- Failure queue outage is logged and exits 1 without placing orders.

## Tasks
- [ ] Add failing Backend.users HTTP/auth and failure-message tests; restore the existing method.
- [ ] Restore main HTTP reads, adapt lifecycle tests, and remove account-queue settings/source/tests.
- [ ] Remove #245 snapshot state migration/tests and account queue infrastructure; update contracts and docs.
- [ ] Run full tests, lint/isolation/template checks, review, publish both PRs and verify CI.

## Execution
Existing isolated worktrees are reused. Publication is authorized by the ongoing PR-edit task. Snapshot event persistence is obsolete once the inbound event stream is removed; fresh HTTP polling supplies each tick and post-cancellation state.

## Final scope correction (2026-10-12)

The latest user instruction supersedes failure-notification parts above. User lookup remains HTTP with structured error logs and exit 1. No failure queue URL, notification publisher, failure contract, DLQ resources or failure-queue alarms are required. SQS carries only order.cancel/order.place.
