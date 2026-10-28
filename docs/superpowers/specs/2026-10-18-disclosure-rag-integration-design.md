# 공시 RAG를 dev에 통합

날짜: 2026-10-18
상태: 구현 및 서비스 테스트 완료, 실제 DB·Compose·모델 서버 검증 대기

## 목적

`.codex/worktrees/disclosure-rag`(detached HEAD `9aa7679`, 미커밋)에 있는 공시 RAG 구현을
현재 `dev`로 옮기면서, 두 차례 코드 리뷰에서 나온 지적을 모두 수정하고 저장소의 기존
관례에 맞춘다. 기능 범위를 넓히지 않는다.

worktree 기준 시점 이후 `dev`에 122커밋이 쌓였고 `market-http`·`market-streamer`·valkey가
추가됐으며 `compose.dev.yaml`이 `compose.local.yaml`로 바뀌었다. 이 문서는 `dev`가
`3709126`일 때를 기준으로 한다. 분기 시점에 다시 확인한다.

## 확정 요구사항

이전 설계에서 유지되는 것:

1. OpenDART 정기공시를 수집·임베딩해 portfolio-builder가 검색 도구로 쓴다.
2. 공시 문서와 질의는 같은 파인튜닝 체크포인트와 같은 벡터 공간을 쓴다.
3. 실행 시간과 흐름이 다르므로 indexer와 retrieval을 별도 서비스로 둔다.
4. 공시 수집·저장은 indexer로 모으고 독립 collector 서비스는 두지 않는다.
5. analyzer와 마찬가지로 RAG 도구도 정상 호출을 완료해야 새 포트폴리오를 저장할 수 있다.
6. 평가·학습 자료는 저장소 밖에 둔다. 이를 위한 ignore 규칙을 추가하지 않는다.
7. 검색은 벡터 단독이다. BM25 하이브리드는 tuned 모델에서 역전됐다.

이번에 바뀌는 것:

8. **임베딩 HTTP 클라이언트를 `packages/core`에 두지 않는다.** 각 공시 서비스가 자기 것을 가진다.
9. **현재 차원은 2000으로 고정하고 `halfvec(2000)`의 코사인 거리를 정확 검색한다.** 2000차원은 기존
   Qwen3-Embedding-8B 평가에서 선택된 값이다. context-9b 2048차원 실험이 끝나기 전까지
   서비스 스키마를 2048로 바꾸지 않는다. 모델·차원 변경은 migration과 전체 재색인이 필요하다.
10. **indexer 실패가 아침 파이프라인의 나머지 작업을 막지 않는다.** 실패는 기록하고 종료 코드로 알린다.
11. **news-preprocessor의 `opendart` publisher를 유지한다.** indexer가 대체하지 않는다.

## 통합 전략

`dev`에서 `feat/disclosure-rag`를 분기하고 worktree의 파일을 옮긴다. 충돌 네 곳은 모두
기계적이며 양쪽 변경을 합친다.

| 파일 | dev가 추가한 것 | 처리 |
|---|---|---|
| `tach.toml` | `market_http`, `market_streamer` source root와 모듈 | 공시 두 모듈을 추가 |
| `docker/app.Dockerfile` | market-http, market-streamer 설치 | 공시 두 서비스를 추가 |
| `.github/workflows/ci-dev.yaml` | valkey 서비스, `KTB_TEST_VALKEY_URL`, 패키지 두 개 | dev 기준에 공시 두 개 추가 |
| portfolio-builder `settings.py`·`__main__.py` | `MARKET_HTTP_BASE_URI`, market-http 경유 bars | dev 기준 위에 retrieval만 얹는다 |

compose 변경 대상은 `compose.local.yaml`과 `compose.prod.yaml`이다. worktree가 수정한
`compose.dev.yaml`은 더 이상 존재하지 않는다.

`dev`에는 공시 코드가 전혀 없다. collector 서비스도, news-http의 공시 route도 없다.
따라서 worktree가 "제거"로 기록한 변경 중 news-preprocessor `opendart` publisher만
실제 추적 파일 삭제이며, 그것은 11번에 따라 되돌린다.

migration revision `0011`은 `dev`와 충돌하지 않는다. `dev`의 마지막 revision은 `0010`이다.

## 임베딩 클라이언트의 위치

