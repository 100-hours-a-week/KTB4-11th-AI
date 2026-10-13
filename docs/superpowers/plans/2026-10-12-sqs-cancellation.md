# SQS Cancellation Implementation Plan

> **For agentic workers:** Execute inline with a final independent review.

**Goal:** Retain HTTP user lookup; publish cancellations and placements to the same account FIFO group.

**Architecture:** Orders publishes order.cancel with the existing cancellation payload. Main logs cancellation request publication separately from cancellation completion, then refreshes the current state through the original Backend.users. Place replacements only if refreshed pending orders have cleared. The same FIFO group preserves request order and the refreshed HTTP state confirms prerequisites.

**Tech Stack:** Existing Python/httpx/boto3/core queue and logging.

**Spec:** User instruction 2026-10-12: 취소도 SQS로 하자; 근거 댓글은 초안만 작성하고 게시하지 않기.

## Global Constraints
- Keep Backend.users identical to dev. No SQS account snapshot consumer or migration.
- Order queue uses account ID groups/eventId deduplication for both cancel and place.
- Publish success is request acceptance by SQS, not cancellation completion.
- HTTP fetch failure notifications remain ordinary Failure FIFO messages.
- Keep #245 Draft. No actual AWS/Backend deployment. Do not post any GitHub comment.

## Review Focus
- Cancel send failure skips that account's new orders.
- Partial cancel publication cannot produce replacements for that account.
- Refreshed pending orders defer replacements.
- HTTP refresh failure notifies and exits before placing orders.
- Cancellation MessageId/eventId/correlationId/results are logged without claiming completion.

## Tasks
- [ ] RED→GREEN: Orders.cancel payload and shared FIFO group.
- [ ] Update main, remove unused HTTP cancellation and adapt end-to-end tests.
- [ ] Update shared contracts/docs and PR descriptions, review, publish and verify full CI.
- [ ] Deliver a sourced comment draft only.

## Final scope correction (2026-10-12)

The latest user instruction supersedes failure-notification parts above. User lookup remains HTTP with structured error logs and exit 1. No failure queue URL, notification publisher, failure contract, DLQ resources or failure-queue alarms are required. SQS carries only order.cancel/order.place.
