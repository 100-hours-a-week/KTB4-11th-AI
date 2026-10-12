# Rebalancer SQS Order Messaging Design

## 현재 범위 (2026-10-12)

사용자 지시에 따라 사용자·계좌 조회와 주문 취소는 기존 HTTP 구현을 유지합니다.
주문 생성 요청만 Order FIFO 큐로 전환하고, HTTP 조회 실패는 별도의 일반 Failure FIFO 큐로 알립니다.
리포트 서비스의 Report Queue 구현은 이 변경의 대상이 아닙니다.

## 흐름

1. `Backend.users()`로 `GET /api/v1/users/ai-server`를 호출합니다. dev와 동일한 인증·응답 모델을 사용합니다.
2. 활성 계좌의 대기 주문을 기존 `Backend.cancel()` / `_cancel_all()`로 HTTP 취소합니다.
3. 취소 성공이 있으면 HTTP로 사용자 정보를 다시 조회합니다. 최신 잔고·보유량으로 범위·수량을 재계산합니다.
4. 취소 실패 또는 대기 주문이 남은 계좌를 제외하고 `order.place`를 Order FIFO 큐로 발행합니다.
5. 초기/갱신 HTTP 조회 또는 응답 검증 실패 시 `account.snapshot.failed`를 Failure FIFO 큐로 보내고 종료 코드 1로 끝냅니다. 실패 알림 발행 자체가 실패해도 주문은 실행하지 않고 오류를 기록합니다.

## 계약·인증·로깅

발행 envelope는 eventId(UUID), correlationId(AI run ID), occurredAt(UTC), type, payload입니다.
주문 payload에는 기존 주문 필드와 reason_id, user_id, account_id, replaces_order_ids가 포함됩니다.
MessageGroupId는 계좌 ID 문자열, MessageDeduplicationId는 eventId입니다. 계좌 간 전역 순서는 보장하지 않습니다.
같은 SDK 전송 재시도는 같은 본문·ID를 사용합니다. FIFO 전송 중복 제거는 5분 범위이며 실행 전체에 대한 영구 exactly-once 보장은 아닙니다. 매 HTTP 폴링 실행은 최신 상태에서 새 요청을 계산하고 새 eventId를 생성합니다.

Failure 그룹은 ai-server이며 reason(users_fetch_failed/users_payload_invalid), detail(예외 유형과 HTTP 상태), source_correlation_id를 포함합니다.
원본 SQS 수신 이벤트가 없으므로 rejected_event_id/source_sqs_message_id는 null입니다. 사용자 응답 본문·인증 정보를 실패 알림에 싣지 않습니다.
빈 users는 정상 응답으로 처리합니다.

ktb_core.backend_auth의 기존 HTTP JWT·CSRF 인증을 유지합니다. boto3는 서비스에서 만들고 ktb_core.queue에 주입합니다.
SQS 인증은 SDK 기본 자격 증명 체인/EC2 IAM Role, 리전은 Compose의 AWS_DEFAULT_REGION 기본 ap-northeast-2입니다.
ORDER_QUEUE_URL, PORTFOLIO_REBALANCER_FAILURE_QUEUE_URL과 Backend 설정은 배포 .env에서 다른 서비스와 같은 방식으로 전달합니다.
Access Key를 직접 저장하지 않습니다. 기존 ktb_core.logging으로 sqs_message_id, event_id, correlation_id, result를 기록합니다.

리밸런서는 SQS 소비자가 아니므로 long polling, DeleteMessage, visibility 연장은 사용하지 않습니다.
계정 스냅샷 큐·스냅샷 복구 테이블·관련 마이그레이션은 제거합니다.
애플리케이션은 DLQ에 직접 발행하거나 일반 소비자로 붙지 않습니다. Failure 큐의 DLQ 격리는 인프라 redrive 정책으로 처리합니다.
실제 Backend 호환/AWS 권한·배포 검증은 별도로 수행합니다.