`packages/core/src/ktb_core/embedding/client.py`와 그 테스트를 삭제한다. 각 서비스가
`embed.py`를 가진다.

근거 세 가지가 일치한다.

- `ktb-core`는 `dependencies = []`이고 모든 모듈이 표준 라이브러리만 쓴다. `backend_auth.py`도
  pyjwt 없이 HMAC JWT를 직접 구현했다. worktree가 httpx 대신 `urllib`을 쓴 것은 core에
  넣었기 때문에 강제된 결과였다.
- `docs/superpowers/specs/2026-09-20-monorepo-init-design.md` §5 "Explicitly not in core yet"는
  "any LLM client"를 core에서 명시적으로 제외한다.
- 선례가 이미 있다. 임베딩 HTTP 클라이언트는 서비스 안에 있고(`news_preprocessor/embed.py`),
  core에는 상수만 있다(`ktb_core/embedding/config.py`).

`ktb_core/embedding/config.py`는 뉴스 전용이므로 수정하지 않는다. 공시 설정은 각 서비스의
`settings.py`가 `DISCLOSURE_EMBEDDING_*` alias로 읽는다.

두 서비스의 `embed.py`는 20줄 남짓 비슷해진다. 이 저장소는 `settings.py`를 모든 서비스에
중복시키는 쪽을 선택한 저장소이고(monorepo 설계 §5 "Settings live in each service, not in
core"), 호출 형태도 실제로 다르다.

| | indexer | retrieval |
|---|---|---|
| 입력 | 한 문서의 청크 리스트 | 질의 한 건 |
| prefix | 청크별 메타데이터·표 문맥 | 학습 질의 instruction |
| timeout 기본 | 120초 | 30초 |

**한 요청은 한 문서의 청크들로 구성한다.** 현재 `batch_size`로 청크를 임의 분할하는 것을
문서 경계 분할로 바꾼다. Qwen3-Embedding-8B는 청크가 서로 독립이라 결과가 같고,
`pplx-embed-v2-context-9b-preview`로 바꾸면 그 요청 형태가 문맥 인코딩의 전제를 이미
만족한다. `embedding_inputs()`가 청크마다 메타데이터·표 문맥 prefix를 붙이는 것은 문맥
비인식 모델을 보완하는 Qwen3 전용 전략이며, 맥락 인코딩 모델에서는 재검토 대상이다.

## 벡터 차원과 입력 길이

`disclosure_chunks.embedding`은 현재 통합 초안에서 `halfvec(2000)`이다. 공시 검색은 HNSW나 IVFFlat을 사용하지 않는다. 종목·공시일·최신 보고서 조건에 맞는 모든 청크를 먼저 `MATERIALIZED` CTE로 확정하고, 각 청크의 코사인 거리를 계산한 뒤 거리·접수번호·청크 ID 순으로 정렬한다. ANN 후보 수 제한은 없고, 최종 top-k와 보고서 수 제한만 적용한다.

migration 0011에서 공시용 HNSW 생성·삭제를 제거했고, HTTP 검색 경로의 HNSW session 설정도 제거했다. 과거 초안의 ANN 인덱스가 남은 DB에서도 materialized 필터 결과를 대상으로 거리를 계산하므로 그 인덱스의 근사 순위를 사용하지 않는다.

현재 2000차원 컬럼은 기존 Qwen 통합 초안의 값이다. 선택한 context-9b 파인튜닝 체크포인트의 1024/2048차원은 실제 모델 서빙 연동 시 스키마와 함께 맞춰야 하며, 정확 검색에는 ANN 인덱스의 2000차원 상한이 적용되지 않는다. `halfvec` 저장 정밀도는 fp16이므로 정확 검색은 저장된 벡터에 대한 거리 순위를 의미한다.

모델 교체는 여전히 새 migration과 전체 재색인을 요구한다. 무중단 전환은 지원하지 않는다.

**기준선은 fp32에서 측정됐고 `halfvec`은 fp16으로 저장한다.** 정규화 벡터의 코사인
거리에서 차이는 작지만 0은 아니다. 재측정 시 이 항목을 분리해 확인한다.

문서와 질의 임베딩 모두 `DISCLOSURE_EMBEDDING_MAX_TOKENS=4096`을 요청에 전달한다.
질의는 UTF-8 12,000바이트를 넘으면 422로 거부한다. 청크 입력은 같은 바이트 예산을 쓴다.

학습에서 측정한 입력 한도를 서비스 추론에도 적용한다. Qwen과 context-9b는 입력 포맷이
다르므로 context-9b는 별도 adapter 검증을 거친 뒤에만 선택한다.

## 스키마

`0011_create_disclosures.py`를 저장소 관례대로 다시 쓴다. 현재는 docstring 없이 거대한
`op.execute("""raw SQL""")` 한 덩어리다. `0001`·`0009`처럼 revision docstring 헤더와
`op.create_table`·`op.create_index`·`op.create_check_constraint`와 `sa.Column`을 쓴다.
벡터 타입은 `pgvector.sqlalchemy`에서 가져온다.

테이블 구성은 worktree와 같다. `disclosures`, `disclosure_documents`, `disclosure_indexes`,
`disclosure_chunks`, `disclosure_collection_progress`. 변경점은 네 가지다.

- `disclosure_chunks.embedding`은 `halfvec(2000)`이며 ANN 인덱스를 만들지 않는다.
- `disclosure_documents.documents` jsonb에서 `body`를 제거한다. 아무 제품 코드도 읽지 않으면서
  `sections`의 텍스트를 그대로 중복해 문서별 저장량을 약 두 배로 만들고, 색인할 때마다
  그 jsonb 전체가 메모리로 올라온다.
- `disclosures.attempts`에 상한을 적용한다. 현재는 증가만 하고 읽는 곳이 없다. status CHECK에
  `'dead_letter'`를 추가하고 `DISCLOSURE_INDEXER_MAX_ATTEMPTS`(기본 5)를 넘긴 접수번호를 그
  상태로 옮긴 뒤 수집 대상에서 제외한다. `0009`가 `articles.embedding_status`에 쓴 값과 같다.
- `disclosure_indexes` 작업 조회 인덱스를 `(signature, status, lease_until)`로 좁힌다.

downgrade는 신규 테이블만 삭제한다. 기존 뉴스 테이블과 데이터는 건드리지 않는다.

## 메모리 예산

운영 compose는 `disclosure-indexer`에 `mem_limit: 1g`를 주는데, 설정 기본값은 아카이브
100MB와 비압축 200MB를 허용한다. 다운로드 중 `bytearray`와 `bytes()` 복사로 내용이 두 벌
살아 있고, 거기에 zip 멤버 전개와 BeautifulSoup 트리와 추출 결과의 JSON 직렬화가 겹친다.
비압축 200MB 문서 하나로 1GB를 넘길 수 있고 OOM kill은 종료 코드 137이다.

세 숫자를 서로 맞춘다. `max_archive_bytes`를 32MB, `max_uncompressed_bytes`를 64MB로
내리고 운영 `mem_limit`을 2g로 올린다. 상한을 넘는 문서는 받지 않고 실패로 남겨
어떤 보고서가 상한을 넘는지 로그로 드러나게 한다.

이 숫자들은 실제 정기보고서를 측정하지 않은 보수적 추정이다. 목표 규모는 기업당 약 5개,
총 약 1,000개 보고서다. 첫 실제 수집에서 거부된 문서가 나오면 상한을 다시 정한다.

`body`를 jsonb에서 빼는 것도 같은 예산에 들어간다. 색인할 때 문서 jsonb 전체가 메모리로
올라오므로 중복 제거가 곧 절감이다.

## 아침 파이프라인

`scripts/run-morning-pipeline.sh`는 `set -Eeuo pipefail`이고 indexer를 맨 앞에서 호출한다.
indexer는 수집 또는 색인 실패가 하나라도 있으면 종료 코드 1로 끝나고, `collect()`는 기업
한 곳의 일시적 DART 오류에도 실패를 센다. 결과적으로 news-clusterer, news-graph-builder,
portfolio-builder가 그날 실행되지 않는다.

indexer는 맨 앞에 두되 실패가 뒤 작업을 막지 않게 한다. 각 작업의 종료 코드를 모아
스크립트 끝에서 하나라도 실패했으면 0이 아닌 코드로 끝낸다. 파이프라인이 실패로 기록되는
것은 유지하면서 포트폴리오 생성은 계속한다.

`disclosure-indexer`는 `corporations`와 `corporation_indices`를 읽으므로 최초 실행 전에
market-syncer가 먼저 돌아야 한다. market-syncer는 이 파이프라인에 없고 별도 일정으로
실행된다. 이 선행 조건을 README와 AGENTS.md에 적는다.

## news-preprocessor 복원

worktree의 news-preprocessor 삭제를 전부 되돌린다. `sources/publishers/opendart/` 패키지,
`tests/test_opendart.py`, `Settings.dart_api_key`, `storage.kospi200_stock_codes`와
`corporation_indices` 테이블 정의, `setup_logging`의 `sensitive_query_params={"crtfc_key"}`,
`.env.example`의 `NEWS_PREPROCESSOR_DART_API_KEY`, AGENTS.md의 publisher 서술을 유지한다.

publisher와 indexer는 서로 겹치지 않는 문서 집합을 다룬다. publisher는
`long_detail_types = ("A001", "A002", "A003", "F001", ...)`를 **제외하고** 짧은 공시만
수집한다. indexer는 정기보고서, 즉 `A001`~`A003`만 수집한다.
`docs/superpowers/specs/2026-09-29-opendart-publisher-design.md`는 "RAG over long filings"을
별도 이슈 #185로 기록했고, 이 작업이 그 #185다. publisher를 대체하는 작업이 아니다.

publisher를 제거하면 짧은 공시 이벤트가 news → cluster → graph 흐름에서 사라지고 대체물이
없다. `docs/superpowers/specs/2026-10-12-disclosure-rag-design.md`도 "기존 news-preprocessor의
짧은 공시 뉴스 흐름은 유지한다"고 적었다.

DART 키는 `NEWS_PREPROCESSOR_DART_API_KEY`, `MARKET_SYNCER_DART_API_KEY`,
`DISCLOSURE_INDEXER_DART_API_KEY` 세 개가 된다. 서비스별 접두사 관례를 따른 결과이며
같은 키를 세 변수에 넣어도 된다.

## 저장소 관례 정렬

- `disclosure-retrieval`의 `routes.py`와 `repository.py`를 `controllers/disclosures.py`와
  `repositories/disclosures.py`로 나눈다. news-http와 market-http가 모두 이 구조다.
- `create_app(engine, settings)`로 의존성을 주입하고 요청 처리 중 `Settings()` 생성을 없앤다.
  market-http의 `create_app(connect_valkey(settings), settings.questdb_conf)`와 같은 형태다.
  현재는 요청마다 `Settings()`를 만들고, `pydantic.ValidationError`가 `ValueError`의
  서브클래스라서 설정 누락이 503으로 위장된다. 서비스는 정상 부팅하고 `/health`는 `ok`를
  반환한다. compose가 `${DISCLOSURE_EMBEDDING_MODEL:-}`로 빈 문자열을 넘기므로 실제로
  발생할 수 있는 상태다.
- 주석을 제거한다. AGENTS.md는 "NEVER WRITE COMMENTS"다.
- `model_validator` 메서드 이름에 선행 밑줄을 붙인다. market-http의 `_iam_needs_identity`와
  같다.

## 리뷰 지적 수정

| 번호 | 문제 | 수정 |
|---|---|---|
| 1 | 파이프라인 전면 중단 | 위 "아침 파이프라인" |
| A | 아카이브 파일 유실 시 영구 실패 | 파일이 없으면 재다운로드로 폴백 |
| B | `attempts`를 쓰기만 함 | `max_attempts` 초과 시 `dead_letter`로 옮기고 수집 대상에서 제외 |
| 2 | 질의 바이트 예산 초과가 503 | 토큰 한도로 전환, 요청 스키마에서 바이트 검증 후 422 |
| 3 | 요청당 `Settings()` | 위 "저장소 관례 정렬" |
| 4 | 재파싱 루프에 상한 없음 | SQL에 `ORDER BY`·`LIMIT`, `document_limit` 적용 |
| 5 | 메모리 상한 모순 | 아래 "메모리 예산" |
| 6 | `body` 중복 저장 | 위 "스키마" |
| C | ANN 후보 누락 가능성 | 사용자 결정에 따라 ANN을 제거하고 전체 필터 후보를 정확 검색 |
| 7 | `SELECT *`로 `raw_payload` 과다 조회, `report_rows` 중복 호출 | 필요한 열만 조회하고 한 번만 호출 |
| 8 | 클라이언트 timeout 고정 40초 | `PORTFOLIO_BUILDER_DISCLOSURE_RETRIEVAL_TIMEOUT` 추가 |
| 9 | compose가 base URI를 하드코딩 | `${…:-http://disclosure-retrieval:8000}` 패턴으로 바꾼다 |
| 10 | 두 필수 조건을 한 번에 알리지 못함 | 차단 메시지에 다른 선행 조건이 남아 있을 수 있음을 적는다 |
| 11 | 임베딩 오류 원인 소실 | 상태 코드와 응답 요약을 로그에 남긴다 |
| 12 | `closing_month`가 `013`에서 `KeyError` | 명확한 오류로 바꾼다 |
| 13 | `walk`·`parse_table` 재귀 한계 | 깊이 상한을 두고 초과 시 문서를 실패 처리 |
| E | 비정상 응답이 500 | `len(data)`·`item["index"]` 접근을 검증 안으로 넣는다 |
| F | `Command(update=None)` | `result.update or {}` |
| 신규 | `test_agent.py:188` 기대값이 낡음 | 7로 고친다 |

마지막 항목은 이번 테스트에서 처음 드러났다. worktree가 `_run` 헬퍼에
`search_disclosures` 호출을 끼워 넣어 ToolMessage가 하나 늘었는데 같은 파일의
`test_the_trace_records_...`만 갱신했다. 이 테스트는 `engine` fixture를 쓰므로
`KTB_TEST_POSTGRES_DSN` 없이는 스킵되어 보이지 않았다.

## 검증

이전 worktree 검토에서 실행한 것:

- `alembic upgrade head`가 PG 18.6 / pgvector 0.8.6에서 `0011`까지 적용되고 다섯 테이블을 만든다.
- 공시 두 서비스의 테스트 35건이 모두 통과한다. 이전에 스킵됐던 DB 테스트 13건을 포함한다.
  lease, `FOR UPDATE SKIP LOCKED`, 활성 색인 교체, 정정본 dedupe, 모델 불일치 거부가
  실제 SQL에서 동작한다. `disclosures` 25행, `disclosure_indexes` 18행,
  `disclosure_chunks` 16행이 삽입됐다.
- 전체 스위트는 962 passed, 8 skipped, 1 failed다. 남은 스킵은 QuestDB뿐이고 실패는 위 표의
  마지막 항목이다.
- 아카이브 유실 시 재다운로드가 0회이고 `status='failed'`가 유지되며, 두 번째 실행에서
  `attempts`만 8에서 16으로 오른다.
- 당시 pgvector HNSW 차원 한계 측정은 현재 정확 검색 설계에 적용하지 않는다.

이번 통합에서 실행한 것:

- disclosure-indexer, disclosure-retrieval, portfolio-builder 관련 테스트: 241 passed, 72 skipped.
- 변경 서비스의 Ruff lint와 format 검사, `tach check`, `git diff --check`.
- `HALFVEC(2000)` SQLAlchemy 타입 import/compile 확인.
- SQLite API 테스트에서 PostgreSQL 전용 HNSW session 설정을 생략하도록 검색 경로 수정.

통합 전제에 필요한 다음 검증은 아직 남아 있다.

- PostgreSQL + pgvector에서 migration 0011 적용과 정확 검색의 실행 계획 확인.
- Docker 이미지 build 및 local/production Compose config 검증.
- 전체 저장소 Ruff 검사. 현재 untracked 사용자 노트북 `disclosure-colab-fixed.ipynb`의 Ruff 오류 때문에 전체 검사는 실패하며, 이 파일은 수정하지 않았다.
- 실제 OpenDART API와 실제 임베딩 서버를 사용한 수집·검색 검증.

실행할 수 없는 것:

- 실제 OpenDART API 호출. 수집 시간, 정기보고서 원문 파싱 결과, 재귀 깊이 한계는 실측하지 않는다.
- 실제 모델 서버 호출. 임베딩 지연, GPU 처리량, 검색 품질은 측정하지 않는다.
- Docker 이미지 build와 운영 compose 기동.

## 전제와 한계

`eval_harness.py`의 `QUERY_PREFIX` 실제 문자열을 아직 모른다.
`DISCLOSURE_EMBEDDING_QUERY_INSTRUCTION` 기본값은 빈 문자열로 두고, 학습 prefix를 넣지
않으면 측정된 품질이 나오지 않는다는 점을 설정 설명과 README에 적는다.

동결 평가 코퍼스 8,215청크를 만든 청킹 방식을 모른다. 현재 청커는 섹션 단위 2400바이트,
overlap 300바이트다. 두 방식이 다르면 측정된 nDCG@5 +0.0789가 운영에 그대로 전이된다고
주장할 수 없다. 운영 청커 기준 재측정이 필요하다.

서비스 1차 체크포인트는 파인튜닝된 Qwen3-Embedding-8B로 분리 설정한다. 2000차원 평가가
4096차원보다 약간 높아 검색 저장 차원은 2000으로 둔다. 별도 context-9b 실험은 선택을
보류한다. 2026-10-19 Drive 기록의 `context-v3-2048-block4096-speed-01` run은 epoch 0,
step 4에서 `empty focus or transformed block exceeds context budget`로 실패했다.
step 0 checkpoint와 `last.pt`는 저장돼 있으나, 재개·수정 run은 아직 기록되지 않았다.
후보 context 모델은 vLLM으로 서빙할 수 없고
transformers와 `trust_remote_code` 기반 래퍼가 필요하며, 질의와 문서의 인코딩 경로가
다르고 preview이며 한국어 지표가 없다. 1차 통합 대상은 Qwen3로 둔다.

서빙은 확정되지 않았다. RunPod과 vLLM은 양택이 아니다. RunPod은 GPU 호스팅이고 vLLM은 그
위에 올리는 추론 서버다. Qwen3-Embedding은 vLLM이 1급 지원하고 OpenAI 호환
`/v1/embeddings`를 그대로 제공한다. 사용 패턴이 아침에 집중되고 나머지 시간이 유휴이므로
파이프라인 창 동안만 Pod를 띄우는 운영이 Serverless 상시 대기나 Pod 24시간 가동보다
낫다. 코드는 `DISCLOSURE_EMBEDDING_BASE_URI`와 모델 이름만 알며 서빙 방식에 의존하지
않는다.

`.env`의 `POSTGRES_PASSWORD`와 `QUESTDB_PASSWORD`가 비어 있어 `docker compose`가 파일 전체
보간에서 실패한다. 다른 서비스의 `:?` 가드 때문이며 공시 작업과 무관한 기존 문제다. 코드
변경 대상이 아니고 환경 설정으로 해결한다.

## 범위 밖

- 모델 선택과 재학습, 평가셋 재구성.
- 무중단 모델 전환.
- 출처 URL, 접수번호, 인용 offset의 공개 계약 추가.
- PDF와 OCR 첨부 처리.
- 과거 시점 KOSPI200 구성원 복원.
- 하이브리드 검색과 재정렬.
- `ktb_core/embedding/config.py`와 뉴스 임베딩 경로 변경.

## 2026-10-20 정확 검색과 점수 진단

공시 서비스에서 HNSW 인덱스와 session 설정 및 500개 후보 제한을 제거했다. 필터를 적용한 전체 청크를 materialized CTE로 확정한 뒤 모든 거리를 계산하고 최종 top-k만 선택한다. 새 DB의 migration 0011은 ANN 인덱스를 생성하지 않는다. 기존 DB 적용과 실행 계획 검증은 아직 필요하다.

1024차원 best step 80의 저장된 dev 진단에서 무응답 14문항 중 9문항이 총 45개 청크를 반환했다. 임계값을 적용하지 않은 원래 지표이며, 그중 최고 점수는 약 0.5105다. 노트북의 마지막 결과 셀에는 cosine 점수 0.6 이상만 표시하는 필터를 추가했다.

같은 dev 결과에서 점수 0.6을 적용하면 답이 있는 138문항 중 125문항의 검색 결과가 모두 없어지고 Recall@5는 약 0.9155에서 0.0797로 떨어진다. 따라서 서비스 기본 임계값 변경은 사용자 적용 범위 확인 전까지 보류한다. 이 결과는 최종 평가셋 성능을 뜻하지 않는다.

2048차원 진단의 RuntimeError는 14.56GiB GPU에서 BF16 가중치를 로드하다 발생한 CUDA OOM이었다. 사용자가 A100으로 변경하기로 했으므로 이를 위한 노트북 코드 변경은 하지 않았다.
