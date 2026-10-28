# Market HTTP Redis recovery OOM observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** #420의 Redis 통신 장애 해제 직후 OOM을 분석할 수 있도록 market-http의 애플리케이션 큐, 처리율, 연결 전환, 선택적 메모리 프로파일을 관측한다.

**Architecture:** 기존 프로세스별 Prometheus 레지스트리와 SSE ASGI 전송 완료 계측을 확장한다. 애플리케이션 소유 `asyncio.Queue`에 경량 회계 hook을 붙이고, redis-py의 기존 실행 경로를 그대로 위임하는 관측 adapter로 연결 전환을 기록한다. 프로파일러는 기본 비활성인 실험 기능이며 복구 정책을 소유하지 않는다.

**Tech Stack:** Python 3.13 (지원 범위 3.13–3.14), asyncio, FastAPI SSE, redis-py 8.1.0 (`uv.lock`), prometheus-client, 표준 라이브러리 tracemalloc, pytest.

**Spec:** https://github.com/100-hours-a-week/KTB4-11th-AI/issues/420 및 아래 `계획용 설계와 근거`. 별도 설계 파일은 만들지 않는다. 이번 위임 산출물은 이 계획 하나이며 구현 승인이나 팀 합의를 대신하지 않는다.

## Global Constraints

- 이번 요청은 OOM 원인을 찾기 위한 계측 추가다. 메모리 증설이나 애플리케이션 동작 수정은 분석 결과에 따라 별도 이슈로 진행한다.
- `QUEUE_CAPACITY = 256`, FIFO, 이벤트별 전달, 기존 overflow 시 큐 제거와 `None` 통지, snapshot 이전 구독 등록을 보존한다. coalescing, 신규 재시도, 리스너 자동 재시작, timeout, 풀 종류와 상한 변경은 금지한다.
- 연결별 ID를 지표 라벨로 사용해 시계열이 과도하게 증가하지 않도록 한다. 종목, Task ID, listener ID, trace ID, 오류 문자열도 metric label로 추가하지 않는다.
- 관측 코드는 `market_http/observability/`에 둔다. 기존 코드에는 사건별 hook만 추가하며 OpenTelemetry나 Prometheus 구현을 유출하지 않는다.
- 기존 metric 이름과 의미, EMF, tracing, 공개 REST/SSE payload, Valkey 쓰기 경계는 변경하지 않는다.
- metrics는 기존 내부 listener `127.0.0.1:9100/metrics`를 사용한다. 공개 API나 ALB에 노출하지 않는다.
- 애플리케이션 수신 시각은 네트워크 장애 시작 시각이 아니다. Pub/Sub가 단절 중 메시지를 저장했다가 재전송한다고 가정하지 않는다.
- 테스트 데이터스토어는 폐기 가능한 전용 인스턴스만 사용한다. Valkey fixture의 `FLUSHDB`를 운영 리소스에 실행하지 않는다.
- 이번 계획 작성에서는 기존 코드, GitHub 이슈, 배포 설정을 변경하지 않고 커밋하거나 push하지 않는다.

## Review Focus

1. 한 큐가 여러 code에 등록되거나 중복 code를 받는 경우에도 한 번만 집계한다. 혼합 stream도 누락하지 않는다. Task 1.
2. overflow, cancellation, 리스너 실패, shutdown의 중첩 cleanup 후에도 현재량은 음수가 되지 않고 과거 큐가 남지 않는다. Task 1.
3. SDK가 내부에서 복구하거나 일부 ACK만 받은 경우 연결 완료와 구독 완료를 혼동하지 않는다. retry 횟수와 예외 전파가 관측 전후 동일하다. Task 3.
4. 초기 snapshot, keepalive, split frame, blocked/failed ASGI send는 수신율과 live 전달율을 오염시키지 않는다. Task 2.
5. 프로파일 디렉터리 쓰기 실패, 용량 한도, 외부 tracing 세션, shutdown 중 dump가 서비스나 메모리 사용을 무한히 늘리지 않는다. Task 4.

---

## 계획용 설계와 근거

### 확인한 기준점

- 독립적으로 `git fetch origin dev`와 `git pull --ff-only origin dev` 완료. branch `dev`, HEAD `b5d43b24f97272ca471c45e80711440d630603ad`, 작성 전 `git status --short` 출력 없음.
- `AGENTS.md`, #420 본문과 댓글(없음), market-http 소스, 설정, 의존성, 관련 테스트, `.env.example`, 두 Compose 파일, 기존 Prometheus 설계를 확인했다.
- `MarketSubscription.run()`은 프로세스당 Pub/Sub 하나를 소유하고 `market:prices`, `market:orderbooks` 양쪽 최초 ACK 후 ready를 기록한다. `_listen()`은 동일한 `MarketEvent`를 각 클라이언트 큐에 참조로 넣는다.
- `_close_subscriber()`는 큐가 가득 찬 경우 기존 항목을 비우고 `None`을 넣는다. listener 실패 후 재시작하지 않는 현재 동작을 테스트가 고정한다.
- 현재 `_listen()`의 resubscribing 로그는 첫 재구독 ACK 수신 시점이다. 실제 재연결 시작이나 SUBSCRIBE 명령 시작 시각이 아니므로 새 로그에서 구별해야 한다.
- 기존 `SSEMetrics`는 성공한 ASGI send 후 complete data frame을 세고 snapshot도 포함한다. `DELIVERY` deque의 live timestamp로 live/snapshot을 구별할 수 있다. ASGI send 완료는 브라우저 수신 확인이 아니다.
- 기본 registry는 `CollectorRegistry()`이므로 기본 전역 registry의 process collector가 자동 노출된다고 가정하지 않는다.
- `uv.lock`과 로컬 import에서 redis-py 8.1.0을 확인했다. `PubSub._execute()`가 retry를 소유하고 `_reconnect()`가 disconnect/connect를 실행한다. `PubSub.on_connect()`는 재구독 명령을 제출하고, 그 뒤 수신되는 두 channel ACK가 구독 복구 완료 근거다.
- 기준 테스트 실행: `uv run pytest services/market-http/tests/test_market_subscription.py services/market-http/tests/test_market_streams.py services/market-http/tests/test_prometheus.py -q`가 성공했다. 실제 Valkey가 필요한 SSE 테스트는 skip되었다. FIS나 OOM 재현을 실행한 것은 아니다.

