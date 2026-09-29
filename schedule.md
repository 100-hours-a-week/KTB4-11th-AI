# EC2 스케줄링

EC2의 systemd timer가 `compose.prod.yaml`의 단발 작업을 실행한다. unit 원본은
`infrastructure/systemd/`에 두고, CD가 EC2의 `/etc/systemd/system/`에 설치한다. 모든
시간은 `Asia/Seoul`을 명시한다.

```
00:00
  │
  │
06:00  Morning Pipeline 시작
  │     market-collector
  │        ↓
  │     news-preprocessor
  │        ↓
  │     news-clusterer
  │        ↓
  │     news-graph-builder
  │        ↓
  │     portfolio-builder
  │
06:10  news-preprocessor ──┐
07:10  news-preprocessor   │ 동일 Lock
08:10  news-preprocessor   │ → Pipeline과 중복 실행 X
  │                        │
08:50  Pipeline 최대 종료 ─┘
  │
09:00 ┌─────────────────────────────────────┐
      │ portfolio-rebalancer-http 상시 실행 │
      │                                     │
      │ 주문 가능 시간 09:00 ~ 15:30        │
      └─────────────────────────────────────┘
09:10  news-preprocessor
10:10  news-preprocessor
11:10  news-preprocessor
12:10  news-preprocessor
13:10  news-preprocessor
14:10  news-preprocessor
15:10  news-preprocessor
15:30  주문 가능 시간 종료
16:10  news-preprocessor
...
23:10  news-preprocessor
```

## 장 시작 전 파이프라인

평일 06:00에 `ktb-morning-pipeline.timer`가 `scripts/run-morning-pipeline.sh`를 실행한다.
앞 단계가 실패하면 뒤 단계는 실행하지 않는다.

1. `market-collector`
2. `news-preprocessor`
3. `news-clusterer`
4. `news-graph-builder`
5. `portfolio-builder`

파이프라인은 실패 시 5분 후 최대 2회 더 시도하며, 최대 실행 시간은 2시간 50분이다.
`Persistent=false`므로 EC2가 오전 9시 이후에 복구되어도 놓친 파이프라인을 늦게 실행하지
않는다.

## 독립 작업

- `news-preprocessor`: 매시 10분에 실행한다. 아침 파이프라인과 같은 lock을 사용해
  두 작업이 겹치지 않는다.
- `market-syncer`: 현재 저장소에 서비스와 Compose 정의가 없어 timer를 추가하지
  않았다. 서비스가 추가되면 매시 30분에 실행한다.
- `portfolio-rebalancer-http`: 배치 작업이 아니라 HTTP 서버이므로 CD가 상시 실행한다.
  09:00~15:30 주문 제한과 한국거래소 휴장일 확인은 주문 로직에서 별도로 강제해야
  한다.

## 배포와 동시 실행 방지

CD와 systemd 배치 작업은 모두 `~/ai/.news-pipeline.lock`을 사용한다. 아침
파이프라인이나 시간당 `news-preprocessor`가 실행 중이면 배포는 대기하지 않고
실패한다. 배치 작업이 끝난 뒤 실패한 GitHub Actions workflow를 재실행한다.
이로써 하나의 파이프라인이 서로 다른 이미지 SHA를 사용하는 일을 막는다.

### 긴급 hotfix

실행 중인 배치를 중단해야 할 정도로 긴급한 경우만 EC2에서 다음을 수행한다.

```bash
sudo systemctl stop ktb-morning-pipeline.service ktb-news-preprocessor.service
systemctl is-active ktb-morning-pipeline.service ktb-news-preprocessor.service
docker ps --filter label=com.docker.compose.project=ktb4-ai \
  --filter label=com.docker.compose.oneoff=True
```

두 service가 모두 `inactive`이고 실행 중인 Compose one-off 컨테이너가 없는 것을
확인한 뒤 hotfix CD를 재실행한다. 컨테이너가 남았다면 해당 배치의 컨테이너인지
확인하고 중단한다. lock 파일을 `rm`하여 우회하지 않는다. 실행 중인 프로세스가
기존 inode의 lock을 계속 보유할 수 있어 배포와 배치가 동시에 실행될 수 있다.

hotfix 배포가 장 시작 전에 끝났고 당일 포트폴리오를 다시 만들어야 하면 새
이미지로 전체 파이프라인을 재실행한다.

```bash
sudo systemctl start ktb-morning-pipeline.service
journalctl -fu ktb-morning-pipeline.service
```

장 시작 후에는 파이프라인을 임의로 재실행하지 않는다. hotfix로 중단된 작업의
부분 결과는 각 서비스의 멱등성과 트랜잭션 보장을 기준으로 확인한다.

## EC2에서 확인

```bash
systemctl list-timers 'ktb-*'
systemctl status ktb-morning-pipeline.timer ktb-news-preprocessor.timer
journalctl -u ktb-morning-pipeline.service
journalctl -u ktb-news-preprocessor.service
```
