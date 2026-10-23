# market-http ECS 배포

운영 ECS의 실행 설정은 [market-http.json](market-http.json), 배포는 [cd-ecs-v2.yaml](../../.github/workflows/cd-ecs-v2.yaml)에서 관리합니다.

자동 배포와 수동 재배포를 모두 지원합니다. main 변경은 CI와 운영 CD 성공 후 자동 배포되고, 환경변수의 외부 저장값만 변경한 경우에는 `AI V2 ECS CD`를 수동 실행해 기존 이미지를 다시 배포할 수 있습니다. 수동 실행은 CI나 이미지 빌드를 다시 실행하지 않습니다.

## 배포 흐름

```text
main push → CI Main 테스트·이미지 push·digest artifact 생성 → CD 운영 DB 마이그레이션·EC2 배포 성공 → AI V2 ECS CD → market-http 배포
```

`CI Main` 성공 후 `CD`가 운영 DB 마이그레이션과 EC2 배포를 수행합니다. 모든 단계가 성공한 뒤 `gh workflow run --ref main`으로 `AI V2 ECS CD`를 호출합니다. ECS CD는 CI나 운영 CD의 내부 job이 아닌 별도 Actions 실행으로 표시됩니다. 마이그레이션이 실패하면 ECS CD를 호출하지 않습니다.

dev 변경은 개발 EC2만 배포합니다. 운영 ECS는 운영 DB를 사용하므로 개발 DB 마이그레이션이 끝났다는 이유로 dev 이미지를 운영 ECS에 배포하지 않습니다.

자동 호출은 운영 DB 마이그레이션에 사용한 커밋 SHA와 CI 실행 ID를 입력으로 전달합니다. ECS CD는 해당 main CI의 성공 여부와 `ecs-release` artifact를 검증합니다. 호출한 운영 CD는 ECS CD 완료를 기다리지 않습니다. 성공한 CI의 커밋을 checkout하고, 같은 커밋의 템플릿에 CI에서 기록한 이미지 digest 주소를 넣어 새 Task Definition 리비전을 등록합니다. 이후 서비스를 갱신하고 배포 ID를 기준으로 완료 여부를 확인합니다.

AWS 최신 리비전을 복사하거나 ECR 최신 이미지를 검색하지 않습니다.

## 실행 설정 변경

다음 설정은 `market-http.json`을 수정한 뒤 CD로 적용합니다.

- 실행 명령, CPU·메모리, 컨테이너 헬스체크
- 포트, 로그 설정, Task Role·Execution Role ARN

앱 변경에 실행 설정 변경이 필요하면 같은 PR에서 함께 검토합니다.

## 환경변수 관리

운영 ECS의 일반 환경변수는 Task Definition에서 분리해 Repository Variable `MARKET_HTTP_ENV_JSON` 하나로 관리합니다. GitHub의 Settings → Secrets and variables → Actions → Variables에서 등록합니다. Variable은 저장된 내용을 조회하고 편집할 수 있습니다. 운영 EC2의 `PROD_AI_ENV`와 개발 Compose 설정은 별도로 유지합니다.

[market-http.env.example.json](market-http.env.example.json)의 내용을 복사해 필요한 값을 수정한 뒤 Variable에 저장합니다. 예시에는 기존 Task Definition에 있던 일반 설정이 들어 있고 DB 비밀번호는 없습니다. 이 예시 파일은 CD에서 읽지 않습니다. 모든 값은 문자열이며, 목록 설정은 `"[\"*\"]"`처럼 JSON 배열을 담은 문자열로 작성합니다.

CD가 매번 Variable을 읽어 컨테이너의 `environment`를 구성합니다. Variable이 없거나 JSON 형식·값의 타입이 잘못되면 새 리비전을 등록하기 전에 실패합니다. Variable을 수정한 뒤 ECS CD만 다시 실행하면 기존 이미지를 새 설정으로 배포할 수 있습니다. Variable 변경 자체는 자동 배포를 시작하지 않습니다. `MARKET_HTTP_PORT`는 태스크의 포트 매핑과 헬스체크에 맞춰 `8000`을 사용합니다.

DB 접속정보는 기존 AWS SSM Parameter Store에 그대로 둡니다. 기존 Repository Variable `MARKET_HTTP_POSTGRES_DSN_SECRET_ARN`에는 값을 가져올 AWS 참조 ARN만 저장하며, ECS가 Execution Role로 값을 조회해 `MARKET_HTTP_POSTGRES_DSN`에 주입합니다. Execution Role에는 해당 값을 읽을 권한이 필요합니다. `MARKET_HTTP_ENV_JSON`에 DB 접속정보를 넣으면 CD가 실패합니다. 참조 대상의 저장값을 변경한 경우에도 ECS CD 재실행이나 서비스의 Force new deployment로 새 태스크를 시작해야 합니다.

