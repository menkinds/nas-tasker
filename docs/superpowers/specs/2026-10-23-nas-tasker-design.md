# nas-tasker 설계 문서

- 작성일: 2026-10-23
- 소유자: YongJin Yoon
- 구현 담당: Claudia (이 문서를 기준으로 구현)
- 상태: 초안 (설계 단계)

## 1. 목표

- YongJin의 Synology NAS에서 실행되는 **작은 독립 컨테이너** 하나로, 어시스턴트 봇이 **미리 정의된 DSM 작업만** 안전하게 실행할 수 있게 한다.
- 노출 범위는 MCP 도구 5개(및 동일 기능의 HTTP API)로 한정한다.
  1. `list_tasks` 2. `run_task` 3. `task_result` 4. `mount_share` 5. `unmount_share`
- 모든 호출은 인증·인가를 거치고, 감사 로그로 남는다.
- 첫 예시 작업: `bot-gbrain-rebuild` (스크립트는 YongJin이 직접 작성·관리).

## 2. 비목표 (Hard limits)

- 작업 스케줄러 작업의 **생성/수정/삭제/활성화/비활성화 금지**. 실행과 조회만 한다.
- **파일 조작 금지**: 파일 생성/삭제/이동/쓰기 API를 노출하지 않는다 (출력 폴더는 읽기 전용 마운트로만 읽음).
- **권한/소유권 변경 금지**: 어디에서도 `chmod`/`chown` 사용 금지 (엔트리포인트 포함).
- **Docker 제어 금지**: Docker 소켓 마운트 금지, 컨테이너 시작/중지 등 일체 불가.
- `bot-` 접두사가 아닌 작업, 허용 목록 밖의 공유 폴더는 어떤 경우에도 다루지 않는다.
- 암호화 공유 폴더 비밀번호 저장/캐시/로그 기록 금지. 채팅을 통한 비밀번호 전달 금지.
- 범용 DSM 프록시가 아니다. 임의 API 호출 경로를 두지 않는다.

## 3. 아키텍처

```
봇(MCP 클라이언트) ──HTTPS──> Cloudflare (Access: Google 로그인) ──Tunnel──> nas-tasker 컨테이너
                                                                          │ (network_mode: host)
사용자 브라우저 ──> 동일 호스트명 /ui (비밀번호 입력 폼, 확인 화면)           │
                                                                          ▼
                                                              DSM Web API (localhost/LAN)
                                                              - SYNO.API.Auth
                                                              - SYNO.Core.TaskScheduler (비공식)
                                                              - 암호화 공유 폴더 mount/unmount API (미확정)
                                                              - Docker 패키지 조회 API (읽기 전용, 미확정)
                                         출력 폴더 (read-only 볼륨) ──> task_result
```

구성 요소:
- **API 서버**: MCP 엔드포인트(예: `/mcp`) + 동일 기능 HTTP 엔드포인트 + 최소한의 웹 UI(`/ui`).
- **인증 계층**: Cloudflare Access JWT 검증 → 이메일 허용 목록 → MCP 베어러 토큰 검증.
- **DSM 클라이언트**: 전용 관리자 계정으로 로그인, 세션(sid) 메모리 보관, 만료 시 재로그인. 허용된 API 메서드만 호출하는 화이트리스트 래퍼.
- **감사 로거**: 모든 호출을 append-only로 기록.
- **확인 토큰 저장소**: 메모리 내, 짧은 TTL, 1회용.
- **네트워킹**: Synology에서는 bridge 네트워크가 DSM API에 닿지 않는 경우가 많으므로 `network_mode: host` 사용. 서버는 가능한 한 `127.0.0.1`에 바인딩하고 cloudflared를 통해서만 외부 노출.
- **Cloudflare Tunnel**: 이 서비스 전용 터널/호스트명 (다른 서비스와 공유하지 않음).

## 4. 도구 명세

공통:
- 모든 응답은 JSON. 오류는 `{ "error": { "code": "...", "message": "..." } }`.
- 공통 오류: `UNAUTHENTICATED`, `FORBIDDEN`, `DSM_UNREACHABLE`, `DSM_AUTH_FAILED`, `DSM_API_ERROR`(DSM 오류 코드 포함), `INVALID_INPUT`, `RATE_LIMITED`, `INTERNAL`.
- `name` 입력은 엄격히 검증: `bot-` 작업은 `^bot-[a-z0-9-]{1,64}$`, 공유 폴더는 허용 목록과 정확히 일치.

