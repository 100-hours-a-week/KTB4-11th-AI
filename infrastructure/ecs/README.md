# market-http ECS 배포

운영 ECS의 실행 설정은 [market-http.json](market-http.json), 배포는 [cd-ecs-v2.yaml](../../.github/workflows/cd-ecs-v2.yaml)에서 관리합니다.

## 배포 흐름

```text
dev push → CI 검사·이미지 push → CD Dev 개발 EC2 배포·헬스체크 성공 → AI V2 ECS CD → market-http 배포
```

CD는 성공한 CI의 커밋을 checkout하고, 같은 커밋의 템플릿에 CI에서 기록한 이미지 digest 주소를 넣어 새 Task Definition 리비전을 등록합니다. 이후 서비스를 갱신하고 배포 ID를 기준으로 완료 여부를 확인합니다.

AWS 최신 리비전을 복사하거나 ECR 최신 이미지를 검색하지 않습니다.

## 실행 설정 변경

다음 설정은 `market-http.json`을 수정한 뒤 CD로 적용합니다.

- 실행 명령, CPU·메모리, 환경변수, 컨테이너 헬스체크
- 포트, 로그 설정, Task Role·Execution Role ARN
- SSM Parameter Store·Secrets Manager의 Secret 참조

앱 변경에 실행 설정 변경이 필요하면 같은 PR에서 함께 검토합니다. 비밀값은 직접 적지 않고 `secrets[].valueFrom`으로 참조합니다. 초기 템플릿은 AWS의 실행 설정을 옮긴 것이며, Secret 실제 값은 포함하지 않습니다.

## 수동 배포

GitHub Actions의 `AI V2 ECS CD`에서 실행 브랜치로 `dev`를 지정하고 `image_tag`에 40자리 커밋 SHA를 입력합니다.

해당 SHA의 dev push CI가 완료·성공했고 `ecs-release` artifact가 남아 있어야 합니다. 이 artifact의 SHA·저장소·digest를 검증해 당시 이미지를 배포합니다. 이전 릴리스도 지정할 수 있으므로 수동 롤백에는 최신 dev 검사를 적용하지 않습니다. artifact 보존 기간은 90일이며, 변경 전 릴리스처럼 기록이 없으면 수동 배포는 실패합니다.

자동 배포는 현재 dev와 SHA가 다르면 생략합니다. 개발·운영은 같은 digest를 사용하며 운영 배포 대기는 최대 35분입니다. 진행 중인 push CI·배포는 새 push로 취소하지 않습니다.

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
