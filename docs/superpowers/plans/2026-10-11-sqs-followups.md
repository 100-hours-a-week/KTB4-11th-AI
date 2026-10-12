# SQS Follow-ups Execution Record

2026-10-12 사용자 지시로 조회·취소를 기존 HTTP로 유지하고 주문 생성 요청만 SQS로 전환합니다.
이 결정은 앞선 계정 스냅샷 큐 복구 계획을 대체합니다.

- 제거: 계정 큐 소비·중복 상태 DB·최신성 워터마크·0011 스냅샷 마이그레이션·Account 큐/DLQ/알람.
- 유지: 기존 HTTP 조회/취소, 취소 후 HTTP 재조회·범위 축소 재주문, FIFO 주문·실패 알림.
- 후속 보완: 공유 계약·예제, 일반 Failure FIFO 큐/DLQ/알람 배포 템플릿·실제 연동 절차.
- 최신 dev의 0010 reason_id 마이그레이션은 유지합니다. 이 PR의 추가 DB 마이그레이션은 없습니다.
- #245 Draft는 유지합니다. 실제 Backend/AWS 배포는 수행하지 않습니다.

현재 계획: [HTTP 조회 복원](2026-10-12-http-user-polling.md).