### Approaches

1. 선택한 계획안: 소유 큐의 증분 회계 + 기존 Prometheus 확장 + SDK 경계 위임 계측 + 선택적 tracemalloc. 큐 고수위가 scrape 사이에도 남으며 데이터 경로 변경 없이 단계별 자료를 얻는다. SDK adapter의 버전 결합과 계측 비용은 테스트해야 한다.
2. 로그와 외부 RSS만 추가: 변경량은 적지만 2~3초 급증의 큐 깊이, 드롭, 수신/전송 비율을 구분하지 못하므로 #420 완료 조건에 부족하다.
3. 전용 재연결 루프나 최신 이벤트 coalescing: 원인 분석 전 동작을 바꾸므로 제외한다.

1번은 계획 작성자의 제안이다. 별도 설계 승인이나 팀 합의가 확인된 것으로 기록하지 않는다. 구현은 이 계획 검토 후 별도 위임에서 시작한다.

### Metric contract

라벨 `stream`은 `prices`, `orderbooks`, `mixed`로 제한한다. 실제 HTTP stream은 앞의 두 값만 사용한다. `mixed`는 현 `subscribe()` API가 허용하는 혼합/빈 키 집합을 위한 큐 집계 값이다. `channel`은 `market:prices`, `market:orderbooks`만 허용한다. `reason`은 아래 명시 값으로 제한한다. idle 시에도 0 시계열을 초기화한다.

| 이름 | 종류 / 단위 | 라벨 | 의미 |
| --- | --- | --- | --- |
| `market_http_sse_queues` | Gauge / queues | stream | fan-out에 등록된 고유 큐 수 |
| `market_http_sse_queue_events` | Gauge / events | stream | 등록 큐의 현재 live data 항목 합계; sentinel 제외 |
| `market_http_sse_queue_max_events` | Gauge / events | stream | 현재 등록 큐 중 가장 큰 깊이; 큐 없으면 0 |
| `market_http_sse_queue_high_watermark_events` | Gauge / events | stream | 프로세스 생존 중 관측한 단일 큐 최대 깊이; 종료된 큐의 peak도 보존 |
| `market_http_sse_queue_payload_bytes` | Gauge / estimated bytes | stream | 큐 항목별 `len(data.encode('utf-8'))`의 논리적 합계 |
| `market_http_sse_queue_full_total` | Counter / incidents | stream | 기존 `queue.full()` 분기를 타서 구독을 종료한 횟수 |
| `market_http_sse_queue_discarded_events_total` | Counter / events | stream, reason | `overflow`, `listener_end`, `shutdown`, `subscriber_exit` 때 버려지는 대기 항목; overflow에서는 넣지 못한 신규 항목도 포함 |
| `market_http_sse_slow_subscribers_closed_total` | Counter / subscribers | stream | overflow로 제거한 큐 수; 실제 TCP 종료 완료 수가 아님 |
| `market_http_pubsub_messages_received_total` | Counter / messages | channel | 알려진 채널의 `type=message` 수, JSON validation 이전; unmatched/invalid도 포함 |
| `market_http_pubsub_payload_received_bytes_total` | Counter / bytes | channel | 위 payload의 재인코딩 UTF-8 길이; Redis wire bytes 아님 |
| `market_http_sse_live_events_sent_total` | Counter / deliveries | stream | Pub/Sub 유래 complete data frame이 ASGI send를 성공한 횟수 |
| `market_http_sse_send_operations_in_flight` | Gauge / operations | stream | 현재 ASGI body send await 중인 호출 수; heartbeat도 포함 |
| `market_http_sse_send_body_bytes_in_flight` | Gauge / bytes | stream | 위 호출이 소유한 body 길이; 커널/TLS/transport 버퍼가 아님 |
| `market_http_sse_observer_buffer_bytes` | Gauge / bytes | stream | 기존 SSEMetrics framing buffer 길이의 연결별 합계 |
| `market_http_sse_delivery_markers_pending` | Gauge / markers | stream | 기존 DELIVERY deque 길이의 연결별 합계; 실제 worker task 수가 아님 |
| `process_resident_memory_bytes` 등 | 기존 Prometheus ProcessCollector | 없음 | 이 registry에 명시 등록; Linux에서 현재 RSS 등 지원 필드 |

기존 `market_http_sse_events_sent_total`은 snapshot을 포함한 의미를 유지한다. 처리 대기 작업은 실제 소유한 큐 항목과 delivery marker로 보고하며, 가상의 worker backlog를 만들지 않는다. 기존 `market_http_valkey_pool_connections{state="acquiring"}`는 풀 획득 호출 중인 수이지 풀 대기 큐 길이가 아니다.