Valkey 연결 풀 상한은 `MARKET_HTTP_VALKEY_MAX_CONNECTIONS`로 설정합니다. 양의 정수만 허용하며, 미설정 시 redis-py의 기본 연결 풀 동작을 유지합니다. 부하 테스트용 상한도 `MARKET_HTTP_ENV_JSON`에서 수정하고 ECS CD를 다시 실행합니다. 연결이 상한에 도달하면 기본 `ConnectionPool`은 `MaxConnectionsError`를 반환하므로 SSE 클라이언트는 연결을 확보하지 못합니다.

## 수동 배포

GitHub Actions → `AI V2 ECS CD` → `Run workflow`에서 실행 브랜치로 `main`을 지정합니다. `image_tag`를 비우면 현재 main 커밋을 사용하고, 특정 릴리스 재배포나 롤백에는 40자리 main 커밋 SHA를 입력합니다.

`ci_run_id`는 자동 호출에서만 전달하므로 수동 실행에서는 비워 둡니다. 이미 실행된 ECS CD의 `Re-run all jobs`를 사용해 같은 커밋으로 다시 배포할 수도 있습니다. 자동 호출된 실행을 재실행할 때는 기존의 최신 main 검사도 적용됩니다.

해당 SHA의 main push CI가 완료·성공했고 `ecs-release` artifact가 남아 있어야 합니다. 이 artifact의 SHA·저장소·digest를 검증해 당시 이미지를 배포합니다. 이전 main 릴리스도 지정할 수 있으므로 수동 롤백에는 최신 main 검사를 적용하지 않습니다. artifact 보존 기간은 90일이며, 변경 전 릴리스처럼 기록이 없으면 수동 배포는 실패합니다. 수동 실행은 EC2 배포나 DB 마이그레이션을 다시 실행하지 않습니다. 환경변수만 다시 적용할 때는 운영 CD와 ECS 배포가 완료된 릴리스의 SHA를 사용합니다. 최신 main CI가 성공했다는 사실만으로 운영 DB 마이그레이션 완료가 보장되지는 않습니다.

`MARKET_HTTP_ENV_JSON`, GitHub Variable로 지정한 AWS 참조, AWS에 저장된 값만 바꾼 경우에도 이 방법으로 기존 이미지를 다시 배포할 수 있습니다. Task Definition JSON을 수정했다면 변경된 JSON이 포함된 커밋을 배포해야 합니다. GitHub Variable·Secret이나 AWS 저장값의 변경 자체는 이 워크플로를 시작하지 않습니다. 최초로 환경변수 분리를 적용할 때는 이 변경을 main에 반영해 운영 DB 마이그레이션과 자동 배포를 완료하고, 이후 설정 변경부터 ECS CD 재실행을 사용합니다.

자동 ECS 배포는 main CI와 운영 CD가 성공한 릴리스를 사용하며, 현재 main과 SHA가 다르면 생략합니다. ECS는 해당 main CI에서 기록한 이미지 digest를 사용하며 운영 배포 대기는 최대 35분입니다. dev push는 운영 ECS를 배포하지 않습니다.

운영 인증은 기존 OIDC Role을 유지합니다. `production` Environment 승인 도입은 AWS Role 신뢰 정책의 OIDC subject를 함께 변경한 뒤 적용해야 합니다.

## Cloud와의 역할 분담

| 관리 주체 | 담당 설정 |
|---|---|
| 앱/CD | Task Definition 실행 설정, 이미지 SHA, 리비전 등록·배포·검증 |
| Cloud | ECS 클러스터·서비스, 타깃 그룹, IAM 정책, 보안 그룹·네트워크 |

포트, Role ARN, DB·캐시 주소, Secret 경로처럼 인프라와 연결되는 값은 Cloud와 협의해 변경합니다.

```text
Cloud 리소스 준비 → 앱 템플릿 변경·배포 → 이전 리소스 정리
```

Cloud의 Task Definition은 최초 서비스 생성용으로만 유지됩니다. 이후 실행 설정은 앱 템플릿이 기준이며, 최초 생성용 Terraform 설정을 수정해도 기존 앱 설정에 반영되지 않습니다.
