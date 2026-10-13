# Rebalancer SQS Order Messaging Design

## 현재 범위 (2026-10-13)

사용자 지시에 따라 사용자·계좌 조회는 기존 HTTP를 유지합니다.
취소·신규/재주문 요청은 같은 Order FIFO 큐와 계좌 그룹으로 발행하고, HTTP 조회 실패는 기존 구조화 로그와 실패 종료로 처리합니다.
리포트 서비스의 Report Queue 구현은 이 변경의 대상이 아닙니다.

## 흐름

1. `Backend.users()`로 `GET /api/v1/users/ai-server`를 호출합니다. dev와 동일한 인증·응답 모델을 사용합니다.
2. 활성 계좌의 대기 주문은 Orders.cancel로 order.cancel을 같은 Order FIFO 큐에 발행합니다.
3. 처음 조회한 잔고·보유량과 남은 실행 횟수로 범위·수량을 계산합니다. 취소 요청 다음에 `order.place`를 같은 계좌 FIFO 그룹으로 바로 발행합니다. 취소 완료를 기다리는 HTTP 재조회는 하지 않습니다.
4. 취소 요청 발행이 실패한 계좌는 신규 주문에서 제외합니다. 포트폴리오의 exit 종목과 과거 편출 잔여 종목은 매도만 요청합니다.
5. 초기 HTTP 조회 또는 응답 검증 실패는 users_fetch_failed로 로그를 기록하고 종료 코드 1로 끝냅니다. 실패 알림 큐에는 발행하지 않습니다.

## 계약·인증·로깅

발행 envelope는 eventId(UUID), correlationId(AI run ID), occurredAt(UTC), type, payload입니다.
주문 payload에는 기존 주문 필드와 reason_id, user_id, account_id, replaces_order_ids가 포함됩니다.
취소 payload는 user_id, account_id, order_id, status: cancelled입니다.
취소와 생성의 MessageGroupId는 같은 계좌 ID 문자열, MessageDeduplicationId는 각 요청 eventId입니다.
계좌 간 전역 순서는 보장하지 않습니다. 동일 계좌의 요청 도착 순서를 유지하며 Backend 소비자는 같은 그룹/배치 안의 명령도 순서대로 처리해야 합니다.
SQS 발행 성공은 취소 완료가 아닙니다. AI는 취소 요청 후 바로 재주문 요청을 발행하고, Backend 소비자는 같은 그룹에서 취소 처리를 마친 뒤 다음 주문을 처리해야 합니다.
대기 주문 잔존을 이유로 재주문을 다음 실행까지 보류하지 않습니다. 별도의 결과 큐나 폴링 루프는 추가하지 않습니다.
같은 SDK 전송 재시도는 같은 본문·ID를 사용합니다. FIFO 전송 중복 제거는 5분 범위이며 실행 전체에 대한 영구 exactly-once 보장은 아닙니다. 매 HTTP 폴링 실행은 최신 상태에서 새 요청을 계산하고 새 eventId를 생성합니다.

빈 users는 정상 응답으로 처리합니다.

ktb_core.backend_auth의 기존 HTTP 사용자 조회 JWT 인증을 유지합니다. boto3는 서비스에서 만들고 ktb_core.queue에 주입합니다.
SQS 인증은 SDK 기본 자격 증명 체인/EC2 IAM Role, 리전은 Compose의 AWS_DEFAULT_REGION 기본 ap-northeast-2입니다.
ORDER_QUEUE_URL과 Backend 설정은 배포 .env에서 다른 서비스와 같은 방식으로 전달합니다.
Access Key를 직접 저장하지 않습니다. 기존 ktb_core.logging으로 sqs_message_id, event_id, correlation_id, result를 기록합니다.

리밸런서는 SQS 소비자가 아니므로 long polling, DeleteMessage, visibility 연장은 사용하지 않습니다.
계정 스냅샷 큐·스냅샷 복구 테이블·관련 마이그레이션은 제거합니다.
애플리케이션은 DLQ에 직접 발행하거나 일반 소비자로 붙지 않습니다. 별도 실패 큐·DLQ·알람 템플릿은 추가하지 않습니다.
실제 Backend 호환/AWS 권한·배포 검증은 별도로 수행합니다.

취소 로그/집계는 order_cancel_requested와 cancel_requested로 발행 사실을 표현하고 MessageId/eventId/correlationId/result를 기록합니다.
FIFO는 요청 순서 보장이고 외부 주문 취소 완료·체결·트랜잭션 원자성을 대신하지 않습니다.
이번 매도의 예상 대금을 매수 예산에 더하지 않습니다. 매수는 조회된 cash_balance만 쓰고 매도 대금은 이후 조회에서 반영돼야 사용할 수 있습니다.