바이트 추정은 참조별 논리적 payload 크기다. 동일 이벤트가 N개 큐에 공유되면 N번 계산하므로 실제 unique heap allocation과 다르다. Python str, dataclass, deque, allocator, ASGI 직렬화, Redis parser, TCP/TLS 메모리를 포함하지 않는다. payload 길이는 수신 이벤트마다 한 번 계산해서 공유하고 큐 등록 시 payload 사본이나 이벤트별 보조 dict를 만들지 않는다. framing buffer는 관측기 자신이 이미 소유한 버퍼만 계측하며 추가 복사를 만들지 않는다.

### Transition log contract

기존 event 이름을 가능한 유지한다. `fields`에는 UTC 시각(기존 JSON logger 시각), `pid`, `listener_id`, `connection_generation`, 제한된 `phase`, `reason`, `error_type`, 두 channel 이름, 큐 events/bytes/max 집계와 nullable `rss_bytes`를 넣는다. 오류 원문과 Redis URL, credential, 원본 payload는 기록하지 않는다.

- `market_subscription_starting` / `market_subscription_ready`: 최초 시작과 두 ACK 완료. 연결 성공만으로 ready를 기록하지 않는다.
- `market_redis_disconnected`: 실제 SDK 오류 경계에서 감지. `reason=read_error|connect_error`; FIS 시작 시각으로 해석하지 않는다.
- `market_redis_connecting` / `market_redis_connected` / `market_redis_connect_failed`: 실제 연결 시도 경계, 최초/재연결을 `phase`로 구분. 이미 연결된 no-op connect는 새 시도로 세지 않는다.
- `market_subscription_resubscribing`: SDK on_connect 재구독 callback을 호출하기 직전. 이전의 첫 ACK 기준 로그를 이 경계로 옮겨 중복하지 않는다.
- `market_subscription_resubscribe_failed`: callback의 실제 실패. 원래 예외를 그대로 전파한다.
- `market_subscription_recovered`: 현재 generation의 알려진 두 ACK를 모두 본 시각. duplicate ACK와 unknown ACK는 완료를 앞당기지 않는다. SDK 관측기가 없는 test fake의 ACK 기반 경로도 기존 테스트와 호환한다.
- 기존 startup failure, listener failure, stopped 로그에 원인과 관측 snapshot을 보완한다. cancellation과 의도적 shutdown을 Redis 장애로 기록하지 않는다.
- `market_sse_queue_full`, `market_sse_subscription_closed`: 기존 구독 제거 시 한 번씩 기록한다. `queue_events`, `queue_payload_bytes`, `discarded_events`, 제한된 종료 사유를 포함한다. 연결 식별자는 필요 시 로그에만 사용하고 payload를 기록하지 않는다.

Task 상관관계는 우선 Cloud의 로그 stream/target label에 있는 ECS Task 식별자를 사용한다. 앱은 `MARKET_HTTP_TASK_ID`가 주입되면 로그 필드로만 추가하며, 없으면 null과 pid/listener_id로 동작한다. 앱에서 Task metadata endpoint를 요청하거나 Task ID를 metric label로 만들지 않는다. Cloud 수집기와 배포 시 주입 여부는 미검증이다.

## 파일 구조

아래는 향후 구현 파일 지도다. 계획 작성 자체는 이 파일 외에는 변경하지 않는다.

- Create `services/market-http/src/market_http/observability/queues.py`: `ObservedQueue`, 고유 큐 집계와 bounded 회계, snapshot.
- Modify `services/market-http/src/market_http/observability/prometheus.py`: metric 등록, live send와 기존 observer 소유 버퍼 계측, process collector.
- Create `services/market-http/src/market_http/observability/redis.py`: PubSub/전용 connection 인스턴스 한정 관측 adapter와 전환 상태.
- Create `services/market-http/src/market_http/observability/memory.py`: Linux 현재 RSS, 실험 전용 profiler.
- Modify `services/market-http/src/market_http/market_subscription.py`: queue factory와 lifecycle hook; FIFO/fan-out/close 행동은 보존.
- Modify `services/market-http/src/market_http/app.py`, `__main__.py`, `settings.py`: 동일 registry 전달과 profiler lifecycle/config.
- Modify `.env.example`, `compose.local.yaml`, `compose.prod.yaml`: 신규 옵션 설명과 market-http 한정 명시적 pass-through. 배포 정책/리소스는 변경하지 않는다.
- Create `services/market-http/tests/test_queue_observability.py`, `test_redis_observability.py`, `test_memory_observability.py`.
- Modify `services/market-http/tests/test_market_subscription.py`, `test_market_streams.py`, `test_prometheus.py`, `test_settings.py`, `test_main.py`: hook 연결과 기존 행동 회귀.
- Create `docs/operations/market-http-redis-recovery-observability.md`; Modify `README.md`: 활성화, 지표 계약, 실험 runbook 링크.
- 의존성 추가나 새 shared package는 계획하지 않는다. `uv.lock`, requirements와 이미지 dependency 파일은 불필요하게 변경하지 않는다.

## Task 1: 소유 큐의 bounded 회계와 scrape 지표

**Files:** `observability/queues.py` (create), `observability/prometheus.py`, `market_subscription.py`, `app.py` (modify); `test_queue_observability.py` (create), `test_market_subscription.py` (modify). 경로는 위 파일 지도 기준이다.

