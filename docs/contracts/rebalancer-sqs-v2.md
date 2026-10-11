# 리밸런서 SQS 계약 v1

#243의 AI 구현 기준입니다. 백엔드가 동일한 계약을 발행·소비하는지 실제 dev 환경에서 확인하기 전에는 연동 완료로 간주하지 않습니다. [공유 예제](rebalancer-sqs-v1.json)는 AI 모델과 함께 테스트합니다. Report 큐 및 리포트 HTTP API는 이 계약의 대상이 아닙니다.

## 공통 envelope

| 필드 | 규칙 |
|---|---|
| `schemaVersion` | 정수 `1`; 기존 발행자 호환을 위해 생략하면 v1. 다른 명시적 버전은 반려 |
| `eventId` | 비어 있지 않은 이벤트 고유 ID. 동일 이벤트 재시도는 ID와 본문을 유지 |
| `correlationId` | 원래 발행자가 지정하는 추적 ID. 스냅샷은 백엔드 실행 ID, 주문·실패 알림은 AI 실행 ID |
| `occurredAt` | 시간대가 있는 ISO 8601 시각. 스냅샷에서는 실제 상태를 읽은 시각이며 재발행 시 바꾸지 않음 |
| `type` | 아래 이벤트 종류 |
| `payload` | 이벤트 종류별 객체 |

새 스냅샷은 새로운 `eventId`와 이전보다 큰 `occurredAt`을 사용합니다. 동일 시각의 다른 이벤트도 과거 상태로 취급하므로 발행자는 UTC 마이크로초 정밀도의 단조 증가 시각을 보장해야 합니다. v1은 별도 stateVersion 없이 이 시각을 상태 버전으로 사용합니다. 서버 시각 동기화가 필요합니다.

## 큐와 메시지

| 종류 | 방향 / 큐 | FIFO 그룹 | payload |
|---|---|---|---|
| `account.snapshot` | Backend → AI / AccountQueueUrl | `accounts` 하나로 고정 | 기존 전체 `{"users": [...]}`; 부분 상태는 지원하지 않음 |
| `order.cancel` | AI → Backend / ORDER_QUEUE_URL | 계좌 ID 문자열 | `user_id`, `account_id`, `order_id`, `status: cancelled` |
| `order.place` | AI → Backend / ORDER_QUEUE_URL | 계좌 ID 문자열 | 기존 주문 필드에 `user_id`, `account_id`, `replaces_order_ids` 추가 |
| `account.snapshot.rejected` | AI → Backend / FailureQueueUrl | `ai-server` | 반려 원인 및 원본 메시지 추적 정보 |
| `account.snapshot.failed` | AI → Backend / FailureQueueUrl | `ai-server` | 조회·저장·복구·삭제·발행 전 상태 확인 실패 및 원본 추적 정보 |

모든 FIFO 발행에서 `MessageDeduplicationId = eventId`입니다. `users: []`는 사용자/계좌가 없는 정상 상태입니다. 실패 payload는 `reason`, `detail`, `rejected_event_id`, `source_sqs_message_id`, `source_correlation_id`입니다. 원본이 없으면 ID는 null이며, 원본 correlation이 없으면 로그·실패 알림에서 AI 실행 ID로 대체합니다. 원본 계좌 입력은 실패 알림과 로그에 담지 않습니다.

## 처리와 복구

백엔드는 09~15시 각 실행 전과 주문 접수·체결·취소 후에 전체 스냅샷을 발행합니다. AI는 20초 long polling하고, 검증된 상태와 이벤트 ID를 PostgreSQL에 커밋한 뒤 SQS 메시지를 삭제합니다. 중복·역순 메시지는 상태를 바꾸지 않고 삭제합니다. 유효 스냅샷은 기본 900초까지 복구할 수 있으며 `PORTFOLIO_REBALANCER_SNAPSHOT_MAX_AGE_SECONDS`로 조정합니다. 30초를 초과하는 미래 시각은 반려합니다.

첫 취소/주문 발행 전에 DB 상태를 원자적으로 claim하여 재시작 복구를 차단합니다. 발행 시작 후의 장애는 다음 최신 스냅샷으로 재계산합니다. 발행 시작 전 장애만 저장된 최신 스냅샷으로 재시도할 수 있습니다. 스냅샷이 만료·소진되었거나 DB 접근에 실패하면 주문을 발행하지 않고 실패 큐로 알린 뒤 종료합니다. 기존 단일 호스트 `flock` 스케줄러를 유지하며 여러 리밸런서를 동시에 실행하는 분산 스케줄링은 지원하지 않습니다.

취소 발행이 성공해도 대기 주문을 로컬에서 지우지 않습니다. 이후 새 스냅샷에서 대기 주문이 사라졌음을 확인하고 최신 잔고·보유량, 남은 실행 횟수로 좁힌 가격 범위를 다시 계산해 주문합니다. 확인하지 못한 계좌에는 겹치는 주문을 발행하지 않습니다.

잘못된 envelope/payload/시각, 지원하지 않는 이벤트는 반려를 알리고 원본을 삭제하지 않습니다. FIFO 배치의 후속 처리도 중단합니다. 반복 실패 메시지의 격리·재처리는 SQS redrive와 운영 절차로 처리하며 애플리케이션은 DLQ에 직접 발행하거나 DLQ를 일반 소비하지 않습니다.

## dev 연동 확인 항목

- [ ] Backend 발행 계약, 단조 증가 snapshot 시각, 전체 상태 및 빈 users 호환 확인
- [ ] Backend에서 취소 후 갱신 상태 발행, AI 재주문 및 추적 ID 연결 확인
- [ ] 동일 eventId 재전달과 AI 재시작에서 중복 주문 없음 확인
- [ ] 입력 실패 → 실패 알림 수신 → DLQ 격리 → 최신 정상 스냅샷 재발행 확인

AI 단위/모의 통합 테스트는 계약·저장·순서·재계산을 검증하며 실제 AWS FIFO redrive나 Backend 주문 실행을 대신하지 않습니다.
