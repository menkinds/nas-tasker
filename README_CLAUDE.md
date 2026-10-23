# nas-tasker

> 최종 갱신: 2026-10-23

## 무엇을 하는 프로젝트인가
YongJin의 Synology NAS에서 도는 작은 컨테이너 하나로, 어시스턴트 봇이 미리 정한 DSM 작업만 안전하게 다루게 한다. MCP 도구는 5개뿐이다: `list_tasks`, `run_task`, `task_result`, `mount_share`, `unmount_share`. 같은 기능의 HTTP API와 공유 폴더 암호 입력용 웹 화면이 함께 있다.

## 현재 상태
- 완료: 코드가 `main`에 다 들어갔다(PR #1, #2 머지, 최신 `cb4e484`). 테스트 168개 통과.
- 남음 (배포 전, NAS·Cloudflare 접근 필요):
  - Docker 이미지 빌드를 한 번도 확인하지 못했다(이 환경에 Docker 데몬이 없었다). 대신 패키지를 설치하고 `python -m nas_tasker`로 서버가 뜨는 것은 확인했다.
  - Task 12: DSM API 이름이 아직 추정값이다(`src/nas_tasker/dsm/api.py`).
  - Cloudflare 설정: 터널, Access 앱, `/mcp` Bypass 앱.

## 파일
| 파일 | 역할 |
|---|---|
| `docs/superpowers/specs/2026-10-23-nas-tasker-design.md` | 명세서 (GBrain `notes:3b28829cb4211c84` 원문) |
| `docs/superpowers/specs/2026-10-23-nas-tasker-bot-access.md` | 봇 접속 방식 지시서 (GBrain `notes:ff99da1e667876cc` 원문) |
| `docs/superpowers/plans/2026-10-23-nas-tasker.md` | 구현 계획 12단계 (Task 12만 남음) |
| `src/nas_tasker/app.py` | 앱 조립 (`build_app`) |
| `src/nas_tasker/middleware.py` | 경로 분류, 인증, `/mcp` 감사, IP 차단 |
| `src/nas_tasker/auth.py` | Access JWT 검증, 베어러 토큰 확인 |
| `src/nas_tasker/dispatch.py` | 도구 입력 검사, 호출 횟수 제한, 감사 로그 |
| `src/nas_tasker/tasks.py`, `outputs.py` | 작업 목록·실행·결과, 로그 꼬리 읽기 |
| `src/nas_tasker/shares.py`, `ui.py` | 공유 폴더 마운트(웹 화면 링크)·언마운트(확인 토큰 2단계) |
| `src/nas_tasker/dsm/api.py`, `dsm/client.py` | DSM API 이름(추정값), 허용 목록, 재로그인 |
| `src/nas_tasker/audit.py`, `ratelimit.py`, `tokens.py`, `validation.py`, `config.py`, `errors.py`, `identity.py` | 감사 로그, 호출·인증 실패 제한, 1회용 토큰, 입력 검사, 설정, 오류 형식, 신원 |
| `tests/mock_dsm.py` | 테스트용 가짜 DSM (`dsm/api.py`와 같은 추정 이름 사용) |
| `Dockerfile`, `synology/docker-compose.yml`, `synology/.env.example` | 배포 (synology/에는 배포 파일만) |
| `README.md` | 개발·배포 안내 |

## 확정된 결정
- `/mcp`는 베어러 토큰만으로 연다(Cloudflare Bypass 앱 전제). `/api/`는 Access JWT와 베어러 토큰을 둘 다, `/ui/`는 Access JWT만 요구한다. 서비스 토큰은 쓰지 않는다(지시서, YongJin 승인).
- 경로에 `.`·`..` 조각, `//`, `\`, `%`가 있거나 원래 경로에 `%2f`·`%2e`·`%5c`가 있으면 404로 거부한다. 이 규칙을 느슨하게 하면 `/mcp`로 Access를 우회하는 길이 생긴다.
- `chmod`/`chown`은 어디에도 쓰지 않는다(Dockerfile 주석 포함, 테스트가 확인). `synology/`에는 배포 파일만 둔다.
- 작업 이름은 `bot-[a-z0-9-]{1,64}`이고 `TASK_PREFIX`는 `bot-`로 고정한다. DSM에 같은 이름이 둘이면 실행을 거부한다(`TASK_AMBIGUOUS`).
- 실행 직후 30초 동안은 다시 실행을 거부하고 `task_result`도 `running`으로 답한다. DSM 상태가 늦게 바뀌거나 응답이 끊겨도 두 번 돌지 않게 하려는 것이다.
- `.env`의 빈 값(`KEY=`)은 기본값으로 처리한다.
- IP 하나가 5분 안에 인증을 10번 실패하면 10분 동안 막는다.

## 다음에 할 일
- 루시: Cloudflare 전용 터널(`http://127.0.0.1:<BIND_PORT>`), Access 앱(Google 로그인, 허용 이메일 2개, AUD 확인), `/mcp`만 건너뛰는 Bypass 앱.
- YongJin: DSM 전용 관리자 계정을 만든다(2단계 인증 끔, 비밀번호 만료 끔, 공유 폴더와 응용 프로그램 권한은 막아도 됨). `bot-gbrain-rebuild` 작업은 `<출력 폴더>/bot-gbrain-rebuild/latest.log`에 쓰게 하고, 감사 로그 폴더는 UID 1026이 쓸 수 있게 하고, `synology/.env`를 채운다.
- 함께 (NAS): Container Manager에서 빌드한 뒤 계획서 Task 12를 따른다. DSM 웹 화면의 `entry.cgi` 요청을 확인해 `dsm/api.py`, `dsm/client.py` 파싱, `tests/mock_dsm.py`를 함께 고치고, 연기 테스트를 한다.
- 선택 (미룬 사소한 문제):
  - 동시에 두 번 요청하면 마운트 링크가 두 개 생긴다.
  - 세션 만료 뒤 늦게 끝난 호출이 새 세션 ID를 지울 수 있다.
  - 인자가 api/method 키를 덮어쓸 여지가 있다.
  - HTTP가 아닌 연결은 검사 없이 통과한다.
  - `splitlines` 때문에 로그 줄 수가 틀릴 수 있다.
  - 비밀번호가 요청 객체 안에 남는다.
  - JWKS를 가져오는 동안 서버가 멈춘다.
  - MCP 오류에 `isError`가 붙지 않는다.

## 주의사항
- 개발: `python3 -m venv .venv && .venv/bin/pip install -e ".[test]" && .venv/bin/pytest -q`
- 의존하는 `mcp` SDK는 2.x다. `FastMCP`가 아니라 `mcp.server.mcpserver.MCPServer`를 쓴다. MCP 주소는 끝에 슬래시가 붙은 `/mcp/`다.
- DSM API 이름이나 필드를 바꿀 때는 `dsm/api.py`, `client.py` 파싱, `tests/mock_dsm.py` 세 곳을 함께 고친다. `ALLOWED_METHODS`에는 조회 메서드만 더한다.
- uvicorn access log는 꺼 두어야 한다. 켜면 `/ui/mount/<id>` 링크가 로그에 남는다.

## 세션 기록
<!-- pipeline.py가 [[링크]]를 위에 더한다. -->

- [[nas-tasker-2026-10-23-1-handoff]] — 명세서→계획→구현(168 테스트)→봇 접속 지시서 반영, PR #1·#2 머지
