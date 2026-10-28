# market-http Redis 복구·메모리 관측 운영 인계

## 상태와 범위

- **구현 완료:** market-http에는 loopback Prometheus endpoint, SSE/Redis/queue 지표, 구조화된 복구 전환 로그, opt-in `tracemalloc` snapshot과 로컬·disposable Valkey 통합 검증이 있다. 구현 범위의 테스트는 `KTB_TEST_VALKEY_URL`이 주어질 때 실제 Valkey, Uvicorn SSE, metrics listener를 함께 검증한다.
- **#420 FIS 실험 증거:** **미실행**. 이 저장소에는 FIS 실행, 대상 Task 배포, production credential, image deployment, ECS/FIS 원본 자료가 없다. 따라서 R1/R3의 OOM 원인이나 2 GiB 한계에서의 결과를 완료·재현·확인했다고 주장하지 않는다.
- 이 문서는 Cloud repository/GHA/SSM/IAM/ECS/ALB, exporter 설정 또는 배포 정책을 변경하지 않는다. 앱은 S3 uploader를 포함하지 않으며 snapshot은 로컬 dump만 만든다.

## 안전한 로컬 검증

`KTB_TEST_VALKEY_URL`에는 `FLUSHDB`가 가능한 전용 disposable Valkey database/instance만 사용한다. 운영·개발 공유 datastore 또는 credential을 명령, 문서, 로그에 넣지 않는다. 변수가 없으면 integration test는 `KTB_TEST_VALKEY_URL is not set`으로 명시적으로 skip한다.

```bash
KTB_TEST_VALKEY_URL=<disposable-test-url> uv run pytest \
  services/market-http/tests/test_market_streams.py \
  services/market-http/tests/test_prometheus.py -v
```

`test_metrics_listener_exposes_queue_receive_and_live_delivery`는 snapshot 뒤 한 건을 publish하여 실제 SSE에서 읽고, 별도 `metrics.serve('127.0.0.1', 0)` endpoint를 scrape한다. `received=1`, live send `=1`, total SSE send `=2`, queue metric 노출, 비ASCII payload의 UTF-8 byte count, SSE 종료 후 현재 queue gauge `=0`을 확인한다. 새 `MarketMetrics` registry가 counter와 queue high-watermark를 0부터 시작하고 이전 queue reference를 보관하지 않는지도 확인한다.

loopback listener를 수동 확인할 때는 application process와 같은 host namespace에서만 다음처럼 조회한다.

```bash
curl --fail http://127.0.0.1:${MARKET_HTTP_PROMETHEUS_PORT:-9100}/metrics
```

Prometheus listener의 실제 기본 bind/port는 `127.0.0.1:9100`이다. `compose.local.yaml`과 `compose.prod.yaml`은 `MARKET_HTTP_PROMETHEUS_BIND_HOST`, `MARKET_HTTP_PROMETHEUS_PORT`, `MARKET_HTTP_TRACE_SPANS_PER_SECOND`, `MARKET_HTTP_TASK_ID`, 그리고 네 개의 `MARKET_HTTP_MEMORY_PROFILE_*` 값을 market-http에 pass-through한다. 둘 다 `OTEL_SDK_DISABLED=${MARKET_HTTP_OTEL_SDK_DISABLED:-true}`이므로 tracer SDK는 기본 비활성이다. Cloud가 필요한 값을 주입하되 이 문서의 값만으로 endpoint를 외부에 노출하지 않는다.

## Metric 계약과 해석 한계

| Metric | Type / unit | Labels | 의미와 한계 |
| --- | --- | --- | --- |
| `market_http_pubsub_messages_received_total` | counter / messages | `channel` | 알려진 Redis Pub/Sub channel에서 validation 전 수신한 메시지. 수신과 fan-out 전송은 1:1이 아니다. |
| `market_http_pubsub_payload_received_bytes_total` | counter / UTF-8 bytes | `channel` | validation 전 payload의 `len(data.encode("utf-8"))`. Redis/TLS/socket/native buffer 크기의 추정치가 아니다. |
| `market_http_sse_events_sent_total` | counter / complete SSE data events | `stream` | ASGI `send` 완료 뒤의 SSE data frame. 실제 client 수신/표시는 보장하지 않는다. |
| `market_http_sse_events_sent_bytes_total` | counter / wire bytes | `stream` | 완전한 SSE data frame의 bytes. HTTP/TCP 재전송과 client-side buffering은 포함하지 않는다. |
| `market_http_sse_live_events_sent_total` | counter / events | `stream` | Pub/Sub에서 온 frame만; snapshot과 ping은 제외한다. |
| `market_http_sse_delivery_latency_seconds` | histogram / seconds | `stream` | Pub/Sub receipt부터 ASGI send 완료까지. client 수신 지연은 아니다. |
| `market_http_sse_connections` | gauge / connections | `stream` | 수락되어 현재 열린 SSE 연결. |
| `market_http_sse_connections_started_total`, `market_http_sse_connections_closed_total` | counter / connections | `stream`, closed metric은 `reason` | 수락/종료 수. reason은 `normal`, `client_disconnect`, `cancelled`, `send_error`, `server_shutdown`, `unknown`. |
| `market_http_sse_queues`, `market_http_sse_queue_events`, `market_http_sse_queue_payload_bytes`, `market_http_sse_queue_max_events` | gauge / queues, events, estimated UTF-8 bytes, events | `stream` | 현재 fan-out queue 상태. bytes는 event payload 추정치이며 Python object overhead나 transport buffer가 아니다. |
| `market_http_sse_queue_high_watermark_events` | gauge / events | `stream` | process 시작 이후 최대 관측 depth; process restart에서 0으로 reset한다. |
| `market_http_sse_queue_full_total`, `market_http_sse_queue_discarded_events_total`, `market_http_sse_slow_subscribers_closed_total` | counter / incidents or events | `stream`, discarded metric은 `reason` | queue overflow/폐기/slow subscriber close. 누적 counter는 process restart에서 0으로 reset한다. |
| `market_http_sse_send_operations_in_flight`, `market_http_sse_send_body_bytes_in_flight`, `market_http_sse_observer_buffer_bytes`, `market_http_sse_delivery_markers_pending` | gauge / operations or bytes | `stream` | 현재 ASGI send·framing·delivery marker 보유량. total resident memory가 아니다. |
| `market_http_valkey_pool_connections` | gauge / connections | `state` | `in_use`, `available`, `acquiring`; acquiring은 acquisition call이며 internal wait queue가 아니다. |
| `market_http_valkey_pool_acquire_duration_seconds`, `market_http_valkey_pool_acquire_timeouts_total` | histogram seconds / counter incidents | histogram `result` | 실제 acquire 결과/timeout만 관측한다. |
| Prometheus process metrics | gauge/counter / bytes, seconds, CPU etc. | prometheus-client standard labels | current process RSS는 순간값이다. `tracemalloc` peak나 cgroup 전체 memory와 다르다. |