### 4.1 `list_tasks`
- 입력: 없음
- 동작: `SYNO.Core.TaskScheduler` 목록 조회 후 이름이 `bot-`로 시작하는 작업만 반환 (필터링은 서버 측, 반드시 적용).
- 출력: `{ "tasks": [ { "name", "id", "enabled", "status": "idle|running|unknown", "last_run_at", "last_exit_code", "next_run_at" } ] }`
- 오류: 공통 오류.

### 4.2 `run_task(name)`
- 입력: `name` (string, `bot-` 접두사 필수)
- 동작:
  1. 이름 검증 → 목록에서 정확히 일치하는 작업 찾기 (`bot-` 아니면 거부).
  2. 현재 실행 중이면 거부.
  3. TaskScheduler의 "지금 실행" 메서드 호출.
- 출력: `{ "name", "started": true, "started_at", "run_id"? }`
- 오류: `TASK_NOT_FOUND`, `NOT_ALLOWED`(접두사 불일치), `TASK_ALREADY_RUNNING`, `TASK_DISABLED`(정책: 비활성 작업은 실행 거부 — 열린 질문 참고), 공통 오류.
- 동시성: 같은 작업에 대한 동시 요청은 서버 내 잠금으로 직렬화하여 이중 실행 방지.

### 4.3 `task_result(name)`
- 입력: `name`, 선택 `tail_lines` (기본 100, 최대 1000)
- 동작: DSM에서 최근 실행 상태/종료 코드 조회 + 읽기 전용 출력 폴더에서 해당 작업 출력 파일의 마지막 N줄 읽기.
- 출력 파일 규약: `<OUTPUT_DIR>/<name>/` 아래 최신 로그 (구체 규약은 작업 스크립트와 합의; 예: `latest.log`). 경로 조작 방지(심볼릭 링크/`..` 거부, 출력 폴더 밖 접근 금지).
- 출력: `{ "name", "status": "running|succeeded|failed|unknown", "exit_code", "finished_at", "output_tail": "...", "truncated": bool }`
- 오류: `TASK_NOT_FOUND`, `NOT_ALLOWED`, `OUTPUT_NOT_FOUND`, 공통 오류.
- 출력 꼬리에 비밀이 포함될 수 있으므로 크기 제한 적용. (마스킹은 열린 질문)

### 4.4 `mount_share(name)`
- 입력: `name` (허용 목록의 공유 폴더)
- 동작 (2단계):
  1. 봇 호출 시: 허용 목록 확인, 이미 마운트 상태면 `ALREADY_MOUNTED`. 아니면 1회용 요청 ID와 웹 UI 링크(`/ui/mount/<request_id>`)를 반환. **봇은 비밀번호를 받지도 전달하지도 않는다.**
  2. 사용자가 브라우저에서 Access 로그인 후 폼에 비밀번호 입력 → 서버가 메모리에서 1회 사용해 마운트 API 호출 → 즉시 폐기(변수 덮어쓰기/참조 해제), 응답·로그·예외 메시지·트레이스에 절대 포함 안 함.
- 출력(1단계): `{ "status": "pending_user_input", "request_id", "ui_url", "expires_at" }`
- 상태 확인: 동일 도구 재호출 또는 `request_id`로 결과 조회 → `{ "status": "mounted|failed|expired" }`
- 오류: `SHARE_NOT_ALLOWED`, `ALREADY_MOUNTED`, `WRONG_PASSWORD`(DSM 오류 매핑), `REQUEST_EXPIRED`, 공통 오류.
- 요청 ID TTL 기본 10분, 1회용. 폼은 POST 전용, CSRF 방지, 비밀번호 필드 autocomplete off.

### 4.5 `unmount_share(name, confirm_token?)`
- 입력: `name`, 선택 `confirm_token`
- 1단계 (토큰 없음): 허용 목록 확인 → 해당 폴더를 사용하는 컨테이너 목록을 **읽기 전용으로** 조회 (Docker 소켓 없이 DSM Container Manager API 등으로; 방법은 열린 질문) → 반환:
  `{ "status": "confirmation_required", "share", "containers_using": [ {"name", "state", "mount_path"} ], "confirm_token", "expires_at" }`
