# market-http 실험용 메모리 캡처

기본 프로파일은 비활성이다. 활성화하더라도 기존 `startup` 모드는 유지한다.

운영 ECS 실험에서는 `MARKET_HTTP_MEMORY_PROFILE_ENABLED=true`, `MARKET_HTTP_MEMORY_PROFILE_START_MODE=trigger`, `MARKET_HTTP_MEMORY_PROFILE_BURST_TRIGGER=true`를 사용한다. CD 렌더러는 프로파일 활성화 시에만 `market-memory` host volume을 추가하고 `/profiles`에 연결한다. host source는 `/var/lib/stockspoon/market-memory`로 고정하며 다른 profile 경로/충돌 mount는 거부한다. 명시하지 않은 start mode는 ECS 렌더러에서 trigger, profile directory는 /profiles가 된다. Task CPU·2GiB 메모리·FIS 사이드카는 보존한다.

배포 전에 Cloud 담당이 각 대상 ECS 호스트의 디렉터리 쓰기 권한과 용량을 준비해야 한다. 실행 UID/GID를 이미지에서 확인한다. 디렉터리 접근 실패가 나도 앱은 계속 실행하고 실패 로그를 남긴다. 이 문서나 코드 수정만으로 운영 환경이 배포되는 것은 아니다.

## 트리거 계약

- `trigger` 모드에서는 프로파일 root 아래 새 run directory와 `profile-state.json`을 만들고 `armed` 상태로 대기한다. 대기 중에는 tracemalloc을 시작하지 않는다.
- state에는 hostname, namespace PID, 선택적 Task ID, UTC 시각이 있다. Cloud가 Docker label과 startup 시각으로 대상 컨테이너를 먼저 검증한 후 해당 run directory를 선택한다.
- 운영자가 해당 run directory의 `capture.trigger` 파일을 생성하면 기준선 캡처와 tracing을 시작한다. 파일 내용은 실행하거나 해석하지 않는다. 트리거는 rename으로 한 번 소비한다.
- `BURST_TRIGGER=true`이면 baseline 완료 후 state가 `baseline_ready`가 된다. 이때 주기적 스냅샷은 기다리므로 부하 준비 중 개수/덤프 바이트 예산을 소진하지 않는다. tracing 자체의 비용은 존재한다.
- 실제 FIS 장애 해제 직전 `capture.burst` 파일을 만들면 집중 캡처를 시작한다. 파일은 claim되며 이벤트 `memory_profile_burst_started`를 기록한다. 600초 내 요청이 없으면 tracing을 중단한다.
- burst가 false이면 첫 트리거 이후 기존 주기 캡처를 즉시 수행한다. 기본 `startup` 모드에는 별도 트리거가 없다.
- interval은 최소 1초, snapshots는 최대 120개, 기존 dump 바이트 예산은 약 128MiB다. 마지막 파일 한 개는 예산을 초과할 수 있다. 덤프 소요 시간 때문에 실제 간격은 길어질 수 있다. 각 스냅샷 크기로 관찰 가능한 구간을 먼저 검증한다.
- 한 프로세스당 한 캡처 세션이다. `finished`는 bounded 수집 작업 종료이며 원하는 관찰 구간의 완전 수집을 뜻하지 않는다. count, timestamps, stop 로그와 baseline 파일을 반드시 확인한다.
- 타 도구가 소유한 tracemalloc은 시작/종료하지 않는다. 앱 종료 때 대기와 캡처를 취소한다. OOM/SIGKILL은 종료 hook을 실행하지 않으므로 마지막 덤프가 없을 수 있다.

## 완료 파일과 분석

스냅샷은 `.snapshot.partial`에 쓴 후 rename하고 manifest도 `.json.partial`에 쓴 후 rename한다. 회수기는 `.snapshot`과 짝 `.json`이 모두 있는 파일만 복사해야 한다. 컨테이너가 삭제돼도 ECS 호스트 bind mount 파일은 남지만 호스트/볼륨 손실까지 보호하지는 않는다. Cloud는 완성 파일을 별도로 회수하고 실험 증거를 영속 보관해야 한다.

```python
import tracemalloc
baseline = tracemalloc.Snapshot.load("baseline.snapshot")
after = tracemalloc.Snapshot.load("after.snapshot")
for row in after.compare_to(baseline, "traceback")[:20]:
    print(row)
```

신뢰할 수 있는 자체 실험 파일만 load한다. Python allocation 차이와 외부 cgroup/RSS를 함께 비교한다. native/TLS/kernel allocation은 tracemalloc만으로 설명되지 않는다. 프로파일 활성화 전후 동일 부하에서 CPU/RSS/지연/파일 크기 비용을 비교한 뒤 R1/R3 증거 수집에 사용한다. 애플리케이션 큐/재시도/복구 정책이나 메모리 한도를 바꾸는 기능은 아니다.

Cloud 절차: Cloud repo `fis/market-http/CAPTURE.md` 및 `capture.py`.