**Interfaces:**
- Consumes: `MarketEvent.data: str`, `QUEUE_CAPACITY = 256`, 기존 `MarketMetrics.registry`.
- Produces: `ObservedQueue(asyncio.Queue[MarketEvent | None])` (형식 참조는 TYPE_CHECKING으로 순환 import 방지), `QueueMetrics(registry: CollectorRegistry)`, `QueueMetrics.open(stream: str) -> ObservedQueue`, `QueueMetrics.close(queue: ObservedQueue, reason: str, *, rejected_events: int = 0) -> None`, `QueueMetrics.snapshot() -> dict[str, int]`.
- `MarketMetrics.queues: QueueMetrics`; `MarketSubscription(valkey: Redis, metrics: MarketMetrics | None = None)`를 추가하고 `create_app`이 기존 `prometheus`를 전달한다. metrics가 없을 때 현재 plain queue와 동작을 보존한다.

- [ ] Step 1: 새 테스트에 다음 assertion을 고정한다. 비동기 시나리오는 기존 `asyncio.run` 패턴과 fake를 사용한다.

```python
assert queue.maxsize == 256
assert queue.qsize() == 2
assert metrics.registry.get_sample_value('market_http_sse_queue_events', {'stream': 'prices'}) == 2
assert metrics.registry.get_sample_value('market_http_sse_queue_max_events', {'stream': 'prices'}) == 2
assert metrics.registry.get_sample_value('market_http_sse_queue_payload_bytes', {'stream': 'prices'}) == sum(len(e.data.encode('utf-8')) for e in events)
assert [queue.get_nowait() for _ in events] == events
```

추가 테스트 이름과 assertion: `test_duplicate_keys_and_shared_event_count_unique_queues`에서 duplicate key 한 큐의 queue 수는 1, 동일 event를 받는 두 큐의 논리 bytes는 2배, mixed 큐는 mixed에만 집계한다. `test_overflow_accounts_rejected_and_drained_events_once`에서 257번째 입력으로 full=1, slow closed=1, discarded{overflow}=257, 현재 events/bytes/queues=0, high watermark=256, 소비 값은 `None`이다. `test_cancel_shutdown_and_old_cleanup_do_not_double_release`에서 disconnect에 남은 항목은 subscriber_exit로 집계하고 중첩 close 후 모든 현재량=0, 카운터 중복 없음, 새 lifespan 큐가 보존된다. `test_utf8_sentinel_and_empty_queue_accounting`에서 비ASCII UTF-8 bytes를 검증하고 sentinel은 events/bytes에서 제외한다. `test_concurrent_scrape_sees_consistent_snapshot`에서 listener thread scrape 중 dict/set 변경 예외와 음수/합계 불일치가 없다.

- [ ] Step 2: `uv run pytest services/market-http/tests/test_queue_observability.py -v` 실행. 새 API import 실패 또는 신규 metric 미노출로 FAIL을 확인한다.
- [ ] Step 3: `QueueMetrics`와 `ObservedQueue`를 구현한다. `put_nowait` 성공 후 증가, `get_nowait` 성공 후 감소하므로 기존 await `get()`과 직접 `get_nowait()` 양쪽을 포착한다. max/peak는 sentinel 제외, peak는 process lifetime이다. 깊이별 고정 257칸 빈도표와 증분 합계를 사용해 매 fan-out마다 전체 큐를 순회하지 않는다. 상태 snapshot은 짧은 lock 아래 복사하고 exporter thread는 business mutable set을 직접 순회하지 않는다. listener가 이벤트당 한 번 UTF-8 길이를 계산해 `MarketEvent`의 default=0인 `payload_bytes: int` 필드로 전달한다; 직접 생성된 event는 queue에서 한 번 계산한 길이를 사용한다. maxsize와 이벤트 순서를 변경하지 않는다.
- [ ] Step 4: 구독 등록/해제/overflow/listener end/shutdown에 회계 hook을 붙인다. close 회계는 idempotent이며 종료된 큐를 회계 저장소에서 제거하고 이후 `get(None)`/old finally hook은 no-op이다. consumer exit에 잔여 data를 계수하되 큐를 비우거나 소비자 취소 동작을 추가하지 않는다. overflow rejected 1건과 기존 drain 항목은 분리해 합산한다. 기존 close method에 제한된 `reason` 인자만 추가하며 취소 전파와 기존 sentinel insertion을 유지한다.
- [ ] Step 5: `uv run pytest services/market-http/tests/test_queue_observability.py services/market-http/tests/test_market_subscription.py services/market-http/tests/test_market_streams.py -v` 실행. unit PASS, 외부 Valkey 미설정 skip는 구분 보고한다.
- [ ] Step 6: 위 Task 1 파일만 stage 후 `git commit -m "feat: observe market HTTP subscriber queues"`.

## Task 2: 수신/실제 ASGI 전달율과 앱 소유 전송 상태

**Files:** `market_subscription.py`, `observability/prometheus.py`, `test_prometheus.py`, `test_market_streams.py` (modify).

**Interfaces:**
- Consumes: `MarketMetrics`, 기존 `DELIVERY`, `track_delivery(*, live: bool = False, received_at: float | None = None) -> None`, 기존 `SSEMetrics` send wrapper.
- Produces: `MarketMetrics.received(channel: str, payload_bytes: int) -> None`; `DeliveryMarkers(deque[float | None])`는 `append`, `popleft`, `clear`에 증분 marker 회계를 붙이고 기존 `DELIVERY` deque 계약을 유지한다. 위 metric contract의 수신 counters, live sent counter, in-flight/body/framing/marker gauges를 제공하며 기존 counter 의미는 그대로다.

