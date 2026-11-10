# 경쟁 포트폴리오 워커 배포

`competition-portfolio-builder`는 SQS 요청을 계속 소비하고 포트폴리오를 생성한다. 개발·운영 CD가 새 이미지로 교체하며, Compose의 `competition` 프로필에 포함된다. 공통 포트폴리오 배치의 `PORTFOLIO_BUILDER_COMPETITION_ENABLED`와 워커 실행 여부는 별개다. 현재 경쟁 포트폴리오는 생성·저장까지 수행하며 참가자별 주문은 실행하지 않는다.

## 환경변수 관리

로컬 Compose는 저장소 루트 `.env`를 읽는다. 서버에서는 CD가 GitHub Repository Variables의 `DEV_AI_ENV` 또는 `PROD_AI_ENV`와 서울 리전 Parameter Store 값을 합쳐 배포 디렉터리의 `.env`를 생성한다. 별도의 `ai.env`는 사용하지 않는다. 서버 `.env`만 수정하면 다음 배포에서 변경이 사라지므로, 영구 설정은 해당 Variable과 Parameter Store에 반영한다.

경쟁 워커에는 기존 builder의 DB·시장·뉴스·LLM 설정과 아래 값이 필요하다. 전체 목록은 `.env.example`을 따른다.

| 설정 | 값 |
| --- | --- |
| `PORTFOLIO_BUILDER_COMPETITION_QUEUE_URL` | 개발은 `stockspoon-v2-dev-ai-request`, 운영은 `stockspoon-v2-prod-ai-request`의 전체 SQS URL |
| `PORTFOLIO_BUILDER_COMPETITION_JEV_CHOICE_MIN_CONFIDENCE` | 명시적으로 지정, 예: `0.5` |
| `PORTFOLIO_BUILDER_COMPETITION_JEV_API_KEY` | 별도 Jev 자격증명을 쓸 때만 설정. 생략하면 `PORTFOLIO_BUILDER_LLM_API_KEY` 사용 |
| `DISCLOSURE_RETRIEVAL_TIMEOUT` | Compose 기본값 `150`초, 허용 범위는 0 초과 180 이하 |

Jev는 설정된 HTTP API를 호출한다. OpenRouter 기본 주소를 사용한다면 유효한 OpenRouter API 키를 넣는다. 잘못된 전용 키를 설정하면 builder 키로 자동 재시도하지 않는다.

Modal 공시 검색에는 `DISCLOSURE_ENABLED=true`, `DISCLOSURE_INFERENCE_BACKEND=modal`, `DISCLOSURE_EMBEDDING_DIMENSIONS=1024`, `DISCLOSURE_EMBEDDING_MAX_TOKENS=16384`와 Modal document/query/rerank URL 및 proxy key/secret을 설정한다. document URL은 공시 인덱서에서 사용한다. 공시 수집·인덱싱 배치도 별도로 정상 동작해야 검색할 자료가 생긴다.

GitHub Variable에 비밀값을 직접 넣지 않는다. 예를 들어 개발용 Variable에는 `DISCLOSURE_MODAL_KEY=ssm:///stockspoon/ai/dev/DISCLOSURE_MODAL_KEY`를 넣고, Parameter Store의 같은 경로에 실제 Modal proxy key를 SecureString으로 저장한다. secret과 LLM/Jev 키도 같은 방식으로 관리한다. 운영은 경로의 `dev`를 `prod`로 바꾼다. `ssm://`는 CD가 해석하는 참조이므로, 로컬 실행용 `.env`에는 실제 값을 넣어야 한다. `.env`와 비밀값은 커밋하지 않는다.

## 배포와 복구

CD는 `news-http`를 먼저 갱신하고 새 이미지 안에서 builder·경쟁 요청·활성화된 공시 검색 설정을 검증한다. 이 검증은 외부 API 호출이나 큐 소비를 하지 않는다. 성공한 뒤 기존 경쟁 워커를 정지·삭제하고 새 워커를 실행한다. 따라서 설정 검증 통과가 Jev·Modal·SQS의 실제 연결 성공을 보장하지는 않는다.

실패하면 이전 Compose·환경변수·이미지 파일을 복구한다. 워커 교체를 시작한 경우에만 이전 워커 상태도 복원한다. 이전에 실행 중이었으면 재실행하고, 정지 상태였으면 정지 컨테이너를 만들며, 없었거나 이전 릴리스에 서비스 정의가 없었으면 새 워커를 제거한다. 다른 Compose 프로젝트의 워커는 건드리지 않는다. 워커 복구도 실패하면 별도 오류를 출력하고 원래 배포 실패 코드를 유지한다.

EC2에는 Docker Compose, Python 3, AWS CLI와 해당 환경의 SSM 복호화·SQS 소비 권한이 필요하다. 기존 SQS 요청을 처리하던 워커는 배포 중 정지될 수 있으므로, 재전달 처리는 기존 애플리케이션의 멱등성에 따른다.
