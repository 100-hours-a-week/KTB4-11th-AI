# market-http ECS 배포

운영 ECS의 실행 설정은 [market-http.json](market-http.json), 배포는 [cd-ecs-v2.yaml](../../.github/workflows/cd-ecs-v2.yaml)에서 관리합니다.

## 배포 흐름

```text
dev push → CI Dev 성공·ECR 업로드 완료 → CD ECS V2 → market-http 배포
```

CD는 성공한 CI의 커밋을 checkout하고, 같은 커밋의 템플릿에 해당 SHA의 이미지 주소를 넣어 새 Task Definition 리비전을 등록합니다. 이후 서비스를 갱신하고 배포 ID를 기준으로 완료 여부를 확인합니다.

AWS 최신 리비전을 복사하거나 ECR 최신 이미지를 검색하지 않습니다.

## 실행 설정 변경

다음 설정은 `market-http.json`을 수정한 뒤 CD로 적용합니다.

- 실행 명령, CPU·메모리, 환경변수, 컨테이너 헬스체크
- 포트, 로그 설정, Task Role·Execution Role ARN
- SSM Parameter Store·Secrets Manager의 Secret 참조

앱 변경에 실행 설정 변경이 필요하면 같은 PR에서 함께 검토합니다. 비밀값은 직접 적지 않고 `secrets[].valueFrom`으로 참조합니다. 초기 템플릿은 AWS의 실행 설정을 옮긴 것이며, Secret 실제 값은 포함하지 않습니다.

## 수동 배포

GitHub Actions의 `CD ECS V2`에서 실행 브랜치로 `dev`를 지정합니다.

수동 배포도 해당 SHA의 dev push에 대한 `CI Dev`가 완료·성공한 경우에만 허용합니다. CI 기록이 없거나 진행 중·실패 상태이면 배포하지 않습니다.

자동·수동 배포 모두 대상 SHA가 현재 dev의 SHA와 일치하는지 확인합니다. dev가 이미 갱신됐다면 이전 SHA의 배포는 건너뜁니다.

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