- [ ] Step 1: 테스트를 추가한다. `test_received_counts_before_validation_and_without_subscribers`에서 known channel invalid JSON 1건 + valid unmatched 1건은 received=2, bytes=UTF-8 합계, queue=0이다. subscribe ACK/unknown channel은 수신 카운터에 넣지 않는다. `test_live_rate_excludes_snapshot_ping_and_failed_send`에서 snapshot 1 + live 1 성공 후 total sent=2, live sent=1, ping과 failed live send는 증가 없음이다. `test_blocked_send_and_split_frame_release_all_gauges`에서 send를 Event로 막으면 operations=1, body bytes=body 길이, 아직 sent=0; release/cancel/error 후 모든 현재량=0이다. split frame은 완성된 data event만 세고 기존 latency assertion을 보존한다. marker는 send에 소비되거나 finally에서 남은 수만큼 해제된다. `test_cardinality_and_idle_endpoint`에서 generate_latest 및 실제 metrics listener HTTP 응답에 새 families가 노출되고 labels에 connection/code/task/listener 식별자가 없다.
- [ ] Step 2: `uv run pytest services/market-http/tests/test_prometheus.py services/market-http/tests/test_market_streams.py -v` 실행. 신규 assertion의 FAIL을 확인한다.
- [ ] Step 3: 수신 hook을 validation 전 known-channel message 분기에 넣는다. Task 1의 계산된 payload 길이를 재사용한다. complete frame 처리 시 기존 `pending.popleft()` 결과가 `None`이 아니면 live sent를 증가한다. send await 전후 및 finally에서 전송 상태를 증분 회계하되 body/frame 내용 사본이나 event별 추가 task를 만들지 않는다. registry에 표준 `ProcessCollector(registry=...)`를 한 번 등록한다. Linux 이외 플랫폼의 process metric 미지원은 정상이며 0 RSS를 조작해 노출하지 않는다.
- [ ] Step 4: 같은 테스트 명령 및 `uv run pytest services/market-http/tests/test_tracing.py services/market-http/tests/test_metrics.py -v` 실행. 기존 EMF와 span sampling 영향 없이 PASS 확인.
- [ ] Step 5: 해당 파일만 stage 후 `git commit -m "feat: expose market receive and SSE delivery pressure"`.

## Task 3: Redis 실제 연결과 재구독 전환 관측

**Files:** `observability/redis.py`, `observability/memory.py` (create; memory는 RSS 함수만 먼저), `market_subscription.py`, `settings.py`, `__main__.py`, `app.py` (modify); `test_redis_observability.py` (create), `test_market_subscription.py`, `test_settings.py` (modify).

**Interfaces:**
- Consumes: Task 1 `QueueMetrics.snapshot()`, 전용 `PubSub` 인스턴스, Task 2 process metric와 동일 의미의 현재 RSS.
- Produces: `SubscriptionObserver(*, listener_id: str, task_id: str | None, queues: QueueMetrics | None)`, `SubscriptionObserver.transition(event: str, *, reason: str, error: BaseException | None = None) -> None`, `observe_pubsub(pubsub: PubSub, observer: SubscriptionObserver) -> None`, `current_rss_bytes() -> int | None`.
- `Settings.task_id: str | None = None` (`MARKET_HTTP_TASK_ID`), `create_app(..., task_id: str | None = None)`, `MarketSubscription(..., task_id: str | None = None)`로 전달한다.

- [ ] Step 1: `test_redis_observability.py`에서 실제 locked redis-py 객체와 fake connection을 사용한다. `test_adapter_preserves_retry_calls_arguments_and_exception_identity`는 관측 on/off에 대해 connect/disconnect/read/subscribe 호출 순서, retry/backoff 설정, 최종 예외 객체, cancellation을 같게 assert한다. `test_connection_then_both_generation_acks`는 reconnect 시작 < connected < resubscribing < recovered, 첫 ACK/중복/unknown ACK만으로 recovered=0, 두 known ACK 후 recovered=1을 assert한다. `test_reconnect_and_resubscribe_failure_are_distinct`는 각 실패에서 error_type과 phase가 다르고 ready/recovered가 없음을 assert한다. `test_shutdown_and_noop_connect_do_not_emit_outage`는 의도적 close와 이미 연결된 connect에서 장애 로그=0이다. `test_transition_fields_safe_without_task_or_proc`는 `/proc` 부재 시 rss=null, task=null, 필수 listener/pid/queue fields 존재, credential/payload/error raw string 없음이다.
- [ ] Step 2: `uv run pytest services/market-http/tests/test_redis_observability.py -v` 실행. adapter 부재의 FAIL을 확인한다.
- [ ] Step 3: 인스턴스 한정 adapter를 구현한다. 최초 subscribe 전에 PubSub의 `connect`, `_reconnect`, `on_connect`를 원래 bound method로 위임하는 wrapper로 감싼다. `on_connect`는 SDK weak callback 등록과 호환되도록 `MethodType` bound method로 유지한다. 최초 `PubSub.connect` 성공 후 전용 connection의 `connect`를 한 번만 관측하며, disconnected 상태일 때만 연결 시도 로그를 낸다. `_reconnect` 입구에서 SDK가 전달한 예외형으로 단절 감지를 기록한다. `on_connect` 진입은 socket/auth 이후이므로 connected를 먼저 기록하고 이어서 resubscribing과 원본 callback 위임을 수행한다. connection.connect가 돌아왔다는 이유로 구독 ACK 완료를 기록하지 않는다. 최초 pool acquisition 실패와 런타임 direct-connect 실패도 phase별로 기록한다. wrapper는 retry 호출/설정/분기를 추가하지 않으며 SDK global class는 patch하지 않는다. 현재 버전의 메서드 seam은 테스트로 고정하고 업그레이드 시 재검증한다.
- [ ] Step 4: `_listen` ACK 처리에 observer generation을 연결하고 기존 fake 기반 ACK 테스트를 보존한다. 리스너 종료/failure/cleanup에는 snapshot만 추가한다. 기존 queue-full 분기와 구독 해제 경계에서 위 `market_sse_queue_full`과 `market_sse_subscription_closed`를 기록한다. `test_queue_close_logs_once_with_pre_drain_snapshot`을 추가해 overflow 전 깊이=256, discarded=257, 같은 큐 중첩 cleanup의 closed log=1, payload 미포함을 assert한다. `/proc/self/statm`의 resident pages와 `os.sysconf('SC_PAGE_SIZE')`로 현재 RSS를 읽고 파일 부재/파싱 오류는 None을 반환한다. `resource.ru_maxrss`를 현재 RSS로 대체하지 않는다. RSS 읽기는 전환 시점에만 수행하고 per-event fan-out에서는 수행하지 않는다.
- [ ] Step 5: `uv run pytest services/market-http/tests/test_redis_observability.py services/market-http/tests/test_market_subscription.py services/market-http/tests/test_settings.py -v` 실행. 특히 `test_listener_failure_notifies_clients_and_does_not_restart`와 기존 shutdown race 테스트가 PASS인지 확인한다.
- [ ] Step 6: 해당 파일만 stage 후 `git commit -m "feat: log market Redis connection transitions"`.