- 2단계 (토큰 있음): 토큰이 동일 share·동일 호출자·미만료·미사용인지 확인 → 언마운트 API 호출.
  출력: `{ "status": "unmounted" }`
- 토큰: 랜덤 128비트 이상, TTL 기본 5분, 1회용, share 및 호출자 신원에 바인딩.
- 오류: `SHARE_NOT_ALLOWED`, `NOT_MOUNTED`, `INVALID_CONFIRM_TOKEN`, `CONFIRM_TOKEN_EXPIRED`, `CONTAINER_LOOKUP_FAILED`(조회 실패 시 언마운트 진행 불가로 처리), 공통 오류.
- 컨테이너 조회 결과를 사용자에게 보여주는 것은 봇의 책임; 서버는 컨테이너를 중지하지 않는다.

## 5. 보안 모델

- **DSM 계정**: nas-tasker 전용 관리자 계정. API 로그인은 2FA를 지원하지 않으므로 2FA 없음 → 대신 긴 무작위 비밀번호, 가능하면 DSM 방화벽/계정 정책으로 LAN(로컬호스트)에서만 로그인 허용. 다른 용도로 사용 금지.
- **자격 증명 위치**: 이 프로젝트 전용 `.env`에만 보관. 저장소에는 `.env.example`(값 없음)만 커밋. 로그에 자격 증명 출력 금지.
- **외부 노출**: 전용 Cloudflare Tunnel 호스트명 + Cloudflare Access(Google 로그인). 허용 이메일: `menkinds@gmail.com`, `reservoir.crabs@gmail.com`.
- **앱 자체 Access JWT 검증** (Cloudflare 설정에만 의존하지 않음): `Cf-Access-Jwt-Assertion` 헤더 필수, 팀 도메인의 JWKS(`https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`)로 서명 검증, `iss`=팀 도메인, `aud`=설정된 AUD, `exp`/`nbf` 확인, `email`이 허용 목록에 포함. 서비스 토큰 사용 시 정책은 열린 질문.
- **MCP 베어러 토큰**: `/mcp` 및 봇용 API는 Access JWT에 더해 이 서비스 전용 베어러 토큰 필요 (상수 시간 비교). GBrain 등 다른 서비스 토큰은 유효하지 않음. 웹 UI는 Access JWT(사람 로그인)만으로 접근하되 비밀번호 입력/확인은 UI에서만.
- **최소 권한 래퍼**: DSM 클라이언트는 허용된 API/메서드 목록 외 호출 불가하도록 코드 수준에서 차단.
- **컨테이너 하드닝**: Docker 소켓 없음, 출력 폴더는 `:ro`, 쓰기 가능 볼륨은 감사 로그 디렉터리뿐, 비루트 실행(단, `chmod`/`chown` 없이 — 이미지 빌드 시 적절한 UID로 구성하거나 호스트에서 YongJin이 수동 준비), 가능한 경우 read-only 루트 파일시스템, `no-new-privileges`.
- **레이트 리밋**: 호출자별 간단한 제한 (특히 `run_task`, `mount_share` 실패 반복).
- **오류 메시지**: 내부 정보·비밀번호·세션 ID 노출 금지.

## 6. 설정 / `.env` 키 (이름만)

- `DSM_URL`
- `DSM_USERNAME`
- `DSM_PASSWORD`
- `DSM_VERIFY_TLS`
- `TASK_PREFIX` (기본 `bot-`, 변경 시에도 `bot-` 외 허용 금지 여부는 열린 질문)
- `TASK_OUTPUT_DIR`
- `SHARE_ALLOWLIST`
- `MCP_BEARER_TOKEN`
- `CF_ACCESS_TEAM_DOMAIN`
- `CF_ACCESS_AUD`
- `ALLOWED_EMAILS`
- `CLOUDFLARE_TUNNEL_TOKEN`
- `BIND_HOST`
- `BIND_PORT`
- `PUBLIC_BASE_URL`
- `AUDIT_LOG_PATH`
- `CONFIRM_TOKEN_TTL_SECONDS`
- `MOUNT_REQUEST_TTL_SECONDS`
- `LOG_LEVEL`
- `TZ`