`market_http_sse_events_sent_total`은 ASGI 완료 지점이고 client가 frame을 수신하거나 UI가 처리한 지점이 아니다. end-user latency는 Cloud load client가 별도로 기록해야 한다. queue receive/send counters는 snapshot, invalid message, filter, fan-out multiplicity 때문에 단순 1:1 유실 판정에 사용하지 않는다.

## Opt-in memory profile

설정의 실제 기본값은 다음과 같다.

| Setting | Default | 제한 |
| --- | --- | --- |
| `MARKET_HTTP_MEMORY_PROFILE_ENABLED` | `false` | 명시적으로 켜야 한다. |
| `MARKET_HTTP_MEMORY_PROFILE_DIR` | `/tmp/market-http-memory` | local writable path가 필요하다. |
| `MARKET_HTTP_MEMORY_PROFILE_INTERVAL_SECONDS` | `10` seconds | 최소 1 second. |
| `MARKET_HTTP_MEMORY_PROFILE_MAX_SNAPSHOTS` | `12` | 1–120. |
| snapshot disk budget | `128 MiB` | 앱 내 상한이며 S3 upload가 없다. |
| `MARKET_HTTP_TRACE_SPANS_PER_SECOND` | `100` | tracer SDK는 Compose 기본값에서 disabled다. |

manifest에는 UTC, pid, sequence, `traced_current_bytes`, `traced_peak_bytes`, current RSS가 기록된다. `traced_current_bytes`는 현재 추적 Python allocation이고 `traced_peak_bytes`는 profiling 시작 이후의 peak이다. 둘은 RSS도 cgroup memory도 아니며, `/proc` current RSS는 현재 process 값일 뿐이다. `tracemalloc`은 활성화 이후 Python allocation만 본다. native/TLS/kernel memory 또는 SIGKILL 뒤의 종료 hook은 설명하지 못한다.

baseline과 후속 snapshot은 앱 밖에서 다음처럼 비교한다. raw market data를 artifact에 넣거나 외부 공개하지 않는다.

```python
import tracemalloc

baseline = tracemalloc.Snapshot.load("/safe/local/path/baseline.snapshot")
after = tracemalloc.Snapshot.load("/safe/local/path/after.snapshot")
for stat in after.compare_to(baseline, "traceback")[:20]:
    print(stat)
```

## Cloud runbook와 역할 경계

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

## 미검증 운영 가정과 남은 결정

- issue의 OOM/2GiB/2~3초와 R1/R3 조건은 보고된 관측이다. 여기서 커널 로그, ECS/FIS 원본, 배포 이미지는 직접 확인하지 않았다.
- 실제 ECS worker 수, redis-py/parser/RESP/TLS 버전과 socket 설정, 트래픽 방향, Cloud metric target label/로그 metadata 구성은 확인하지 않았다. receive/queue 지표가 낮아도 SDK parser/TCP/TLS/native allocation을 배제할 수 없다.
- tracemalloc은 활성화 이후 Python 추적 allocation만 측정하며 native/TLS/kernel 메모리를 설명하지 못한다. `/proc`의 현재 RSS와 cgroup 전체 memory는 다르다. SIGKILL은 종료 hook을 실행하지 않는다.
- Task ID 주입 방법, 실험용 private scrape 1초 수집 가능 여부, writable/영속 artifact 경로와 보존 정책은 Cloud 결정이 필요하다.
- 제안 기본값 `10초`, `12개`, `5 frames`, `128 MiB`는 팀 합의가 아니다. 정상/장애 비교로 profiling 비용을 확인한 뒤 재실험 설정을 결정해야 한다. 필요하면 다음 재실험에서 interval을 1초까지 낮출 수 있으나 수집 비용과 stall을 먼저 확인한다.
- 기본 profiling이 2~3초 spike를 놓칠 수 있다. R1/R3에서 queue peak/전환 log/고주기 RSS와 함께 증거가 부족하면 별도 승인된 native profiler 실험을 후속 제안한다. 이번 구현에 상시 native profiler나 메모리 증설을 넣지 않는다.