## Task 4: 기본 비활성 실험용 bounded 메모리 프로파일

**Files:** `observability/memory.py`, `settings.py`, `app.py`, `__main__.py`, `.env.example`, `compose.local.yaml`, `compose.prod.yaml` (modify); `test_memory_observability.py` (create), `test_settings.py`, `test_main.py` (modify).

**Interfaces:**
- Consumes: Task 3 `current_rss_bytes()`, 앱 lifespan, 기존 JSON logger.
- Produces: `memory_profiling(settings: Settings) -> AsyncContextManager[None]`; 외부에 객체 snapshot을 반환하지 않는다.
- 신규 설정: `memory_profile_enabled: bool = False`, `memory_profile_dir: str = '/tmp/market-http-memory'`, `memory_profile_interval_seconds: float = Field(default=10, ge=1)`, `memory_profile_max_snapshots: int = Field(default=12, ge=1, le=120)`. 모두 `MARKET_HTTP_` prefix. Task ID와 profiler 설정을 두 Compose의 market-http environment에 기본값과 함께 명시한다. mount나 외부 업로드 권한은 추가하지 않는다.

- [ ] Step 1: 설정 기본값/환경변수/범위 오류를 assert한다. `test_disabled_profiler_does_no_work`는 tracemalloc.start, snapshot, 파일 생성 호출=0이다. `test_profiler_produces_loadable_bounded_snapshots`는 임시 디렉터리에서 max=2로 snapshot을 두 개만 생성하고 `tracemalloc.Snapshot.load` 성공, 관련 manifest에 UTC/pid/sequence/traced current/peak/RSS가 있음을 assert한다. `test_profiler_disk_failure_and_cancel_are_nonfatal`은 permission/dump 실패 시 제한된 경고 후 프로파일링이 멈추지만 앱은 계속 실행되고 종료가 끝남을 assert한다. `test_existing_tracemalloc_owner_is_not_stopped`는 외부 tracing 감지 시 warning과 skip, 기존 tracing이 유지됨을 assert한다. `test_profile_budget_and_single_flight`는 snapshot 동시 실행 최대=1, snapshot 파일 총량 128 MiB 도달 시 다음 dump를 하지 않으며 이전 파일은 삭제하지 않음을 assert한다.
- [ ] Step 2: `uv run pytest services/market-http/tests/test_memory_observability.py services/market-http/tests/test_settings.py -v` 실행. 신규 API/설정 부재로 FAIL 확인.
- [ ] Step 3: enabled일 때만 lifespan에서 소유권 있는 `tracemalloc.start(5)`와 profiler task를 시작한다. baseline을 즉시 수집하고 이후 지정 간격마다 최대 12개(기본값)까지 수집한다. snapshot 생성과 dump는 단일 `asyncio.to_thread` 작업으로 직렬 실행한다. 각 run은 새 하위 디렉터리를 사용하며 파일명은 pid/sequence만 사용한다. dump 파일과 작은 JSON manifest를 기록하고 객체나 payload 내용은 log로 출력하지 않는다. snapshot은 보관하지 않고 dump 후 참조를 해제한다. max 도달 또는 디스크 오류/128 MiB 예산 도달 시 해당 실험 수집만 중지하고 자신이 시작한 tracing을 정리한다. 단일 dump 자체가 한도를 넘을 수 있으며 안전한 사전 allocation 상한은 보장하지 못함을 문서화한다.
- [ ] Step 4: shutdown에서는 profiler task를 cancel하되 진행 중인 단일 dump는 완료/실패를 회수한 뒤 종료한다. snapshot을 강제로 확보하기 위해 서비스 종료를 반복 지연하거나 OOM 직전 추가 dump를 시도하지 않는다. 전체 프로세스 profiling이 이미 활성화되어 있으면 이 기능은 건너뛴다. `__main__`에서 설정을 실제 app lifecycle까지 전달하고 기본 비활성 경로는 기존 테스트를 보존한다.
- [ ] Step 5: 같은 테스트와 `uv run pytest services/market-http/tests/test_main.py services/market-http/tests/test_app.py -v` 실행. subprocess temp-directory 실험에서 snapshot load와 max-files assertion이 PASS임을 확인한다. 실제 OOM 재현이나 운영 filesystem 권한을 확인했다고 표현하지 않는다.
- [ ] Step 6: 해당 파일만 stage 후 `git commit -m "feat: add opt-in market HTTP memory experiment profiling"`.