## 7. 감사 로그

- 모든 호출(성공/실패/거부/인증 실패 포함)을 JSON Lines로 append.
- 필드: `ts`(ISO8601, 타임존 포함), `request_id`, `actor_email`(Access JWT), `auth_method`(bearer+access / ui), `client_ip`(CF 헤더), `tool`, `args`(비밀 제외, 화이트리스트 필드만), `result`(ok/denied/error), `error_code`, `duration_ms`, `dsm_error_code`.
- **절대 기록 금지**: 비밀번호, 베어러 토큰, JWT 원문, DSM sid, 확인 토큰 원문(해시 앞부분만 허용).
- 컨테이너 stdout에도 동일 요약을 남기되 같은 규칙 적용.
- 보존/회전 정책은 열린 질문 (앱 내부 크기 기반 회전 권장, 파일 권한 변경 없이).

## 8. 테스트

- **Mock DSM** 서버로 테스트 (실제 NAS 불필요): Auth, TaskScheduler 목록/실행/상태, 공유 폴더 mount/unmount, 컨테이너 조회를 흉내.
- 필수 테스트 케이스:
  - `bot-` 아닌 작업은 목록에서 제외되고 실행/결과 조회 거부.
  - 실행 중 작업 `run_task` 거부, 동시 요청 이중 실행 방지.
  - 작업 생성/수정/삭제/활성화 API가 클라이언트에서 호출 불가.
  - 허용 목록 밖 share 거부.
  - 비밀번호가 로그·감사 로그·응답·예외에 나타나지 않음 (로그 캡처 후 검색).
  - 언마운트 2단계: 토큰 없음/만료/재사용/다른 share/다른 호출자 거부.
  - Access JWT: 잘못된 서명, 잘못된 aud/iss, 만료, 허용 목록 밖 이메일 거부. 베어러 토큰 누락/불일치/GBrain 토큰 거부.
  - `task_result` 경로 조작(`..`, 심볼릭 링크) 거부, tail 크기 제한.
  - DSM 세션 만료 시 재로그인.
- 테스트와 참조 자료는 `synology/` 폴더 밖에 둔다.

## 9. 배포

- 저장소 규칙: `synology/` 폴더에는 **배포 파일만** (compose 파일, `.env.example`, 꼭 필요한 스크립트). 테스트·참조 문서 금지.
- Synology Container Manager에서 compose 프로젝트로 실행, `network_mode: host`.
- cloudflared는 이 서비스 전용 터널로 (사이드카 또는 별도 컨테이너, host 네트워크).
- 볼륨: 작업 출력 폴더(`:ro`), 감사 로그 디렉터리(쓰기). 그 외 없음.
- 사전 준비(YongJin 수동): 전용 DSM 관리자 계정 생성, `bot-gbrain-rebuild` 작업과 스크립트 작성 및 출력 경로 규약 맞추기, Cloudflare Access 애플리케이션/정책 생성 및 AUD 확인, `.env` 작성.
- 엔트리포인트에 `chmod`/`chown` 금지.

## 10. 열린 질문

1. 암호화 공유 폴더 마운트/언마운트의 **정확한 DSM API 이름/메서드/파라미터** (예: `SYNO.Core.Share.Crypto` 계열로 추정) — 구현 중 DSM 웹 UI 네트워크 요청으로 확인.
2. `SYNO.Core.TaskScheduler`의 정확한 메서드(목록, 실행, 상태/결과) 및 DSM 7.x 버전별 차이.
3. 공유 폴더를 사용하는 컨테이너를 Docker 소켓 없이 조회하는 방법 (Container Manager의 `SYNO.Docker.*` 계열 API 등) 및 조회 실패 시 동작.
4. 작업 출력 파일 경로/이름 규약 (`bot-gbrain-rebuild` 스크립트와 합의).
5. 비활성화된 `bot-` 작업 실행 허용 여부.
6. Cloudflare Access 서비스 토큰(봇의 비대화형 접근) 허용 여부와 그 경우 신원 표기 방법.
7. 출력 꼬리의 비밀 마스킹 필요 여부.
8. 감사 로그 보존 기간과 회전 방식.
9. DSM 접속을 `localhost` HTTP로 할지 HTTPS(자체 서명)로 할지.
