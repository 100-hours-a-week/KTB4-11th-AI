# Architecture Pages 운영

이슈 [#442](https://github.com/100-hours-a-week/KTB4-11th-AI/issues/442)의 독립 워크플로는
`main` push 또는 `main`에서의 수동 실행으로 동작한다. 기존 CI Main/CD에 의존하거나
그 결과를 변경하지 않는다. 기본 브랜치 `dev`에 병합한 뒤 **워크플로를
`main`에도 반영해야** push 자동 실행이 시작된다.

## 관리자 사전 설정

1. Tailscale 관리 콘솔에서 GitHub OIDC용 federated identity를 만든다.
   issuer는 `https://token.actions.githubusercontent.com`으로 지정하고 `auth_keys` 쓰기 권한과
   `tag:archify-ci`만 부여한다. 전용 audience와 client ID를 아래 Secrets에 등록한다.
2. 신뢰 조건을 repository `100-hours-a-week/KTB4-11th-AI`, ref `refs/heads/main`,
   workflow `100-hours-a-week/KTB4-11th-AI/.github/workflows/archify-pages.yml@refs/heads/main`으로
   제한한다. GitHub 기본 subject `repo:100-hours-a-week/KTB4-11th-AI:ref:refs/heads/main`을
   사용하고 `workflow_ref` claim도 확인한다. `generate` job에 environment를 추가하면 subject가
   달라지므로 federation 조건도 함께 검토해야 한다. 외부 PR·다른 ref·다른 workflow는 거부한다.
3. LLM 서버에 별도 `tag:archify-llm`을 부여하고 아래처럼 필요한 TCP 포트만 허용한다.
   예시는 Tailscale Serve HTTPS 443 기준이며 실제 서버 포트로 맞춘다.

   ```json
   {
     "tagOwners": {
       "tag:archify-ci": ["autogroup:admin"],
       "tag:archify-llm": ["autogroup:admin"]
     },
     "grants": [
       {"src": ["tag:archify-ci"], "dst": ["tag:archify-llm"], "ip": ["tcp:443"]}
     ],
     "tests": [
       {
         "src": "tag:archify-ci",
         "accept": ["tag:archify-llm:443"],
         "deny": ["tag:archify-llm:22", "tag:archify-llm:8000"]
       }
     ]
   }
   ```

   기존 정책에 병합하고 실제 다른 Tailnet 장치/포트도 deny 테스트에 추가한다.
   Grants/ACL은 허용 규칙을 합산하므로 `*` → `*` 같은 기존 광범위 허용을 반드시 제거하거나
   범위를 줄인다. 위 규칙만 추가해서는 격리되지 않는다. CI 태그를 SSH·subnet router·exit node
   접근 규칙에 포함하지 않는다. Runner는 `--accept-routes=false`로 연결한다.
4. 서버가 loopback에만 바인딩되었다면 Tailscale Serve로 Tailnet 내부에만 노출한다.
   Funnel이나 공개 포트는 사용하지 않는다. `/v1/models`, `/v1/chat/completions`를 지원해야 한다.
   사용자 지정 health 경로는 제공하지 않으므로 서버가 이 계약을 지원하는지 먼저 확인한다.
5. Repository Settings → Pages → Source를 **GitHub Actions**로 선택한다. 조직의 공개 Pages
   정책을 확인하고 `github-pages` environment의 배포 허용 브랜치를 `main`으로 제한한다.
   공개 사이트에는 모델 ID와 아래 빌드 정보도 게시되므로 공개 가능한 모델 별칭을 사용한다.

| GitHub 설정 | 이름 | 값 |
| --- | --- | --- |
| Secret | `TS_OAUTH_CLIENT_ID` | federated identity의 client ID |
| Secret | `TS_AUDIENCE` | federated identity에 설정한 audience |
| Secret (선택) | `LLM_API_KEY` | 서버가 요구하는 API 토큰; 무인증 서버는 생략 |
| Variable | `LLM_BASE_URL` | `http://100.x.y.z:포트/v1` 또는 `https://서버.테일넷.ts.net/v1` |
| Variable | `LLM_MODEL` | `/v1/models`가 반환하는 실제 모델 ID |
| Variable | `LLM_CONTEXT` | 서버에서 실제 허용한 context token 수 |
| Variable | `LLM_OUTPUT` | 실제 최대 output token 수; context보다 작아야 함 |

MagicDNS의 짧은 이름 대신 `.ts.net` FQDN이나 Tailnet IPv4 주소를 사용한다. 공개 API 주소,
URL 내 인증정보, query/fragment, redirect는 허용하지 않는다. 모델은 OpenAI-compatible
tool calling을 지원해야 한다. `/v1/models` 성공만으로 tool calling과 context 지원이 입증되지는
않으므로 서버에서 읽기·파일 작성 tool call과 충분한 context를 먼저 검증한다.
서버 동시성은 이 워크플로 기준 1개이며 다른 클라이언트의 추론 부하도 함께 고려한다.

## 실행과 산출물

생성·검증 Python 코드는 워크플로의 `shell: python` / `run` 블록 안에서 관리한다.
별도 실행 스크립트나 애플리케이션 의존성을 추가하지 않는다.

- OpenCode `1.19.5`, Archify commit `f325d86230553e1dd15d3464324f1fcd01f02352`를 고정한다.
  Archify 패키지를 `.opencode/skills/archify`에 복사하는 지원 디렉터리 설치 방식을 사용한다.
  패키지는 자체 renderer/validator를 포함하므로 별도 npm install이 필요 없다.
- Runner가 추적 중인 서비스/공유 패키지 소스, Compose, infrastructure 및 workflows의
  Python/TOML/YAML/SQL만 임시 사본으로 제공한다. `.env`, Git 인증정보, 프로젝트 지침,
  테스트/fixture, symlink, 실제 환경 설정은 제공하지 않는다. 사본의 코드/설정은 개인 LLM
  서버로 전송되므로 저장소 접근 정책에 맞는 서버만 지정한다.
- OpenCode에는 `local` provider만 허용한다. 기본 모델과 작은 모델을 같은 서버로 고정하며
  유료 API fallback, share, 플러그인, 셸·웹·하위 agent 실행은 비활성화한다. 읽기와 JSON 후보
  작성만 허용한다. agent 프로세스에는 GitHub/OIDC/Tailscale 자격증명을 전달하지 않는다.
- LLM이 만든 `architecture.json`을 Runner에서 `archify finalize architecture ... --repo-root ...
  --quality showcase --json`으로 검증한다. JSON/schema, 저장소 근거, 렌더링, strict artifact
  검사와 실제 Chrome browser-check가 통과해야 배포한다. Ubuntu hosted runner의 Chrome을
  사용하며 브라우저가 없거나 gate가 실패하면 우회하지 않고 실패한다.
- `index.html`, `architecture.json`, `build.json`, `downloads.html`만 `_site/`에서 배포한다.
  `build.json`은 source SHA, 모델 ID, token 한도, 도구 버전을 기록한다. HTML은 독립 실행형이며
  다운로드 페이지의 링크는 상대 경로이므로 Project Pages 서브패스를 보존한다.
- 동일 파일을 일반 Actions Artifact로 **14일** 보관한다. HTML/JSON 생성물은 Git에 커밋하지
  않는다. 비공개 세션 로그, 렌더러 중간 파일, source 사본은 artifact에 포함하지 않는다.
- API health는 15초 timeout으로 최대 3회(간격 5초), 모델 생성은 20분, finalize는 10분으로
  제한한다. 생성 job 전체는 40분이다. 실패하면 업로드/배포하지 않고 기존 Pages를 유지한다.
  Tailscale 액션의 post cleanup이 임시 장치를 logout한다. 강제 runner 종료 시 정리 상태는
  Tailscale 관리 콘솔에서 확인한다.
- concurrency는 이전 실행을 취소하고 deploy job은 최신 `main` SHA도 검사한다. 수동 재실행한
  과거 SHA는 배포할 수 없다. 배포 진행 중의 새 push는 다음 실행에서 최신 산출물로 교체된다.

공개 주소:

- [아키텍처](https://100-hours-a-week.github.io/KTB4-11th-AI/)
- [JSON 원본](https://100-hours-a-week.github.io/KTB4-11th-AI/architecture.json)
- [HTML/JSON 다운로드](https://100-hours-a-week.github.io/KTB4-11th-AI/downloads.html)
- [생성 버전 정보](https://100-hours-a-week.github.io/KTB4-11th-AI/build.json)

## 최초 배포 검증 및 장애 대응

1. Secrets/Variables, OIDC 신뢰 조건, grants와 Pages 설정을 마친 뒤 `main`에서 수동 실행한다.
   이 저장소 변경만으로 관리자 설정이나 실제 서버 접속이 완료되지는 않는다.
2. 정상 실행에서 Pages 네 주소와 Actions Artifact를 확인한다. HTML을 다운로드하여 오프라인으로
   열고 `/KTB4-11th-AI/` 서브패스에서도 탐색·다운로드가 동작하는지 확인한다.
3. `build.json`의 SHA가 최신 `main`인지 확인한다. 구성요소와 관계의 근거를 실제 코드와 대조한다.
   모델의 의미적 정확성은 validator만으로 보장되지 않는다.
4. 공개 전 소스의 민감 값과 생성물의 공개 적합성을 점검한다. 워크플로는 API key/endpoint,
   Tailnet 이름, private IPv4, AWS access key, private key 표식을 탐지하면 실패하지만 모든
   종류의 기밀정보를 자동 판정하지는 못한다. 실제 비밀값은 추적 소스에 두지 않는다.
5. Tailscale 정책 테스트와 CI 태그 장치에서 LLM 허용 포트 연결 성공, SSH/다른 장치 포트 연결
   실패를 확인한다. 기존의 더 넓은 grants/ACL이 없는지도 확인한다.
6. 서버 정지, 없는 모델 ID, 잘못된 JSON으로 각각 실패시키고 기존 Pages SHA가 유지되는지
   확인한다. feature branch의 workflow_dispatch는 generate job이 skip되는지도 확인한다.
7. 실패 시 설정과 LLM 서버 상태를 먼저 확인한다. 로그는 서버 주소나 세션 원문을 노출하지 않고
   실패 단계를 표시한다. 디버깅 중에도 세션 로그/환경값을 공개 로그나 artifact에 올리지 않는다.
   유료 provider로 자동 전환하거나 검증 gate를 끄지 않는다.

로컬 워크플로 문법 검사:

```bash
actionlint .github/workflows/archify-pages.yml
```

인라인 Python을 변경할 때는 `run` 블록을 임시 파일로 추출해 Python 구문 검사와
네트워크/도구 호출을 대체한 안전성 검증을 수행한다. 실제 연동 검증은 위 최초 배포 절차를 따른다.

## 확인한 공식 계약

- [Tailscale Action v4 inputs와 post logout](https://github.com/tailscale/github-action/blob/v4/action.yml)
- [Tailscale workload identity federation](https://tailscale.com/docs/features/workload-identity-federation)
- [OpenCode custom provider](https://opencode.ai/docs/providers/#custom-provider)
- [OpenCode permissions](https://opencode.ai/docs/permissions/)
- [Archify 설치와 CLI](https://github.com/tt-a1i/archify/tree/f325d86230553e1dd15d3464324f1fcd01f02352/archify)
- [GitHub Pages custom workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