## Task 5: 운영 문서, 실제 scrape 통합 검증, Cloud 인계

**Files:** `docs/operations/market-http-redis-recovery-observability.md` (create), `README.md` (modify), `services/market-http/tests/test_prometheus.py`, `test_market_streams.py` (modify).

**Interfaces:**
- Consumes: Tasks 1–4의 metric/log/config 계약과 snapshot 파일.
- Produces: 실행 가능한 안전한 로컬 검증 절차, bounded 실험 결과 기록 형식, Cloud 인계 checklist. 실제 FIS 실행과 운영 배포는 구현 PR에 포함하지 않는다.

- [ ] Step 1: 통합 테스트 `test_metrics_listener_exposes_queue_receive_and_live_delivery`를 추가한다. disposable Valkey와 실제 Uvicorn/SSE 및 `metrics.serve('127.0.0.1', 0)`를 사용한다. snapshot 후 publish 1건을 SSE에서 읽고 metrics HTTP GET에서 received=1, live sent=1, 기존 total sent=2, 큐 지표 노출, 비ASCII payload bytes 정확도를 assert한다. SSE 연결 종료 후 현재 gauges=0을 기다려 확인한다. datastore 미설정이면 명시 skip한다. restart 테스트에서 새 registry의 counters/peak가 0이고 이전 queue reference가 남지 않는지 확인한다.
- [ ] Step 2: 전용 Valkey가 제공된 경우 `KTB_TEST_VALKEY_URL=<disposable-test-url> uv run pytest services/market-http/tests/test_market_streams.py services/market-http/tests/test_prometheus.py -v` 실행. 신규 통합 assertion의 FAIL을 확인하고 Tasks 1–4 연결 누락만 수정한 뒤 PASS를 확인한다. URL은 운영 값을 사용하거나 문서에 credential을 기록하지 않는다.
- [ ] Step 3: 운영 문서를 작성하고 README에서 연결한다. metric 이름/type/unit/labels/추정 오차, 설정의 실제 기본값, loopback scrape 명령, 두 Compose pass-through, tracer 기본 비활성, current RSS와 peak 차이, ASGI 완료와 클라이언트 수신 차이, SDK seam과 process 재시작 counter reset을 포함한다. snapshot을 `tracemalloc.Snapshot.load(path)` 후 baseline과 `compare_to(..., 'traceback')`로 비교하는 오프라인 예시를 제공한다. 새로운 프로파일링 패키지나 앱 자체의 S3 uploader는 도입하지 않는다.
- [ ] Step 4: 아래 Cloud runbook/완료 증거와 미검증 가정을 문서에 그대로 포함한다. 구현 완료와 #420의 실험 증거 완료를 별도 상태로 표시한다. FIS 결과가 없으면 #420 전체 완료로 보고하지 않는다.
- [ ] Step 5: `uv run pytest services/market-http -v`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run tach check`, `uv run tach check-external -e services`, `git diff --check` 실행. 모두 성공 또는 외부 리소스 skip/blocker를 명시한다. 공개 schema/example 변경을 하지 않았는지도 diff로 확인한다. 의존성이 바뀌지 않았다면 lock/export 재생성은 하지 않는다. Docker가 가능하면 `docker build -f docker/market-http.Dockerfile -t market-http-420-check .`와 결합 app 이미지 build를 실행하고, 불가하면 확인하지 못했다고 보고한다.
- [ ] Step 6: 문서와 통합 테스트만 stage 후 `git commit -m "docs: document Redis recovery OOM investigation handoff"`. `git diff --stat origin/dev...HEAD`와 작업 트리를 확인해 다른 서비스/리소스/복구 정책 변경이 없는지 검토한다. PR 생성과 이슈 본문 수정은 별도 요청 전 하지 않는다.

### Cloud runbook와 역할 경계

AI 서비스 담당 산출물: 지표 endpoint, 위 log schema, opt-in profiler, 로컬 단위/통합 검증, 이미지, 활성화 문서. Cloud 담당 산출물: 테스트 환경 변수와 Task 식별자 주입, 내부 scrape 경로와 target metadata, dashboard/수집 간격, FIS 대상과 승인, 부하 발생기, 클라이언트 수신 지연, artifact 영속화와 시간축 대조. 이 계획은 Cloud repository/GHA/SSM, IAM, ECS memory, ALB, exporter 설정을 변경하지 않는다. 현재 AI repo 배포 파일의 존재를 Cloud 역할 합의로 오해하지 않는다.

1. Cloud와 대상 환경/승인/중단 조건을 정한다. issue의 기준 조건을 보존한다: R1 SSE 900/Task 2, 한 Task Redis 방향 60초 100% 손실; R3 SSE 2,000/Task 3, 동일 손실. Task 메모리 기준 2GiB를 바꾸지 않는다. R2 200ms/60초는 선택적 비교군이다.
2. 매 실험마다 image SHA, SDK 버전, Task definition/revision, 2GiB limit 증거, worker 수, Task ID/로그 stream/Prometheus target의 매핑, UTC clock 동기화, 부하 분산과 실제 연결 수를 기록한다. 소스 HEAD가 배포 SHA와 같다고 가정하지 않는다.
3. 장애 전 idle/scrape smoke를 확인하고 baseline snapshot이 load되는지 확인한다. 일반 실행과 profiler 실행을 같은 조건에서 비교해 CPU/RSS/지연/디스크 오버헤드를 측정한다. 프로파일링 자체가 OOM 시각을 앞당기거나 지연시킬 수 있다.
4. Cloud 제안 수집 간격은 실험 중 1초이며 최종값은 Cloud가 부하/수집비용을 검증해 결정한다. 30초 수집의 한계를 보완하되 gauge가 1초 사이 spike를 모두 보장하지는 않는다. queue peak와 누적 drop counter를 함께 사용한다.
5. PromQL 예시: `rate(market_http_pubsub_messages_received_total[10s])`, `rate(market_http_sse_live_events_sent_total[10s])`, `rate(market_http_sse_events_sent_total[10s])`, `max_over_time(market_http_sse_queue_max_events[1m])`. scrape cadence가 rate window에 충분한 표본을 제공하는지 확인한다. fan-out multiplicity와 snapshot/invalid 차이가 있으므로 수신/전송 카운터를 1:1 비교해 유실을 단정하지 않는다.
6. `t_inject`, `t_release`는 FIS 자료에서, 앱의 detect/connect/resubscribe/ACK는 log에서, queue/receive/send/RSS는 metrics에서, end-user latency는 부하 클라이언트에서 얻는다. 같은 Task와 UTC 축에 놓는다. 정상/장애/해제 후 최소 30초를 포함하고 원본 시세 내용은 저장하지 않는다.
7. Cloud가 writable 경로, ephemeral storage 한도, 운영자 접근, OOM/SIGKILL 이후 snapshot 회수 방법(공유 volume/별도 수집기 등)을 준비한다. 앱은 로컬 dump만 수행한다. artifact에 코드 경로가 포함될 수 있으므로 접근/보존 정책을 정하고 외부 공개하지 않는다.
8. 산출 증거 표는 `run_id | image_sha | task_id | FIS start/end UTC | detect/connect/ACK UTC | queue peak/bytes/drop | receive/live-send rate | RSS peak | profile paths | client latency | outcome/limitations`로 한다. OOM이 나면 마지막 파일이 없거나 불완전할 수 있음을 명시한다. 디스크 dump 하나가 2~3초 급증을 정확히 잡는다고 약속하지 않는다.
9. R1/R3 각각 timeline, 원본 metrics/log export, load 가능한 baseline/후속 profile(또는 OOM 때문에 없는 이유), 추정 증가 allocation 경로와 반증/미확인 부분을 남긴다. 증거가 없으면 원인을 결론내리지 않고 수집 공백으로 기록한다. 원인 수정은 별도 이슈다.

### 미검증 운영 가정과 남은 결정

- issue의 OOM/2GiB/2~3초와 R1/R3 조건은 보고된 관측이다. 여기서 커널 로그, ECS/FIS 원본, 배포 이미지는 직접 확인하지 않았다.
- 실제 ECS worker 수, redis-py/parser/RESP/TLS 버전과 socket 설정, 트래픽 방향, Cloud metric target label/로그 metadata 구성은 확인하지 않았다. receive/queue 지표가 낮아도 SDK parser/TCP/TLS/native allocation을 배제할 수 없다.
- tracemalloc은 활성화 이후 Python 추적 allocation만 측정하며 native/TLS/kernel 메모리를 설명하지 못한다. `/proc`의 현재 RSS와 cgroup 전체 memory는 다르다. SIGKILL은 종료 hook을 실행하지 않는다.
- Task ID 주입 방법, 실험용 private scrape 1초 수집 가능 여부, writable/영속 artifact 경로와 보존 정책은 Cloud 결정이 필요하다.
- 제안 기본값 `10초`, `12개`, `5 frames`, `128 MiB`는 팀 합의가 아니다. 정상/장애 비교로 profiling 비용을 확인한 뒤 재실험 설정을 결정해야 한다. 필요하면 다음 재실험에서 interval을 1초까지 낮출 수 있으나 수집 비용과 stall을 먼저 확인한다.
- 기본 profiling이 2~3초 spike를 놓칠 수 있다. R1/R3에서 queue peak/전환 log/고주기 RSS와 함께 증거가 부족하면 별도 승인된 native profiler 실험을 후속 제안한다. 이번 구현에 상시 native profiler나 메모리 증설을 넣지 않는다.

## Self-review 결과

- Spec coverage: 큐 현재/최대/포화/드롭/slow close/bytes/처리대기=Tasks 1–2, receive/SSE rate=Task 2, 전환 log와 Task 상관관계=Task 3, opt-in profile와 비용/한계=Task 4, endpoint/docs/Cloud 재실험 evidence=Task 5. 실제 R1/R3 자료 확보는 구현만으로 충족되지 않으며 명시적 후속 인계다.
- Step scan: 각 task에 실패 테스트, FAIL 명령, 정확한 파일/API/고정값, PASS 명령, scoped commit이 있다. Task 5의 문서와 수동 실험은 실행/검토 증거로 검증하고 가짜 테스트 실패를 만들지 않는다.
- Type consistency: queue hook은 `QueueMetrics`와 `ObservedQueue`, log hook은 `SubscriptionObserver`, 설정은 `Settings`, registry는 단일 `MarketMetrics`를 공유한다. 기존 `subscribe()`의 asyncio.Queue 호환성과 MarketEvent 3개 positional 인자를 보존한다.
- Review Focus: 다섯 위험군을 각각 Tasks 1–4의 구체적 테스트에 배정했고 Task 5에서 real HTTP scrape와 새 registry 초기화를 검증한다.
- Proportion: 함수 본문을 계획에 복제하지 않고 metric 계약, SDK seam, 회계/프로파일 안전성, 검증 assertion처럼 구현자가 임의 결정하면 안 되는 부분만 고정했다. 구현 범위는 #420 관측에 한정하고 새 복구/배포 아키텍처를 포함하지 않는다.
