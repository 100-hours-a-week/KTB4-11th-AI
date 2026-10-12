# StockSpoon V2 리밸런서 통신 계약

StockSpoon V2 문서이며 메시지 schemaVersion은 1입니다. 발행 envelope에서 생략한 경우도 1로 해석합니다.

| 작업 | 경로 | FIFO 그룹 |
|---|---|---|
| 사용자·계좌 조회 | 기존 GET /api/v1/users/ai-server | 해당 없음 |
| 주문 취소 | 기존 PATCH /api/v1/accounts/{account_id}/orders/{order_id} | 해당 없음 |
| order.place | AI → ORDER_QUEUE_URL | 계좌 ID 문자열 |
| account.snapshot.failed | AI → PORTFOLIO_REBALANCER_FAILURE_QUEUE_URL | ai-server |

FIFO MessageDeduplicationId는 eventId입니다. eventId는 이벤트 UUID, correlationId는 AI 실행 ID,
occurredAt은 UTC 시각입니다. 같은 SDK 재시도는 본문·ID를 유지하며 다른 실행은 새 이벤트입니다.
SQS 전송 중복 제거는 5분 범위입니다.

사용자 정보는 매 실행 HTTP로 조회하고, 취소 성공 후 같은 API를 다시 조회해 최신 상태로 주문을 계산합니다.
조회·갱신 실패 시 주문을 보내지 않고 account.snapshot.failed로 알립니다. 실패 payload는 reason,
detail(예외 유형·HTTP 상태), source_correlation_id입니다. rejected_event_id와 source_sqs_message_id는
HTTP 조회에 대응하는 원본 SQS 이벤트가 없으므로 null입니다. 응답 본문·인증 정보를 포함하지 않습니다.
users: []는 정상입니다. 계정 스냅샷 이벤트의 발행·소비·복구 DB 마이그레이션은 필요하지 않습니다.

주문 payload에는 기존 주문 필드와 reason_id, user_id, account_id, replaces_order_ids가 포함됩니다.
공유 예제: [rebalancer-sqs-v2.json](rebalancer-sqs-v2.json).

HTTP는 기존 JWT/CSRF 인증, SQS는 SDK 기본 인증 체인/EC2 IAM Role을 사용합니다.
DLQ에 직접 발행하거나 일반 소비자를 연결하지 않습니다. 실제 Backend 호환과 AWS 권한·배포는 별도 검증합니다.

## 연동 확인
- [ ] 기존 GET 응답 모델과 빈 users 처리 확인
- [ ] HTTP 취소 후 GET으로 최신 잔고·보유량·대기 주문 확인
- [ ] Order/Failure envelope·FIFO ID·reason_id 확인
- [ ] 동일 eventId 소비 중복 방지와 성공 후 삭제는 Backend 소비자에서 확인
- [ ] HTTP 실패 알림·Failure DLQ redrive·알람 확인
