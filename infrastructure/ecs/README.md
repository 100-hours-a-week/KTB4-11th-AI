# market-http ECS 배포

운영 ECS의 실행 설정은 [market-http.json](market-http.json), 배포는 [cd-ecs-v2.yaml](../../.github/workflows/cd-ecs-v2.yaml)에서 관리합니다.

## 배포 흐름

```text
main push → CI Main 테스트·이미지 push·digest artifact 생성 → CD Main 운영 DB 마이그레이션·EC2 배포 성공 → AI V2 ECS CD → market-http 배포
```

CD는 성공한 CI의 커밋을 checkout하고, 같은 커밋의 템플릿에 CI에서 기록한 이미지 digest 주소를 넣어 새 Task Definition 리비전을 등록합니다. 이후 서비스를 갱신하고 배포 ID를 기준으로 완료 여부를 확인합니다.

AWS 최신 리비전을 복사하거나 ECR 최신 이미지를 검색하지 않습니다.

## 실행 설정 변경

다음 설정은 `market-http.json`을 수정한 뒤 CD로 적용합니다.

- 실행 명령, CPU·메모리, 환경변수, 컨테이너 헬스체크
- 포트, 로그 설정, Task Role·Execution Role ARN
- SSM Parameter Store·Secrets Manager의 Secret 참조

앱 변경에 실행 설정 변경이 필요하면 같은 PR에서 함께 검토합니다. 비밀값은 직접 적지 않고 `secrets[].valueFrom`으로 참조합니다. 초기 템플릿은 AWS의 실행 설정을 옮긴 것이며, Secret 실제 값은 포함하지 않습니다.

GitHub Actions Repository Secret `MARKET_HTTP_POSTGRES_DSN_SECRET_ARN`에는 PostgreSQL DSN이 저장된 AWS Secrets Manager Secret의 ARN을 설정합니다. DSN 자체는 AWS Secrets Manager에만 저장하고, ECS Task Execution Role에 해당 Secret의 읽기 권한을 부여합니다.

Valkey 연결 풀 상한은 `MARKET_HTTP_VALKEY_MAX_CONNECTIONS`로 설정합니다. 양의 정수만 허용하며, 미설정 시 redis-py의 기본 연결 풀 동작을 유지합니다. ECS에서 부하 테스트할 때는 `market-http.json`의 `environment`에 이 값을 추가하고 테스트가 끝나면 제거합니다. 연결이 상한에 도달하면 기본 `ConnectionPool`은 `MaxConnectionsError`를 반환하므로 SSE 클라이언트는 연결을 확보하지 못합니다.

## 수동 배포

GitHub Actions의 `AI V2 ECS CD`에서 실행 브랜치로 `main`을 지정하고 `image_tag`에 40자리 main 커밋 SHA를 입력합니다.

해당 SHA의 main push CI가 완료·성공했고 `ecs-release` artifact가 남아 있어야 합니다. 이 artifact의 SHA·저장소·digest를 검증해 당시 이미지를 배포합니다. 이전 main 릴리스도 지정할 수 있으므로 수동 롤백에는 최신 main 검사를 적용하지 않습니다. artifact 보존 기간은 90일이며, 변경 전 릴리스처럼 기록이 없으면 수동 배포는 실패합니다.

자동 운영 ECS 배포는 CD Main이 운영 PostgreSQL 마이그레이션까지 성공한 후에만 시작하며, 현재 main과 SHA가 다르면 생략합니다. ECS는 main CI에서 기록한 이미지 digest를 사용하며 운영 배포 대기는 최대 35분입니다. dev push는 운영 ECS를 배포하지 않습니다.

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
