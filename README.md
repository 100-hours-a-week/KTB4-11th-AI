# KTB4-11th-AI

척척개미단 AI 서비스 모노레포입니다. 뉴스를 수집·임베딩하고(news-preprocessor), 사건 단위로 묶고(news-clusterer), 요약과 지식 그래프를 만들며(news-graph-builder), 이를 바탕으로 포트폴리오를 구성합니다(portfolio-builder). 개발 명령과 구조는 [AGENTS.md](AGENTS.md)를 참고하세요.

## 환경 변수

서비스별 설정은 각 서비스의 접두사가 붙은 환경 변수로 읽습니다. "필수"가 아닌 값은 기본값이 있습니다.

### 공통 · 도구

| 변수 | 필수 | 기본값 | 설명 |
|---|---|---|---|
| `KTB_POSTGRES_DSN` | 마이그레이션 시 | | `alembic upgrade`가 사용하는 DSN |
| `KTB_TEST_POSTGRES_DSN` | | | DB 테스트용 DSN. 없으면 해당 테스트를 건너뜀. 테스트가 테이블을 비우므로 `news`가 아닌 `news_test`를 가리킬 것 |
| `KTB_EMBEDDING_BASE_URI` | news-preprocessor | | OpenAI 호환 임베딩 서버 주소 (`/v1` 포함) |
| `KTB_EMBEDDING_MODEL` | | `mlx-community/Qwen3-Embedding-4B-4bit-DWQ` | 임베딩 모델 |
| `KTB_EMBEDDING_DIMENSIONS` | | `2000` | DB 컬럼 `vector(2000)`과 같아야 함 |
| `KTB_EMBEDDING_MAX_TOKENS` | | `16384` | 임베딩 입력 최대 토큰 |

### news-preprocessor (`NEWS_PREPROCESSOR_`)

| 변수 | 필수 | 기본값 |
|---|---|---|
| `NEWS_PREPROCESSOR_POSTGRES_DSN` | ✓ | |
| `NEWS_PREPROCESSOR_EMBED_BATCH_LIMIT` | | `100` |
| `NEWS_PREPROCESSOR_USER_AGENT` | | `ktb-ai/0.1` |
| `NEWS_PREPROCESSOR_LOG_LEVEL` | | `INFO` |

### news-clusterer (`NEWS_CLUSTERER_`)

| 변수 | 필수 | 기본값 |
|---|---|---|
| `NEWS_CLUSTERER_POSTGRES_DSN` | ✓ | |
| `NEWS_CLUSTERER_EPS` | | `0.2` (코사인 거리, 0 초과 2 이하) |
| `NEWS_CLUSTERER_MIN_SAMPLES` | | `3` |
| `NEWS_CLUSTERER_LOG_LEVEL` | | `INFO` |

### news-graph-builder (`NEWS_GRAPH_BUILDER_`)

| 변수 | 필수 | 기본값 | 설명 |
|---|---|---|---|
| `NEWS_GRAPH_BUILDER_POSTGRES_DSN` | ✓ | | |
| `NEWS_GRAPH_BUILDER_LOG_LEVEL` | | `INFO` | |
| `NEWS_GRAPH_BUILDER_LLM_BASE_URI` | ✓ | | OpenAI 호환 LLM 서버 주소 (`/v1` 포함) |
| `NEWS_GRAPH_BUILDER_LLM_MODEL` | ✓ | | |
| `NEWS_GRAPH_BUILDER_SUMMARY_MAX_CHARS` | | `24000` | LLM에 넣는 기사 본문 글자 수 상한 |
| `NEWS_GRAPH_BUILDER_LLM_TIMEOUT` | | `120` | 초 |
| `NEWS_GRAPH_BUILDER_MAX_ENTITIES` | | `30` | 클러스터당 개체 수 상한 |
| `NEWS_GRAPH_BUILDER_MAX_RELATIONS` | | `50` | 클러스터당 관계 수 상한 |
| `NEWS_GRAPH_BUILDER_KIWOOM_APP_KEY` | ✓ | | 키움 REST API 앱 키 |
| `NEWS_GRAPH_BUILDER_KIWOOM_SECRET_KEY` | ✓ | | 키움 REST API 시크릿 키 |
| `NEWS_GRAPH_BUILDER_KIWOOM_BASE_URI` | | `https://api.kiwoom.com` | 모의투자 도메인으로 바꿀 수 있음 |
| `NEWS_GRAPH_BUILDER_KIWOOM_REQUEST_INTERVAL` | | `0.2` | 키움 요청 사이 대기 시간(초) |
| `NEWS_GRAPH_BUILDER_DART_API_KEY` | ✓ | | OpenDART API 키. `compose.dev.yaml`은 `.env`의 `OPENDART_API_KEY`에서 채움 |

키움 키는 주문이 가능한 키이므로 모의투자 키나 전용 계정을 권장하고, 운영 태스크의 outbound IP를 키움에 등록해야 합니다. 키는 커밋하지 말고, 명령줄에 직접 입력하는 대신 파일에서 export 하세요.

### portfolio-builder (`PORTFOLIO_BUILDER_`)

| 변수 | 필수 | 기본값 |
|---|---|---|
| `PORTFOLIO_BUILDER_POSTGRES_DSN` | ✓ | |
| `PORTFOLIO_BUILDER_QUESTDB_DSN` | ✓ | |
| `PORTFOLIO_BUILDER_NEWS_CLUSTERER_URL` | ✓ | |
| `PORTFOLIO_BUILDER_LOG_LEVEL` | | `INFO` |
