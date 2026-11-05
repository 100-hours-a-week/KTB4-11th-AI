# Archify 사이트 배포

로컬에서 생성한 `_site/`를 ORAS로 GHCR에 업로드하고
`.github/workflows/deploy-archify.yml`을 수동 실행한다.
워크플로는 OCI manifest digest로 지정한 결과물을 내려받아 검증하고 GitHub Pages에 배포한다.
GitHub Release나 사이트 결과물의 커밋은 필요하지 않다.

## 최초 설정

- 로컬에 [ORAS CLI](https://oras.land/docs/installation/) 1.3.6과 GitHub CLI를 설치한다.
- GitHub CLI는 저장소의 Actions 실행 권한이 있는 계정으로 로그인한다.
- GHCR 업로드용 PAT classic에 `write:packages` 권한을 부여한다.
  조직이 SSO를 사용하면 해당 조직에 토큰을 승인한다.
- GitHub 사용자 이름과 토큰을 로컬 환경의 `GHCR_USER`, `GHCR_TOKEN`으로 준비한다.
  토큰 값을 명령 인수, 저장소 파일, 로그에 기록하지 않는다.

```bash
printf '%s' "$GHCR_TOKEN" | oras login ghcr.io --username "$GHCR_USER" --password-stdin
```

패키지는 `ghcr.io/100-hours-a-week/archify-site`를 사용한다.
첫 업로드 후 조직의 Packages에서 패키지 설정을 확인한다.

1. 패키지 공개 범위는 기본값인 **Private**로 유지한다.
2. **Connect repository**로 소스 저장소를 연결하지 않는다.
3. **Manage Actions access → Add repository**에서 `KTB4-11th-AI`에 **Read** 권한을 부여한다.
   이 설정은 소스 저장소 연결과 별개이며, 배포 job은 `GITHUB_TOKEN`과 `packages: read`로 읽는다.

업로드 시 `org.opencontainers.image.source`로 저장소를 지정하지 않는다.
이 방식은 패키지를 소스 Repo의 Packages에 연결하지 않지만,
조직의 Packages 목록에는 권한 있는 사용자가 볼 수 있는 항목이 생성된다.
조직의 패키지 생성 정책이 허용되어 있어야 한다.

GitHub Pages의 Source는 **GitHub Actions**여야 한다.
`github-pages` environment는 수동 실행할 브랜치(아래 예시에서는 `main`)의 배포를 허용해야 한다.
워크플로가 기본 브랜치에 반영된 뒤 수동 실행할 수 있다.
`dev` 병합만으로 기본 브랜치의 워크플로가 갱신되지는 않는다.

## 업로드

Archify로 생성·검증한 `_site/index.html`, `_site/architecture.json`이 있는 상태에서
저장소 루트에서 실행한다. 압축 루트에 두 파일이 위치해야 하며,
사이트가 참조하는 추가 파일도 함께 포함한다. 링크는 허용하지 않는다.

```bash
ARCHIFY_PACKAGE=ghcr.io/100-hours-a-week/archify-site
ARCHIFY_TAG="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIFY_UPLOAD_DIR="$(mktemp -d)"
tar -czf "$ARCHIFY_UPLOAD_DIR/archify-site.tar.gz" -C _site .
(
  cd "$ARCHIFY_UPLOAD_DIR"
  oras push "$ARCHIFY_PACKAGE:$ARCHIFY_TAG" \
    --artifact-type application/vnd.archify.site.v1 \
    archify-site.tar.gz:application/gzip
)
ARCHIFY_DIGEST="$(oras resolve "$ARCHIFY_PACKAGE:$ARCHIFY_TAG")"
```

`ARCHIFY_DIGEST`는 `sha256:` 뒤에 64자리 소문자 16진수가 오는 **OCI manifest digest**다.
`sha256sum archify-site.tar.gz`로 계산한 파일 해시와 다르다.
태그는 업로드 이력을 구분하고, 배포는 태그 대신 digest로 고정한다.

## 배포

업로드와 최초 권한 설정을 마친 뒤 실행한다.

```bash
gh workflow run deploy-archify.yml \
  --repo 100-hours-a-week/KTB4-11th-AI \
  --ref main \
  -f digest="$ARCHIFY_DIGEST"
```

GitHub Actions UI의 **Deploy Archify → Run workflow**에서 브랜치와 digest를 입력해도 된다.
워크플로는 다운로드 실패, 압축 누락·손상, 경로 이탈·링크,
필수 파일 누락·빈 파일 또는 잘못된 JSON을 배포 전에 차단한다.
진행 중인 배포는 취소하지 않으며 `archify-pages-deploy` 그룹으로 동시 배포를 막는다.
GitHub concurrency는 실행 중 하나와 대기 중 하나를 유지하므로 연속 요청 시 이전 대기 요청은 대체될 수 있다.

과거 결과물로 되돌리려면 보관 중인 해당 manifest digest로 다시 실행한다.
패키지 버전을 삭제하면 그 digest를 이용한 재배포는 불가능하다.

## 참고

- [ORAS와 GHCR](https://oras.land/docs/compatible_oci_registries/)
- [GitHub 패키지와 저장소 연결](https://docs.github.com/en/packages/learn-github-packages/connecting-a-repository-to-a-package)
- [패키지의 Actions 접근 권한](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility#ensuring-workflow-access-to-your-package)
