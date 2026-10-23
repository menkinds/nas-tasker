# nas-tasker 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Synology NAS에서 돌아가는 작은 컨테이너 하나로, 봇이 `bot-` 작업 실행·결과 조회와 허용된 암호화 공유 폴더 마운트·언마운트만 하도록 MCP 도구 5개와 같은 기능의 HTTP API, 비밀번호 입력용 웹 UI를 만든다.

**Architecture:** FastAPI 앱 하나에 `/mcp/`(MCP, stateless JSON), `/api/<tool>`(HTTP), `/ui/mount/<id>`(웹 UI)를 둔다. 맨 바깥 ASGI 미들웨어가 Cloudflare Access JWT(+봇 경로는 베어러 토큰)를 검증하고 호출자 신원을 contextvar에 넣는다. MCP와 HTTP는 같은 `Dispatcher`를 거쳐 입력 검사·레이트 리밋·감사 로그를 공유하고, DSM은 허용된 (api, method) 쌍만 보내는 `DsmClient` 하나로만 호출한다.

**Tech Stack:** Python 3.12+, FastAPI, uvicorn, httpx(AsyncClient + 테스트용 MockTransport), PyJWT[crypto], `mcp` SDK 2.x(`MCPServer`), python-multipart, pytest + anyio. 컨테이너는 `python:3.12-slim`, 배포는 Synology Container Manager compose + cloudflared.

**Spec:** `docs/superpowers/specs/2026-10-23-nas-tasker-design.md`

## Global Constraints

- 노출 도구는 정확히 5개: `list_tasks`, `run_task`, `task_result`, `mount_share`, `unmount_share` (+ 같은 기능의 HTTP API, `/ui`).
- 작업 이름은 `^bot-[a-z0-9-]{1,64}$`. `TASK_PREFIX`는 `bot-` 외 값이면 시작 시 거부.
- 공유 폴더는 `SHARE_ALLOWLIST`와 정확히 일치할 때만.
- DSM 클라이언트는 `ALLOWED_METHODS` 밖 호출을 보내기 전에 막는다. 작업 생성/수정/삭제/활성화/비활성화, Docker 제어, 파일 API 없음.
- 어디에도 `chmod`/`chown`(Dockerfile `--chown` 포함) 금지. Docker 소켓 마운트 금지.
- 비밀번호·베어러 토큰·JWT 원문·DSM sid·확인 토큰 원문은 응답·로그·감사 로그·예외 메시지에 절대 없음 (확인 토큰·요청 ID는 sha256 앞 12자만).
- 모든 응답은 JSON, 오류는 `{"error": {"code", "message"}}` (DSM 오류면 `dsm_error_code` 추가).
- Access JWT: `Cf-Access-Jwt-Assertion` 헤더, RS256, `iss`=`https://<team>.cloudflareaccess.com`, `aud`=`CF_ACCESS_AUD`, `exp` 필수, `email` ∈ `ALLOWED_EMAILS` (`menkinds@gmail.com`, `reservoir.crabs@gmail.com`).
- 봇 경로(`/mcp`, `/api/`)는 JWT + `MCP_BEARER_TOKEN`(상수 시간 비교). `/ui/`는 JWT만.
- 확인 토큰: 256비트 랜덤, TTL 기본 300초, 1회용, share와 호출자에 묶임. 마운트 요청: TTL 기본 600초, 1회용, POST 전용, CSRF 토큰, `autocomplete="off"`.
- `task_result`의 `tail_lines` 기본 100, 최대 1000. 출력 폴더 밖·심볼릭 링크 거부.
- 서버 기본 바인딩 `127.0.0.1`, 컨테이너 `network_mode: host`, 출력 폴더 `:ro`, 쓰기 볼륨은 감사 로그 폴더뿐, `read_only`, `no-new-privileges`, 비루트.
- `synology/`에는 `docker-compose.yml`, `.env.example`만. 테스트·참고 문서는 그 밖에.
- `.env`는 커밋 금지, `.env.example`은 키 이름만(값 없음).

## 열린 질문에 대한 잠정 결정

명세서 10절의 열린 질문을 아래처럼 잠정 결정하고 진행한다. 바꾸려면 표시된 한 곳만 고치면 된다.

| # | 질문 | 이 계획의 기본값 | 고칠 곳 |
|---|---|---|---|
| 1–3 | DSM API 이름·메서드·필드 | 추정값을 `dsm/api.py`에 모으고 Task 12에서 실제 NAS로 확인 | `src/nas_tasker/dsm/api.py`, `dsm/client.py` 파싱, `tests/mock_dsm.py` |
| 4 | 출력 파일 규약 | `<TASK_OUTPUT_DIR>/<작업 이름>/latest.log` | `outputs.LOG_FILE` |
| 5 | 비활성 작업 실행 | 거부 (`TASK_DISABLED`) | `TaskService.run_task` |
| 6 | Access 서비스 토큰 | **거부** (이메일 없는 JWT는 `FORBIDDEN`). **배포 전 결정 필요**: 비대화형 봇은 보통 서비스 토큰이 있어야 Access를 통과함 | `AccessVerifier.verify` |
| 7 | 출력 꼬리 마스킹 | 마스킹 없음, 256 KiB·1000줄 상한만 | `outputs.MAX_TAIL_BYTES` |
| 8 | 감사 로그 회전 | 앱 안에서 10 MiB마다 회전, 5개 보관 | `audit.MAX_BYTES`, `audit.BACKUPS` |
| 9 | DSM 접속 | 기본 `http://127.0.0.1:5000`, HTTPS면 `DSM_URL`·`DSM_VERIFY_TLS`로 | `.env` |

명세서에 없던 것을 이 계획이 더한 것:
- 오류 코드 `TASK_AMBIGUOUS`: DSM에 같은 `bot-` 이름이 둘 이상이면 실행하지 않는다.
- `mount_share`의 선택 인자 `request_id`: "request_id로 결과 조회"를 구현한다.
- 실행 직후 30초 유예(`RECENT_START_GRACE_SECONDS`): DSM 상태가 늦게 바뀌어도 이중 실행을 막는다.
- 웹 UI 비밀번호 시도 5회 상한(`MAX_PASSWORD_ATTEMPTS`).
- compose 전용 키 `TASK_OUTPUT_HOST_DIR`, `AUDIT_HOST_DIR`, `APP_UID`, `APP_GID` (호스트 경로와 컨테이너 UID).
- MCP 주소는 끝에 슬래시가 붙은 `<PUBLIC_BASE_URL>/mcp/`.

알려진 한계: MCP SDK는 타입이 틀린 인자(예: `name`이 숫자)를 `Dispatcher` 전에 막는다. 이런 호출은 인증은 통과했지만 감사 로그에 남지 않는다.

## Review Focus

테스트가 다루지 않으면 실제 사용 중에 가장 먼저 터질 만한 입력과 조건이다. 각 줄의 테스트는 해당 Task에 이미 들어 있다.

1. **DSM이 꺼졌거나 응답이 없을 때** → 트레이스 없이 깔끔한 `DSM_UNREACHABLE`(502). 마운트 도중이면 같은 링크로 다시 시도할 수 있다. (Task 4 `test_dsm_down_maps_to_unreachable`, Task 6 `test_dsm_down_during_mount_allows_retry`, Task 8 `test_dsm_down_is_clean_502`)
2. **실행 직후 DSM이 아직 "idle"이라고 할 때** → 두 번째 `run_task`는 `TASK_ALREADY_RUNNING`. (Task 5 `test_dsm_status_lag_still_blocks_second_start`)
3. **지저분한 로그 파일**(아주 큼, 줄바꿈 없는 거대한 한 줄, UTF-8 아님, FIFO) → 멈추거나 터지지 않고 상한 안에서 돌려준다. (Task 5 `test_outputs.py`)
4. **브라우저에서 두 번 누르기, 만료된 링크 다시 열기, 재시작 뒤 사라진 링크** → DSM 마운트 호출은 한 번만, 만료 안내 페이지. (Task 6 `test_unknown_request_id_reads_as_expired`, Task 10 `test_double_submit_mounts_once`, `test_expired_or_unknown_link_is_friendly`)
5. **DSM에 같은 이름의 작업이 둘, 대소문자·공백이 섞인 이름** → 실행 거부. (Task 2 `test_invalid_task_names`, Task 5 `test_duplicate_names_refused`)

## 파일 구조

```
pyproject.toml                 패키지·의존성·pytest 설정
.gitignore
Dockerfile                     비루트 이미지 (chmod/chown 없음)
README.md                      설치·배포 안내
synology/docker-compose.yml    배포 전용
synology/.env.example          키 이름만
src/nas_tasker/
  __init__.py
  __main__.py                  uvicorn 실행 (access log 끔)
  errors.py                    ToolError, 오류 코드 → HTTP 상태
  config.py                    .env → Settings (검증 포함)
  identity.py                  Identity(subject, auth_method)
  validation.py                작업 이름·share·tail_lines 검사
  tokens.py                    1회용 토큰 저장소, 토큰 해시
  ratelimit.py                 호출자별 슬라이딩 윈도우
  audit.py                     JSONL 감사 로그, 인자 화이트리스트, 회전
  dsm/api.py                   DSM API 이름·허용 메서드 (추정값은 여기만)
  dsm/client.py                DsmClient: 로그인·재로그인·허용 목록·파싱
  dsm/__init__.py
  outputs.py                   출력 파일 꼬리 읽기 (심볼릭 링크·FIFO 거부)
  tasks.py                     list_tasks / run_task / task_result
  shares.py                    mount_share(2단계) / unmount_share(2단계)
  auth.py                      Access JWT 검증, 베어러 확인
  dispatch.py                  도구 입력 검사·레이트 리밋·감사, CallContext
  middleware.py                인증 ASGI 미들웨어 (그 밖의 경로는 404)
  http_api.py                  POST /api/{tool}
  mcp_server.py                MCP 도구 5개
  ui.py                        /ui/mount/{id} 폼과 제출
  app.py                       build_app(): 조립
tests/
  __init__.py, conftest.py     anyio, mock DSM 픽스처
  mock_dsm.py                  httpx.MockTransport 기반 가짜 DSM
  jwt_helpers.py               테스트용 RSA 키·JWT
  app_helpers.py               TestClient, 헤더, 감사 로그 읽기
  test_*.py
```

---

### Task 1: 프로젝트 뼈대, 설정, 오류 형식

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `src/nas_tasker/__init__.py` (빈 파일), `src/nas_tasker/errors.py`, `src/nas_tasker/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `errors.ToolError(code: str, message: str, dsm_error_code: int | None = None)`. `.code`, `.message`, `.dsm_error_code`, `.http_status -> int`, `.to_dict() -> {"error": {...}}`가 있다. `str(err)`는 code만 돌려준다.
  - `errors.HTTP_STATUS: dict[str, int]`, `errors.DENIED_CODES: frozenset[str]`
  - `config.load_settings(env: Mapping[str, str] = os.environ) -> Settings`, `config.ConfigError`
  - `config.Settings` 필드: `dsm_url, dsm_username, dsm_password, dsm_verify_tls, task_output_dir, share_allowlist: frozenset[str], mcp_bearer_token, cf_access_issuer ("https://<team>"), cf_access_aud, allowed_emails: frozenset[str] (소문자), bind_host, bind_port, public_base_url (끝 슬래시 없음), audit_log_path, confirm_token_ttl_seconds, mount_request_ttl_seconds, log_level`

- [ ] **Step 1: 패키지 파일 만들기**

`pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "nas-tasker"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "httpx>=0.27",
    "pyjwt[crypto]>=2.9",
    "mcp>=2.3,<3",
    "python-multipart>=0.0.9",
]

[project.optional-dependencies]
test = ["pytest>=8", "anyio>=4"]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`.gitignore`:

```text
.env
__pycache__/
*.egg-info/
.venv/
.pytest_cache/
```

빈 `src/nas_tasker/__init__.py`, 빈 `tests/__init__.py`를 만든다. 그다음:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[test]"
```

- [ ] **Step 2: 실패하는 테스트 쓰기** — `tests/test_config.py`

```python
import pytest

from nas_tasker.config import ConfigError, load_settings
from nas_tasker.errors import ToolError

BASE_ENV = {
    "DSM_USERNAME": "nas-tasker",
    "DSM_PASSWORD": "dsm-secret-pw",
    "TASK_OUTPUT_DIR": "/data/output",
    "MCP_BEARER_TOKEN": "b" * 40,
    "CF_ACCESS_TEAM_DOMAIN": "myteam.cloudflareaccess.com",
    "CF_ACCESS_AUD": "aud-123",
    "ALLOWED_EMAILS": "Menkinds@gmail.com, reservoir.crabs@gmail.com",
    "PUBLIC_BASE_URL": "https://tasker.example.com/",
}


def test_defaults_and_normalisation():
    s = load_settings(BASE_ENV)
    assert s.dsm_url == "http://127.0.0.1:5000"
    assert s.dsm_verify_tls is True
    assert s.cf_access_issuer == "https://myteam.cloudflareaccess.com"
    assert s.allowed_emails == {"menkinds@gmail.com", "reservoir.crabs@gmail.com"}
    assert s.share_allowlist == frozenset()
    assert s.public_base_url == "https://tasker.example.com"
    assert (s.bind_host, s.bind_port) == ("127.0.0.1", 8787)
    assert (s.confirm_token_ttl_seconds, s.mount_request_ttl_seconds) == (300, 600)


def test_secrets_not_in_repr():
    text = repr(load_settings(BASE_ENV))
    assert "dsm-secret-pw" not in text
    assert "b" * 40 not in text


def test_missing_required_key():
    env = dict(BASE_ENV, DSM_PASSWORD="")
    with pytest.raises(ConfigError, match="DSM_PASSWORD"):
        load_settings(env)


@pytest.mark.parametrize("prefix", ["", "job-", "bot"])
def test_task_prefix_cannot_change(prefix):
    with pytest.raises(ConfigError, match="TASK_PREFIX"):
        load_settings(dict(BASE_ENV, TASK_PREFIX=prefix))


def test_short_bearer_token_rejected():
    with pytest.raises(ConfigError, match="MCP_BEARER_TOKEN"):
        load_settings(dict(BASE_ENV, MCP_BEARER_TOKEN="short"))


def test_bad_bool_and_int():
    with pytest.raises(ConfigError, match="DSM_VERIFY_TLS"):
        load_settings(dict(BASE_ENV, DSM_VERIFY_TLS="maybe"))
    with pytest.raises(ConfigError, match="BIND_PORT"):
        load_settings(dict(BASE_ENV, BIND_PORT="0"))


def test_tool_error_shape():
    err = ToolError("DSM_API_ERROR", "DSM API call failed", dsm_error_code=117)
    assert str(err) == "DSM_API_ERROR"
    assert err.http_status == 502
    assert err.to_dict() == {
        "error": {"code": "DSM_API_ERROR", "message": "DSM API call failed", "dsm_error_code": 117}
    }
```

- [ ] **Step 3: 실패 확인**

Run: `.venv/bin/pytest tests/test_config.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nas_tasker.config'`

- [ ] **Step 4: 구현** — `src/nas_tasker/errors.py`

```python
HTTP_STATUS = {
    "UNAUTHENTICATED": 401,
    "FORBIDDEN": 403,
    "NOT_ALLOWED": 403,
    "SHARE_NOT_ALLOWED": 403,
    "INVALID_INPUT": 400,
    "INVALID_CONFIRM_TOKEN": 400,
    "CONFIRM_TOKEN_EXPIRED": 400,
    "WRONG_PASSWORD": 400,
    "TASK_NOT_FOUND": 404,
    "OUTPUT_NOT_FOUND": 404,
    "TASK_ALREADY_RUNNING": 409,
    "TASK_DISABLED": 409,
    "TASK_AMBIGUOUS": 409,
    "ALREADY_MOUNTED": 409,
    "NOT_MOUNTED": 409,
    "REQUEST_EXPIRED": 410,
    "RATE_LIMITED": 429,
    "INTERNAL": 500,
    "DSM_UNREACHABLE": 502,
    "DSM_AUTH_FAILED": 502,
    "DSM_API_ERROR": 502,
    "CONTAINER_LOOKUP_FAILED": 502,
}

# Audit log "result" is "denied" for these codes, "error" for the rest.
DENIED_CODES = frozenset(
    {"UNAUTHENTICATED", "FORBIDDEN", "NOT_ALLOWED", "SHARE_NOT_ALLOWED", "RATE_LIMITED"}
)


class ToolError(Exception):
    """An error safe to show to the caller. str(err) is only the code."""

    def __init__(self, code: str, message: str, dsm_error_code: int | None = None):
        super().__init__(code)
        self.code = code
        self.message = message
        self.dsm_error_code = dsm_error_code

    @property
    def http_status(self) -> int:
        return HTTP_STATUS.get(self.code, 500)

    def to_dict(self) -> dict:
        err = {"code": self.code, "message": self.message}
        if self.dsm_error_code is not None:
            err["dsm_error_code"] = self.dsm_error_code
        return {"error": err}
```

`src/nas_tasker/config.py`:

```python
import os
from collections.abc import Mapping
from dataclasses import dataclass, field

REQUIRED = (
    "DSM_USERNAME",
    "DSM_PASSWORD",
    "TASK_OUTPUT_DIR",
    "MCP_BEARER_TOKEN",
    "CF_ACCESS_TEAM_DOMAIN",
    "CF_ACCESS_AUD",
    "ALLOWED_EMAILS",
    "PUBLIC_BASE_URL",
)


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    dsm_url: str
    dsm_username: str
    dsm_password: str = field(repr=False)
    dsm_verify_tls: bool
    task_output_dir: str
    share_allowlist: frozenset[str]
    mcp_bearer_token: str = field(repr=False)
    cf_access_issuer: str
    cf_access_aud: str
    allowed_emails: frozenset[str]
    bind_host: str
    bind_port: int
    public_base_url: str
    audit_log_path: str
    confirm_token_ttl_seconds: int
    mount_request_ttl_seconds: int
    log_level: str


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _bool(name: str, value: str) -> bool:
    low = value.strip().lower()
    if low in ("1", "true", "yes"):
        return True
    if low in ("0", "false", "no"):
        return False
    raise ConfigError(f"{name} must be true or false")


def _positive_int(name: str, value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise ConfigError(f"{name} must be an integer") from None
    if number <= 0:
        raise ConfigError(f"{name} must be positive")
    return number


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    missing = [key for key in REQUIRED if not env.get(key, "").strip()]
    if missing:
        raise ConfigError("missing settings: " + ", ".join(missing))
    if env.get("TASK_PREFIX", "bot-") != "bot-":
        raise ConfigError("TASK_PREFIX must be 'bot-'")
    if len(env["MCP_BEARER_TOKEN"]) < 32:
        raise ConfigError("MCP_BEARER_TOKEN must be at least 32 characters")

    team = env["CF_ACCESS_TEAM_DOMAIN"].strip().removeprefix("https://").rstrip("/")
    return Settings(
        dsm_url=env.get("DSM_URL", "http://127.0.0.1:5000").rstrip("/"),
        dsm_username=env["DSM_USERNAME"],
        dsm_password=env["DSM_PASSWORD"],
        dsm_verify_tls=_bool("DSM_VERIFY_TLS", env.get("DSM_VERIFY_TLS", "true")),
        task_output_dir=env["TASK_OUTPUT_DIR"],
        share_allowlist=frozenset(_csv(env.get("SHARE_ALLOWLIST", ""))),
        mcp_bearer_token=env["MCP_BEARER_TOKEN"],
        cf_access_issuer=f"https://{team}",
        cf_access_aud=env["CF_ACCESS_AUD"].strip(),
        allowed_emails=frozenset(e.lower() for e in _csv(env["ALLOWED_EMAILS"])),
        bind_host=env.get("BIND_HOST", "127.0.0.1"),
        bind_port=_positive_int("BIND_PORT", env.get("BIND_PORT", "8787")),
        public_base_url=env["PUBLIC_BASE_URL"].rstrip("/"),
        audit_log_path=env.get("AUDIT_LOG_PATH", "/data/audit/audit.jsonl"),
        confirm_token_ttl_seconds=_positive_int(
            "CONFIRM_TOKEN_TTL_SECONDS", env.get("CONFIRM_TOKEN_TTL_SECONDS", "300")
        ),
        mount_request_ttl_seconds=_positive_int(
            "MOUNT_REQUEST_TTL_SECONDS", env.get("MOUNT_REQUEST_TTL_SECONDS", "600")
        ),
        log_level=env.get("LOG_LEVEL", "INFO").upper(),
    )
```

- [ ] **Step 5: 통과 확인**

Run: `.venv/bin/pytest tests/test_config.py -q`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add pyproject.toml .gitignore src tests
git commit -m "feat: project skeleton, settings loading and error format"
```

---

### Task 2: 입력 검사, 1회용 토큰, 레이트 리밋, 신원

**Files:**
- Create: `src/nas_tasker/validation.py`, `src/nas_tasker/tokens.py`, `src/nas_tasker/ratelimit.py`, `src/nas_tasker/identity.py`
- Test: `tests/test_primitives.py`

**Interfaces:**
- Consumes: `ToolError` (Task 1)
- Produces:
  - `validation.TASK_PREFIX = "bot-"`, `check_task_name(name: object) -> str`. 접두사가 다르면 `NOT_ALLOWED`, 형식이 틀리면 `INVALID_INPUT`.
  - `validation.check_share_name(name: object, allowlist: frozenset[str]) -> str`. 목록에 없으면 `SHARE_NOT_ALLOWED`.
  - `validation.check_tail_lines(value: object) -> int`. None이면 100, 범위는 1–1000.
  - `tokens.new_token() -> str`, `tokens.token_hash(token: str) -> str` (12자)
  - `tokens.OneTimeStore(ttl_seconds: int, clock=time.time)`: `.issue(payload) -> (token, expires_at_epoch)`, `.take(token, matches: Callable[[payload], bool]) -> payload`. 실패하면 `TokenInvalid`나 `TokenExpired`를 던진다. payload가 맞지 않은 토큰은 소모하지 않는다.
  - `ratelimit.RateLimiter(limits=DEFAULT_LIMITS, clock=time.monotonic)`: `.check(caller: str, tool: str) -> None`. 넘으면 `RATE_LIMITED`.
  - `identity.Identity(subject: str, auth_method: str)` (frozen dataclass)

- [ ] **Step 1: 실패하는 테스트 쓰기** — `tests/test_primitives.py`

```python
import pytest

from nas_tasker.errors import ToolError
from nas_tasker.ratelimit import RateLimiter
from nas_tasker.tokens import OneTimeStore, TokenExpired, TokenInvalid, token_hash
from nas_tasker.validation import check_share_name, check_tail_lines, check_task_name


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.mark.parametrize("name", ["bot-gbrain-rebuild", "bot-a", "bot-" + "a" * 64])
def test_valid_task_names(name):
    assert check_task_name(name) == name


@pytest.mark.parametrize(
    "name,code",
    [
        ("gbrain-rebuild", "NOT_ALLOWED"),
        ("Bot-x", "NOT_ALLOWED"),
        ("bot-", "INVALID_INPUT"),
        ("bot-X", "INVALID_INPUT"),
        ("bot-a b", "INVALID_INPUT"),
        ("bot-../etc", "INVALID_INPUT"),
        ("bot-a/b", "INVALID_INPUT"),
        ("bot-a\n", "INVALID_INPUT"),
        ("bot-" + "a" * 65, "INVALID_INPUT"),
        (123, "INVALID_INPUT"),
        (None, "INVALID_INPUT"),
    ],
)
def test_invalid_task_names(name, code):
    with pytest.raises(ToolError) as exc:
        check_task_name(name)
    assert exc.value.code == code


def test_share_must_match_allowlist_exactly():
    allow = frozenset({"secret"})
    assert check_share_name("secret", allow) == "secret"
    for bad in ["Secret", "secret ", "secret/..", "other"]:
        with pytest.raises(ToolError) as exc:
            check_share_name(bad, allow)
        assert exc.value.code == "SHARE_NOT_ALLOWED"
    with pytest.raises(ToolError) as exc:
        check_share_name(["secret"], allow)
    assert exc.value.code == "INVALID_INPUT"


def test_tail_lines():
    assert check_tail_lines(None) == 100
    assert check_tail_lines(1000) == 1000
    for bad in [0, 1001, -1, True, "10", 1.5]:
        with pytest.raises(ToolError):
            check_tail_lines(bad)


def test_one_time_store_consumes_once():
    store = OneTimeStore(300, Clock())
    token, expires_at = store.issue({"share": "s"})
    assert expires_at == 1300.0
    assert len(token) >= 22  # >= 128 bits in urlsafe base64
    assert store.take(token, lambda p: True) == {"share": "s"}
    with pytest.raises(TokenInvalid):
        store.take(token, lambda p: True)


def test_one_time_store_expiry_and_mismatch():
    clock = Clock()
    store = OneTimeStore(300, clock)
    token, _ = store.issue({"share": "s"})
    with pytest.raises(TokenInvalid):
        store.take(token, lambda p: False)
    clock.now += 300
    with pytest.raises(TokenExpired):
        store.take(token, lambda p: True)
    with pytest.raises(TokenInvalid):
        store.take(None, lambda p: True)


def test_token_hash_is_short_and_stable():
    assert token_hash("abc") == token_hash("abc")
    assert len(token_hash("abc")) == 12
    assert "abc" not in token_hash("abc")


def test_rate_limiter_window():
    clock = Clock()
    limiter = RateLimiter({"run_task": (2, 60.0), "*": (100, 60.0)}, clock)
    limiter.check("a", "run_task")
    limiter.check("a", "run_task")
    with pytest.raises(ToolError) as exc:
        limiter.check("a", "run_task")
    assert exc.value.code == "RATE_LIMITED"
    limiter.check("b", "run_task")  # other caller unaffected
    clock.now += 60
    limiter.check("a", "run_task")
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_primitives.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nas_tasker.ratelimit'`

- [ ] **Step 3: 구현**

`src/nas_tasker/validation.py`:

```python
import re

from .errors import ToolError

TASK_PREFIX = "bot-"
_TASK_NAME = re.compile(r"bot-[a-z0-9-]{1,64}")
DEFAULT_TAIL_LINES = 100
MAX_TAIL_LINES = 1000


def check_task_name(name: object) -> str:
    if not isinstance(name, str):
        raise ToolError("INVALID_INPUT", "name must be a string")
    if not name.startswith(TASK_PREFIX):
        raise ToolError("NOT_ALLOWED", "only bot- tasks are allowed")
    if not _TASK_NAME.fullmatch(name):
        raise ToolError("INVALID_INPUT", "invalid task name")
    return name


def check_share_name(name: object, allowlist: frozenset[str]) -> str:
    if not isinstance(name, str):
        raise ToolError("INVALID_INPUT", "name must be a string")
    if name not in allowlist:
        raise ToolError("SHARE_NOT_ALLOWED", "share is not in the allowlist")
    return name


def check_tail_lines(value: object) -> int:
    if value is None:
        return DEFAULT_TAIL_LINES
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError("INVALID_INPUT", "tail_lines must be an integer")
    if not 1 <= value <= MAX_TAIL_LINES:
        raise ToolError("INVALID_INPUT", f"tail_lines must be between 1 and {MAX_TAIL_LINES}")
    return value
```

`src/nas_tasker/tokens.py`:

```python
import hashlib
import secrets
import time
from collections.abc import Callable
from typing import Any


class TokenInvalid(Exception):
    pass


class TokenExpired(Exception):
    pass


def new_token() -> str:
    return secrets.token_urlsafe(32)  # 256 bits


def token_hash(token: str) -> str:
    """Short, non-reversible label for logs."""
    return hashlib.sha256(token.encode()).hexdigest()[:12]


class OneTimeStore:
    """In-memory one-time tokens with a TTL. Lost on restart, by design."""

    def __init__(self, ttl_seconds: int, clock: Callable[[], float] = time.time):
        self._ttl = ttl_seconds
        self._clock = clock
        self._items: dict[str, tuple[float, Any]] = {}

    def issue(self, payload: Any) -> tuple[str, float]:
        now = self._clock()
        self._items = {t: item for t, item in self._items.items() if item[0] > now}
        token = new_token()
        expires_at = now + self._ttl
        self._items[token] = (expires_at, payload)
        return token, expires_at

    def take(self, token: object, matches: Callable[[Any], bool]) -> Any:
        """Consume a token. A token whose payload does not match stays unused."""
        item = self._items.get(token) if isinstance(token, str) else None
        if item is None:
            raise TokenInvalid()
        expires_at, payload = item
        if self._clock() >= expires_at:
            del self._items[token]
            raise TokenExpired()
        if not matches(payload):
            raise TokenInvalid()
        del self._items[token]
        return payload
```

`src/nas_tasker/ratelimit.py`:

```python
import time
from collections import defaultdict, deque
from collections.abc import Callable

from .errors import ToolError

# tool -> (max calls, window seconds), per caller
DEFAULT_LIMITS = {
    "run_task": (10, 60.0),
    "mount_share": (10, 600.0),
    "unmount_share": (10, 600.0),
    "*": (60, 60.0),
}


class RateLimiter:
    def __init__(
        self,
        limits: dict[str, tuple[int, float]] = DEFAULT_LIMITS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._limits = limits
        self._clock = clock
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def check(self, caller: str, tool: str) -> None:
        limit, window = self._limits.get(tool, self._limits["*"])
        now = self._clock()
        hits = self._hits[(caller, tool)]
        while hits and hits[0] <= now - window:
            hits.popleft()
        if len(hits) >= limit:
            raise ToolError("RATE_LIMITED", "too many requests, try again later")
        hits.append(now)
```

`src/nas_tasker/identity.py`:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class Identity:
    subject: str  # verified, lower-cased email from the Access JWT
    auth_method: str  # "bearer+access" for the bot, "ui" for a person in the browser
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_primitives.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/nas_tasker tests/test_primitives.py
git commit -m "feat: input validation, one-time tokens, rate limiter"
```

---

### Task 3: 감사 로그

**Files:**
- Create: `src/nas_tasker/audit.py`
- Test: `tests/test_audit.py`

**Interfaces:**
- Consumes: `tokens.token_hash` (Task 2)
- Produces:
  - `audit.safe_args(tool: str, args: object) -> dict`. `SAFE_ARGS[tool]`에 있는 필드만 남기고, `request_id`·`confirm_token`은 `<key>_hash`로 바꾼다.
  - `audit.AuditLogger(path: str, max_bytes=MAX_BYTES, backups=BACKUPS)`: `.record(**fields) -> None`. JSONL 한 줄(`ts`는 타임존이 붙은 ISO8601)을 쓰고, 같은 줄을 로거 `nas_tasker.audit`에도 INFO로 남긴다.
  - 호출하는 쪽(Task 8, 10)이 넘기는 필드: `request_id, actor_email, auth_method, client_ip, tool, args, result, error_code, duration_ms, dsm_error_code`

- [ ] **Step 1: 실패하는 테스트 쓰기** — `tests/test_audit.py`

```python
import json
import logging

from nas_tasker.audit import AuditLogger, safe_args
from nas_tasker.tokens import token_hash


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_record_appends_json_lines(tmp_path):
    path = tmp_path / "audit.jsonl"
    audit = AuditLogger(str(path))
    audit.record(tool="list_tasks", result="ok")
    audit.record(tool="run_task", result="denied", error_code="NOT_ALLOWED")
    lines = read_lines(path)
    assert [e["tool"] for e in lines] == ["list_tasks", "run_task"]
    assert lines[1]["error_code"] == "NOT_ALLOWED"
    # ISO8601 with timezone offset
    assert lines[0]["ts"][-6] in "+-" or lines[0]["ts"].endswith("Z")


def test_record_also_logs_to_stdout_logger(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="nas_tasker.audit")
    AuditLogger(str(tmp_path / "a.jsonl")).record(tool="list_tasks", result="ok")
    assert '"tool": "list_tasks"' in caplog.text


def test_safe_args_whitelists_and_hashes():
    args = {
        "name": "secret",
        "password": "hunter2",
        "confirm_token": "tok-raw",
        "request_id": "req-raw",
        "extra": "x",
    }
    out = safe_args("unmount_share", args)
    assert out == {
        "name": "secret",
        "confirm_token_hash": token_hash("tok-raw"),
        "request_id_hash": token_hash("req-raw"),
    }
    assert "hunter2" not in json.dumps(out)
    assert safe_args("run_task", "not a dict") == {}
    assert safe_args("unknown_tool", {"name": "x"}) == {}


def test_rotation_keeps_backups(tmp_path):
    path = tmp_path / "audit.jsonl"
    audit = AuditLogger(str(path), max_bytes=200, backups=2)
    for i in range(30):
        audit.record(tool="list_tasks", n=i, pad="x" * 50)
    assert path.exists()
    assert (tmp_path / "audit.jsonl.1").exists()
    assert (tmp_path / "audit.jsonl.2").exists()
    assert not (tmp_path / "audit.jsonl.3").exists()
    assert read_lines(path)[-1]["n"] == 29
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_audit.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nas_tasker.audit'`

- [ ] **Step 3: 구현** — `src/nas_tasker/audit.py`

```python
import json
import logging
import os
import threading
from datetime import datetime

from .tokens import token_hash

# Only these argument fields are ever written. Capability-like values are hashed.
SAFE_ARGS = {
    "list_tasks": (),
    "run_task": ("name",),
    "task_result": ("name", "tail_lines"),
    "mount_share": ("name",),
    "unmount_share": ("name",),
    "ui_mount": ("name",),
}
HASHED_ARGS = ("request_id", "confirm_token")

MAX_BYTES = 10 * 1024 * 1024
BACKUPS = 5

log = logging.getLogger("nas_tasker.audit")


def safe_args(tool: str, args: object) -> dict:
    if not isinstance(args, dict):
        return {}
    out = {k: args[k] for k in SAFE_ARGS.get(tool, ()) if k in args}
    for key in HASHED_ARGS:
        value = args.get(key)
        if isinstance(value, str):
            out[f"{key}_hash"] = token_hash(value)
    return out


class AuditLogger:
    """Append-only JSON Lines audit log with size-based rotation."""

    def __init__(self, path: str, max_bytes: int = MAX_BYTES, backups: int = BACKUPS):
        self._path = path
        self._max_bytes = max_bytes
        self._backups = backups
        self._lock = threading.Lock()

    def record(self, **fields) -> None:
        entry = {"ts": datetime.now().astimezone().isoformat(timespec="milliseconds"), **fields}
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str)
        with self._lock:
            self._rotate_if_needed()
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        log.info(line)

    def _rotate_if_needed(self) -> None:
        try:
            if os.path.getsize(self._path) < self._max_bytes:
                return
        except FileNotFoundError:
            return
        for i in range(self._backups - 1, 0, -1):
            src = f"{self._path}.{i}"
            if os.path.exists(src):
                os.replace(src, f"{self._path}.{i + 1}")
        os.replace(self._path, f"{self._path}.1")
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_audit.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: 커밋**

```bash
git add src/nas_tasker/audit.py tests/test_audit.py
git commit -m "feat: append-only JSONL audit log with arg whitelist and rotation"
```

---

### Task 4: Mock DSM과 DSM 클라이언트

**Files:**
- Create: `src/nas_tasker/dsm/__init__.py`, `src/nas_tasker/dsm/api.py`, `src/nas_tasker/dsm/client.py`
- Create: `tests/mock_dsm.py`, `tests/conftest.py`
- Test: `tests/test_dsm_client.py`

**Interfaces:**
- Consumes: `ToolError` (Task 1)
- Produces:
  - `dsm.api.ALLOWED_METHODS: frozenset[tuple[str, str]]`, `SESSION_ERROR_CODES`, `WRONG_PASSWORD_CODES`, API 상수 `AUTH, TASK_SCHEDULER, SHARE, SHARE_CRYPTO, DOCKER_CONTAINER` (`(name, version)`)
  - `DsmClient(base_url, username, password, *, verify_tls=True, transport=None, timeout=15.0)` (모두 async)
    - `.call(api_name, method, version, params=None) -> dict`. 허용 목록 밖이면 `ForbiddenDsmCall`(보내지 않음)을 던진다. 세션 오류(106/107/119)면 한 번 다시 로그인하고, DSM이 실패를 돌려주면 `DsmApiError(code)`를 던진다.
    - `.list_tasks() -> list[TaskInfo]`, `.run_task(task: TaskInfo) -> None`
    - `.get_share(name) -> ShareInfo`, `.mount_share(name, password) -> None`, `.unmount_share(name) -> None`
    - `.list_containers_using(share) -> list[ContainerUse]`. 실패하면 모두 `CONTAINER_LOOKUP_FAILED`로 바뀐다.
    - `.aclose()`
  - `TaskInfo(id, name, enabled, status: "idle"|"running"|"unknown", last_run_at, last_exit_code, next_run_at, real_owner)`, `ShareInfo(name, encrypted, mounted)`, `ContainerUse(name, state, mount_path)`
  - `DsmApiError(ToolError)`: code는 `DSM_API_ERROR`이고 `.dsm_error_code`를 가진다.
  - 네트워크·HTTP·JSON 오류는 `ToolError("DSM_UNREACHABLE")`로 바뀌고, 원래 예외는 연결하지 않는다(`from None`).
  - 테스트 픽스처: `mock_dsm` (`MockDsm`), `dsm` (`DsmClient`), `anyio_backend`
  - `MockDsm` 조작 손잡이: `.tasks`, `.shares`, `.containers`, `.calls`, `.runs`, `.logins`, `.down`, `.run_sets_running`, `.fail_containers`, `.expire_sessions()`
  - 테스트 상수: `DSM_USER`, `DSM_PASS`, `SHARE_PASSWORD`

DSM 이름과 필드는 **추정값**이다. 바꿀 때는 `dsm/api.py`, `client.py`의 파싱 코드, `tests/mock_dsm.py`를 한 번에 함께 고친다(Task 12).

- [ ] **Step 1: 테스트 기반 만들기**

`tests/mock_dsm.py`:

```python
"""In-process fake of the DSM Web API, served through httpx.MockTransport.

Mirrors the PROVISIONAL names in nas_tasker/dsm/api.py.
"""

import json
from urllib.parse import parse_qsl

import httpx

DSM_USER = "nas-tasker"
DSM_PASS = "dsm-secret-pw"
SHARE_PASSWORD = "share-pw-Zq9!"


def ok(data=None):
    return httpx.Response(200, json={"success": True, "data": data or {}})


def fail(code):
    return httpx.Response(200, json={"success": False, "error": {"code": code}})


class MockDsm:
    def __init__(self):
        self.tasks = [
            {"id": 1, "name": "bot-gbrain-rebuild", "enable": True, "running": False,
             "real_owner": "root", "last_run_time": "2026-10-22 03:00", "last_exit_code": 0,
             "next_trigger_time": "2026-10-24 03:00"},
            {"id": 2, "name": "nightly-backup", "enable": True, "running": False,
             "real_owner": "root", "last_run_time": None, "last_exit_code": None,
             "next_trigger_time": None},
            {"id": 3, "name": "bot-disabled", "enable": False, "running": False,
             "real_owner": "root", "last_run_time": None, "last_exit_code": None,
             "next_trigger_time": None},
        ]
        self.shares = {
            "secret": {"encryption": 1, "is_mounted": False, "password": SHARE_PASSWORD},
            "other": {"encryption": 1, "is_mounted": True, "password": "x"},
        }
        self.containers = [
            {"name": "jellyfin", "status": "running",
             "binds": [{"host_volume_file": "/secret/media", "mount_point": "/media"}]},
            {"name": "unrelated", "status": "stopped",
             "binds": [{"host_volume_file": "/docker/app", "mount_point": "/app"}]},
        ]
        self.sids: set[str] = set()
        self.logins = 0
        self.calls: list[tuple[str, str]] = []
        self.runs: list[int] = []
        self.down = False
        self.run_sets_running = True  # False simulates DSM status lag
        self.fail_containers = False

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def expire_sessions(self):
        self.sids.clear()

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        assert request.url.path == "/webapi/entry.cgi"
        form = dict(parse_qsl(request.content.decode()))
        api, method = form["api"], form["method"]
        self.calls.append((api, method))
        if api == "SYNO.API.Auth" and method == "login":
            if form.get("account") != DSM_USER or form.get("passwd") != DSM_PASS:
                return fail(400)
            self.logins += 1
            sid = f"sid-{self.logins}"
            self.sids.add(sid)
            return ok({"sid": sid})
        if form.get("_sid") not in self.sids:
            return fail(119)
        handler = getattr(self, f"_{api.replace('.', '_')}_{method}", None)
        if handler is None:
            return fail(103)
        return handler(form)

    def _SYNO_Core_TaskScheduler_list(self, form):
        return ok({"tasks": [dict(t) for t in self.tasks], "total": len(self.tasks)})

    def _SYNO_Core_TaskScheduler_run(self, form):
        for item in json.loads(form["tasks"]):
            task = next(t for t in self.tasks if t["id"] == item["id"])
            self.runs.append(task["id"])
            if self.run_sets_running:
                task["running"] = True
        return ok()

    def _SYNO_Core_Share_get(self, form):
        share = self.shares.get(form["name"])
        if share is None:
            return fail(3300)
        return ok({"shares": [{"name": form["name"], "encryption": share["encryption"],
                               "is_mounted": share["is_mounted"]}]})

    def _SYNO_Core_Share_Crypto_decrypt(self, form):
        share = self.shares[form["name"]]
        if form["password"] != share["password"]:
            return fail(3302)
        share["is_mounted"] = True
        return ok()

    def _SYNO_Core_Share_Crypto_encrypt(self, form):
        self.shares[form["name"]]["is_mounted"] = False
        return ok()

    def _SYNO_Docker_Container_list(self, form):
        if self.fail_containers:
            return fail(117)
        return ok({"containers": [{"name": c["name"], "status": c["status"]}
                                  for c in self.containers]})

    def _SYNO_Docker_Container_get(self, form):
        c = next(c for c in self.containers if c["name"] == form["name"])
        return ok({"profile": {"name": c["name"], "volume_bindings": c["binds"]}})
```

`tests/conftest.py`:

```python
import pytest

from nas_tasker.dsm import DsmClient

from .mock_dsm import DSM_PASS, DSM_USER, MockDsm


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mock_dsm():
    return MockDsm()


@pytest.fixture
async def dsm(mock_dsm):
    client = DsmClient("http://dsm.test", DSM_USER, DSM_PASS, transport=mock_dsm.transport())
    yield client
    await client.aclose()
```

- [ ] **Step 2: 실패하는 테스트 쓰기** — `tests/test_dsm_client.py`

```python
import pytest

from nas_tasker.dsm import DsmApiError, DsmClient, ForbiddenDsmCall
from nas_tasker.dsm.api import ALLOWED_METHODS
from nas_tasker.errors import ToolError

pytestmark = pytest.mark.anyio


async def test_list_tasks_parses_fields(dsm):
    tasks = await dsm.list_tasks()
    first = tasks[0]
    assert (first.id, first.name, first.enabled, first.status) == (1, "bot-gbrain-rebuild", True, "idle")
    assert first.last_exit_code == 0
    assert first.next_run_at == "2026-10-24 03:00"
    assert first.real_owner == "root"


async def test_missing_running_field_is_unknown(dsm, mock_dsm):
    del mock_dsm.tasks[0]["running"]
    assert (await dsm.list_tasks())[0].status == "unknown"


async def test_logs_in_once_and_reuses_sid(dsm, mock_dsm):
    await dsm.list_tasks()
    await dsm.list_tasks()
    assert mock_dsm.logins == 1


async def test_relogin_after_session_expiry(dsm, mock_dsm):
    await dsm.list_tasks()
    mock_dsm.expire_sessions()
    assert len(await dsm.list_tasks()) == 3
    assert mock_dsm.logins == 2


async def test_bad_dsm_credentials(mock_dsm):
    client = DsmClient("http://dsm.test", "nas-tasker", "wrong", transport=mock_dsm.transport())
    with pytest.raises(ToolError) as exc:
        await client.list_tasks()
    assert exc.value.code == "DSM_AUTH_FAILED"
    await client.aclose()


async def test_dsm_down_maps_to_unreachable(dsm, mock_dsm):
    mock_dsm.down = True
    with pytest.raises(ToolError) as exc:
        await dsm.list_tasks()
    assert exc.value.code == "DSM_UNREACHABLE"
    assert exc.value.__cause__ is None


async def test_dsm_error_code_is_carried(dsm, mock_dsm):
    with pytest.raises(DsmApiError) as exc:
        await dsm.get_share("missing")
    assert exc.value.dsm_error_code == 3300


@pytest.mark.parametrize(
    "api_name,method",
    [
        ("SYNO.Core.TaskScheduler", "create"),
        ("SYNO.Core.TaskScheduler", "set"),
        ("SYNO.Core.TaskScheduler", "delete"),
        ("SYNO.Core.TaskScheduler", "set_enable"),
        ("SYNO.Docker.Container", "start"),
        ("SYNO.Docker.Container", "stop"),
        ("SYNO.FileStation.Delete", "start"),
    ],
)
async def test_mutating_methods_are_blocked_before_sending(dsm, mock_dsm, api_name, method):
    with pytest.raises(ForbiddenDsmCall):
        await dsm.call(api_name, method, 1, {})
    assert mock_dsm.calls == []


def test_allowlist_has_no_mutating_task_or_docker_methods():
    for api_name, method in ALLOWED_METHODS:
        if api_name == "SYNO.Core.TaskScheduler":
            assert method in {"list", "run"}
        if api_name.startswith("SYNO.Docker"):
            assert method in {"list", "get"}
        assert not api_name.startswith("SYNO.FileStation")


async def test_share_mount_and_unmount(dsm, mock_dsm):
    assert (await dsm.get_share("secret")).mounted is False
    await dsm.mount_share("secret", "share-pw-Zq9!")
    assert (await dsm.get_share("secret")).mounted is True
    await dsm.unmount_share("secret")
    assert mock_dsm.shares["secret"]["is_mounted"] is False


async def test_wrong_share_password_error_code(dsm):
    with pytest.raises(DsmApiError) as exc:
        await dsm.mount_share("secret", "nope")
    assert exc.value.dsm_error_code == 3302


async def test_list_containers_using_share(dsm, mock_dsm):
    uses = await dsm.list_containers_using("secret")
    assert [(u.name, u.state, u.mount_path) for u in uses] == [("jellyfin", "running", "/media")]
    mock_dsm.containers[0]["binds"][0]["host_volume_file"] = "/volume1/secret/media"
    assert len(await dsm.list_containers_using("secret")) == 1


async def test_container_lookup_failure(dsm, mock_dsm):
    mock_dsm.fail_containers = True
    with pytest.raises(ToolError) as exc:
        await dsm.list_containers_using("secret")
    assert exc.value.code == "CONTAINER_LOOKUP_FAILED"


def test_repr_hides_credentials():
    client = DsmClient("http://dsm.test", "u", "dsm-secret-pw")
    assert "dsm-secret-pw" not in repr(client)
```

- [ ] **Step 3: 실패 확인**

Run: `.venv/bin/pytest tests/test_dsm_client.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nas_tasker.dsm'` (conftest import 오류)

- [ ] **Step 4: 구현**

`src/nas_tasker/dsm/api.py`:

```python
"""DSM Web API names and response fields used by nas-tasker.

PROVISIONAL: everything except SYNO.API.Auth is unofficial and must be checked
against the real NAS (plan Task 12). When it changes, change it here, in
client.py parsing, and in tests/mock_dsm.py together.
"""

ENTRY_PATH = "/webapi/entry.cgi"

AUTH = ("SYNO.API.Auth", 6)
TASK_SCHEDULER = ("SYNO.Core.TaskScheduler", 3)
SHARE = ("SYNO.Core.Share", 1)
SHARE_CRYPTO = ("SYNO.Core.Share.Crypto", 1)
DOCKER_CONTAINER = ("SYNO.Docker.Container", 1)

# The only (api, method) pairs the client will ever send. No create/set/
# delete/enable/disable, no Docker start/stop, no file APIs.
ALLOWED_METHODS = frozenset(
    {
        ("SYNO.API.Auth", "login"),
        ("SYNO.Core.TaskScheduler", "list"),
        ("SYNO.Core.TaskScheduler", "run"),
        ("SYNO.Core.Share", "get"),
        ("SYNO.Core.Share.Crypto", "decrypt"),  # mount an encrypted share
        ("SYNO.Core.Share.Crypto", "encrypt"),  # unmount an encrypted share
        ("SYNO.Docker.Container", "list"),
        ("SYNO.Docker.Container", "get"),
    }
)

# 106 session timeout, 107 duplicate login, 119 SID not found
SESSION_ERROR_CODES = frozenset({106, 107, 119})
WRONG_PASSWORD_CODES = frozenset({3302})  # PROVISIONAL
```

`src/nas_tasker/dsm/client.py`:

```python
import asyncio
import json
from dataclasses import dataclass

import httpx

from ..errors import ToolError
from . import api


class ForbiddenDsmCall(RuntimeError):
    """A code path tried to call a DSM method outside the allowlist."""


class DsmApiError(ToolError):
    def __init__(self, dsm_error_code: int | None):
        super().__init__("DSM_API_ERROR", "DSM API call failed", dsm_error_code=dsm_error_code)


@dataclass(frozen=True)
class TaskInfo:
    id: int
    name: str
    enabled: bool
    status: str  # idle | running | unknown
    last_run_at: str | None
    last_exit_code: int | None
    next_run_at: str | None
    real_owner: str | None


@dataclass(frozen=True)
class ShareInfo:
    name: str
    encrypted: bool
    mounted: bool


@dataclass(frozen=True)
class ContainerUse:
    name: str
    state: str
    mount_path: str


def _encode(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def _bad_response() -> ToolError:
    return ToolError("DSM_API_ERROR", "unexpected DSM response")


def _share_of(host_path: str) -> str:
    parts = host_path.strip("/").split("/")
    if len(parts) > 1 and parts[0].startswith("volume") and parts[0][6:].isdigit():
        return parts[1]
    return parts[0]


class DsmClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        verify_tls: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 15.0,
    ):
        self._http = httpx.AsyncClient(
            base_url=base_url, verify=verify_tls, transport=transport, timeout=timeout
        )
        self._username = username
        self._password = password
        self._sid: str | None = None
        self._login_lock = asyncio.Lock()

    def __repr__(self) -> str:
        return "DsmClient()"

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _post(self, api_name: str, method: str, version: int, params: dict) -> dict:
        form = {"api": api_name, "method": method, "version": str(version)}
        form.update({k: _encode(v) for k, v in params.items()})
        try:
            resp = await self._http.post(api.ENTRY_PATH, data=form)
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, ValueError):
            # from None: never chain the original error, which may carry request details
            raise ToolError("DSM_UNREACHABLE", "DSM is unreachable") from None
        if not isinstance(body, dict):
            raise _bad_response()
        return body

    async def _login(self) -> None:
        async with self._login_lock:
            if self._sid is not None:
                return
            name, version = api.AUTH
            body = await self._post(
                name,
                "login",
                version,
                {
                    "account": self._username,
                    "passwd": self._password,
                    "session": "nas-tasker",
                    "format": "sid",
                },
            )
            if not body.get("success"):
                code = (body.get("error") or {}).get("code")
                raise ToolError("DSM_AUTH_FAILED", "DSM login failed", dsm_error_code=code)
            try:
                self._sid = str(body["data"]["sid"])
            except (KeyError, TypeError):
                raise _bad_response() from None

    async def call(
        self, api_name: str, method: str, version: int, params: dict | None = None
    ) -> dict:
        if (api_name, method) not in api.ALLOWED_METHODS:
            raise ForbiddenDsmCall(f"{api_name}.{method}")
        for attempt in (1, 2):
            if self._sid is None:
                await self._login()
            body = await self._post(api_name, method, version, {**(params or {}), "_sid": self._sid})
            if body.get("success"):
                data = body.get("data")
                return data if isinstance(data, dict) else {}
            code = (body.get("error") or {}).get("code")
            if code in api.SESSION_ERROR_CODES and attempt == 1:
                self._sid = None
                continue
            raise DsmApiError(code)
        raise AssertionError("unreachable")

    # --- task scheduler -------------------------------------------------

    async def list_tasks(self) -> list[TaskInfo]:
        name, version = api.TASK_SCHEDULER
        data = await self.call(name, "list", version, {"offset": 0, "limit": 1000})
        try:
            return [self._parse_task(raw) for raw in data["tasks"]]
        except (KeyError, TypeError, ValueError):
            raise _bad_response() from None

    @staticmethod
    def _parse_task(raw: dict) -> TaskInfo:
        running = raw.get("running")
        status = "unknown" if running is None else ("running" if running else "idle")
        exit_code = raw.get("last_exit_code")
        return TaskInfo(
            id=int(raw["id"]),
            name=str(raw["name"]),
            enabled=bool(raw.get("enable")),
            status=status,
            last_run_at=raw.get("last_run_time"),
            last_exit_code=int(exit_code) if exit_code is not None else None,
            next_run_at=raw.get("next_trigger_time"),
            real_owner=raw.get("real_owner") or raw.get("owner"),
        )

    async def run_task(self, task: TaskInfo) -> None:
        name, version = api.TASK_SCHEDULER
        await self.call(
            name, "run", version, {"tasks": [{"id": task.id, "real_owner": task.real_owner}]}
        )

    # --- shares ---------------------------------------------------------

    async def get_share(self, share: str) -> ShareInfo:
        name, version = api.SHARE
        data = await self.call(name, "get", version, {"name": share})
        try:
            raw = data["shares"][0]
            return ShareInfo(
                name=str(raw["name"]),
                encrypted=bool(raw["encryption"]),
                mounted=bool(raw["is_mounted"]),
            )
        except (KeyError, IndexError, TypeError):
            raise _bad_response() from None

    async def mount_share(self, share: str, password: str) -> None:
        name, version = api.SHARE_CRYPTO
        await self.call(name, "decrypt", version, {"name": share, "password": password})

    async def unmount_share(self, share: str) -> None:
        name, version = api.SHARE_CRYPTO
        await self.call(name, "encrypt", version, {"name": share})

    # --- containers (read only) ------------------------------------------

    async def list_containers_using(self, share: str) -> list[ContainerUse]:
        name, version = api.DOCKER_CONTAINER
        try:
            listing = await self.call(name, "list", version, {"limit": -1, "offset": 0})
            found = []
            for item in listing["containers"]:
                detail = await self.call(name, "get", version, {"name": item["name"]})
                for binding in detail["profile"].get("volume_bindings", []):
                    if _share_of(binding["host_volume_file"]) == share:
                        found.append(
                            ContainerUse(
                                name=str(item["name"]),
                                state=str(item.get("status", "unknown")),
                                mount_path=str(binding["mount_point"]),
                            )
                        )
            return found
        except (ToolError, KeyError, TypeError, AttributeError):
            raise ToolError(
                "CONTAINER_LOOKUP_FAILED", "could not check which containers use the share"
            ) from None
```

`src/nas_tasker/dsm/__init__.py`:

```python
from .client import ContainerUse, DsmApiError, DsmClient, ForbiddenDsmCall, ShareInfo, TaskInfo

__all__ = ["ContainerUse", "DsmApiError", "DsmClient", "ForbiddenDsmCall", "ShareInfo", "TaskInfo"]
```

- [ ] **Step 5: 통과 확인**

Run: `.venv/bin/pytest -q`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add src/nas_tasker/dsm tests/mock_dsm.py tests/conftest.py tests/test_dsm_client.py
git commit -m "feat: allowlisted DSM client with re-login, and mock DSM for tests"
```

---

### Task 5: 작업 서비스 (`list_tasks`, `run_task`, `task_result`)

**Files:**
- Create: `src/nas_tasker/outputs.py`, `src/nas_tasker/tasks.py`
- Test: `tests/test_outputs.py`, `tests/test_tasks.py`

**Interfaces:**
- Consumes: `DsmClient`, `TaskInfo` (Task 4), `check_task_name`, `check_tail_lines`, `TASK_PREFIX` (Task 2)
- Produces:
  - `outputs.read_tail(output_dir: str, name: str, lines: int) -> tuple[str, bool]`. 돌려주는 값은 `(꼬리, truncated)`. 실패하면 `OUTPUT_NOT_FOUND`나 `NOT_ALLOWED`를 던진다.
  - `outputs.LOG_FILE = "latest.log"`, `outputs.MAX_TAIL_BYTES = 262144`
  - `tasks.iso(ts: float) -> str` (로컬 타임존이 붙은 ISO8601, 초 단위)
  - `tasks.TaskService(dsm, output_dir, clock=time.time, monotonic=time.monotonic)`
    - `.list_tasks() -> {"tasks": [...]}`
    - `.run_task(name) -> {"name", "started": True, "started_at"}`
    - `.task_result(name, tail_lines=None) -> {"name", "status", "exit_code", "finished_at", "output_tail", "truncated"}`

`task_result` 상태는 이렇게 정한다. 실행 중이면 `running`, 종료 코드가 없으면 `unknown`, 0이면 `succeeded`, 그 밖이면 `failed`.

- [ ] **Step 1: 실패하는 테스트 쓰기**

`tests/test_outputs.py`:

```python
import os

import pytest

from nas_tasker.errors import ToolError
from nas_tasker.outputs import MAX_TAIL_BYTES, read_tail


def write_log(root, name, text):
    d = root / name
    d.mkdir(exist_ok=True)
    (d / "latest.log").write_bytes(text if isinstance(text, bytes) else text.encode())


def code_of(fn):
    with pytest.raises(ToolError) as exc:
        fn()
    return exc.value.code


def test_tail_last_lines(tmp_path):
    write_log(tmp_path, "bot-a", "".join(f"line {i}\n" for i in range(10)))
    assert read_tail(str(tmp_path), "bot-a", 3) == ("line 7\nline 8\nline 9", True)
    assert read_tail(str(tmp_path), "bot-a", 100) == (
        "\n".join(f"line {i}" for i in range(10)),
        False,
    )


def test_missing_output(tmp_path):
    assert code_of(lambda: read_tail(str(tmp_path), "bot-a", 10)) == "OUTPUT_NOT_FOUND"
    (tmp_path / "bot-a").mkdir()
    assert code_of(lambda: read_tail(str(tmp_path), "bot-a", 10)) == "OUTPUT_NOT_FOUND"


def test_symlinked_task_dir_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "latest.log").write_text("secret\n")
    root = tmp_path / "out"
    root.mkdir()
    os.symlink(outside, root / "bot-a")
    assert code_of(lambda: read_tail(str(root), "bot-a", 10)) == "NOT_ALLOWED"


def test_symlinked_log_file_rejected(tmp_path):
    (tmp_path / "passwd").write_text("root:x:0:0\n")
    (tmp_path / "bot-a").mkdir()
    os.symlink(tmp_path / "passwd", tmp_path / "bot-a" / "latest.log")
    assert code_of(lambda: read_tail(str(tmp_path), "bot-a", 10)) == "NOT_ALLOWED"


def test_fifo_does_not_hang(tmp_path):
    (tmp_path / "bot-a").mkdir()
    os.mkfifo(tmp_path / "bot-a" / "latest.log")
    assert code_of(lambda: read_tail(str(tmp_path), "bot-a", 10)) == "OUTPUT_NOT_FOUND"


def test_large_file_is_capped(tmp_path):
    write_log(tmp_path, "bot-a", "x" * 100 + "\n" + ("y" * 99 + "\n") * 5000)
    tail, truncated = read_tail(str(tmp_path), "bot-a", 1000)
    assert truncated is True
    assert len(tail.encode()) <= MAX_TAIL_BYTES
    assert "x" not in tail


def test_single_huge_line_still_returns_something(tmp_path):
    write_log(tmp_path, "bot-a", "z" * (MAX_TAIL_BYTES * 2))
    tail, truncated = read_tail(str(tmp_path), "bot-a", 10)
    assert truncated is True
    assert len(tail) == MAX_TAIL_BYTES


def test_invalid_utf8_is_replaced(tmp_path):
    write_log(tmp_path, "bot-a", b"ok \xff\xfe done\n")
    tail, _ = read_tail(str(tmp_path), "bot-a", 10)
    assert tail.startswith("ok ") and tail.endswith(" done")
```

`tests/test_tasks.py`:

```python
import asyncio

import pytest

from nas_tasker.errors import ToolError
from nas_tasker.tasks import TaskService

pytestmark = pytest.mark.anyio


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def mono():
    return Clock()


@pytest.fixture
def service(dsm, tmp_path, mono):
    return TaskService(dsm, str(tmp_path), monotonic=mono)


async def code_of(coro):
    with pytest.raises(ToolError) as exc:
        await coro
    return exc.value.code


async def test_list_only_bot_tasks(service):
    result = await service.list_tasks()
    assert [t["name"] for t in result["tasks"]] == ["bot-gbrain-rebuild", "bot-disabled"]
    assert set(result["tasks"][0]) == {
        "name", "id", "enabled", "status", "last_run_at", "last_exit_code", "next_run_at"
    }


async def test_run_task_starts(service, mock_dsm):
    result = await service.run_task("bot-gbrain-rebuild")
    assert result["started"] is True and result["name"] == "bot-gbrain-rebuild"
    assert mock_dsm.runs == [1]


async def test_non_bot_task_cannot_run_or_be_read(service, mock_dsm):
    assert await code_of(service.run_task("nightly-backup")) == "NOT_ALLOWED"
    assert await code_of(service.task_result("nightly-backup")) == "NOT_ALLOWED"
    assert mock_dsm.runs == []


async def test_unknown_and_disabled(service):
    assert await code_of(service.run_task("bot-nope")) == "TASK_NOT_FOUND"
    assert await code_of(service.run_task("bot-disabled")) == "TASK_DISABLED"


async def test_duplicate_names_refused(service, mock_dsm):
    mock_dsm.tasks.append(dict(mock_dsm.tasks[0], id=9))
    assert await code_of(service.run_task("bot-gbrain-rebuild")) == "TASK_AMBIGUOUS"
    assert mock_dsm.runs == []


async def test_running_task_refused(service, mock_dsm):
    mock_dsm.tasks[0]["running"] = True
    assert await code_of(service.run_task("bot-gbrain-rebuild")) == "TASK_ALREADY_RUNNING"


async def test_concurrent_requests_start_once(service, mock_dsm):
    results = await asyncio.gather(
        *(service.run_task("bot-gbrain-rebuild") for _ in range(5)), return_exceptions=True
    )
    assert sum(1 for r in results if isinstance(r, dict)) == 1
    assert all(r.code == "TASK_ALREADY_RUNNING" for r in results if isinstance(r, ToolError))
    assert mock_dsm.runs == [1]


async def test_dsm_status_lag_still_blocks_second_start(service, mock_dsm, mono):
    mock_dsm.run_sets_running = False
    await service.run_task("bot-gbrain-rebuild")
    assert await code_of(service.run_task("bot-gbrain-rebuild")) == "TASK_ALREADY_RUNNING"
    mono.now += 31
    await service.run_task("bot-gbrain-rebuild")
    assert mock_dsm.runs == [1, 1]


async def test_task_result(service, tmp_path, mock_dsm):
    (tmp_path / "bot-gbrain-rebuild").mkdir()
    (tmp_path / "bot-gbrain-rebuild" / "latest.log").write_text("a\nb\nc\n")
    result = await service.task_result("bot-gbrain-rebuild", 2)
    assert result == {
        "name": "bot-gbrain-rebuild",
        "status": "succeeded",
        "exit_code": 0,
        "finished_at": "2026-10-22 03:00",
        "output_tail": "b\nc",
        "truncated": True,
    }
    mock_dsm.tasks[0]["last_exit_code"] = 2
    assert (await service.task_result("bot-gbrain-rebuild"))["status"] == "failed"
    mock_dsm.tasks[0]["running"] = True
    running = await service.task_result("bot-gbrain-rebuild")
    assert (running["status"], running["finished_at"]) == ("running", None)


async def test_task_result_bad_tail_lines(service):
    assert await code_of(service.task_result("bot-gbrain-rebuild", 5000)) == "INVALID_INPUT"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_outputs.py tests/test_tasks.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nas_tasker.outputs'`

- [ ] **Step 3: 구현**

`src/nas_tasker/outputs.py`:

```python
import os
import stat

from .errors import ToolError

LOG_FILE = "latest.log"  # <TASK_OUTPUT_DIR>/<task name>/latest.log (spec open question 4)
MAX_TAIL_BYTES = 256 * 1024


def read_tail(output_dir: str, name: str, lines: int) -> tuple[str, bool]:
    """Last `lines` lines of a task's log, read-only, never following symlinks.

    `name` must already be validated by check_task_name.
    """
    root = os.path.realpath(output_dir)
    task_dir = os.path.join(root, name)
    path = os.path.join(task_dir, LOG_FILE)
    if os.path.islink(task_dir):
        raise ToolError("NOT_ALLOWED", "symlinks are not allowed in the output folder")
    if os.path.commonpath([root, os.path.realpath(task_dir)]) != root:
        raise ToolError("NOT_ALLOWED", "path is outside the output folder")
    try:
        # O_NOFOLLOW rejects a symlinked log file, O_NONBLOCK avoids hanging on a FIFO
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise ToolError("OUTPUT_NOT_FOUND", "no output for this task yet") from None
    except NotADirectoryError:
        raise ToolError("OUTPUT_NOT_FOUND", "no output for this task yet") from None
    except OSError:
        if os.path.islink(path):
            raise ToolError("NOT_ALLOWED", "symlinks are not allowed in the output folder") from None
        raise ToolError("OUTPUT_NOT_FOUND", "output file cannot be read") from None
    with os.fdopen(fd, "rb") as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ToolError("OUTPUT_NOT_FOUND", "output is not a regular file")
        start = max(0, info.st_size - MAX_TAIL_BYTES)
        f.seek(start)
        data = f.read(MAX_TAIL_BYTES)
    all_lines = data.decode("utf-8", errors="replace").splitlines()
    if start > 0 and len(all_lines) > 1:
        all_lines = all_lines[1:]  # first line was cut by the byte cap
    truncated = start > 0 or len(all_lines) > lines
    return "\n".join(all_lines[-lines:]), truncated
```

`src/nas_tasker/tasks.py`:

```python
import asyncio
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime

from .dsm import DsmClient, TaskInfo
from .errors import ToolError
from .outputs import read_tail
from .validation import TASK_PREFIX, check_tail_lines, check_task_name

# DSM may report "idle" for a moment after "run". Refuse a second start inside this window.
RECENT_START_GRACE_SECONDS = 30.0


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _task_dict(task: TaskInfo) -> dict:
    return {
        "name": task.name,
        "id": task.id,
        "enabled": task.enabled,
        "status": task.status,
        "last_run_at": task.last_run_at,
        "last_exit_code": task.last_exit_code,
        "next_run_at": task.next_run_at,
    }


class TaskService:
    def __init__(
        self,
        dsm: DsmClient,
        output_dir: str,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self._dsm = dsm
        self._output_dir = output_dir
        self._clock = clock
        self._monotonic = monotonic
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._started: dict[str, float] = {}

    async def _find(self, name: str) -> TaskInfo:
        matches = [t for t in await self._dsm.list_tasks() if t.name == name]
        if not matches:
            raise ToolError("TASK_NOT_FOUND", "no such bot- task")
        if len(matches) > 1:
            raise ToolError("TASK_AMBIGUOUS", "more than one task has this name")
        return matches[0]

    async def list_tasks(self) -> dict:
        tasks = await self._dsm.list_tasks()
        return {"tasks": [_task_dict(t) for t in tasks if t.name.startswith(TASK_PREFIX)]}

    async def run_task(self, name: object) -> dict:
        name = check_task_name(name)
        async with self._locks[name]:
            task = await self._find(name)
            if not task.enabled:
                raise ToolError("TASK_DISABLED", "task is disabled")
            started = self._started.get(name)
            recently = started is not None and self._monotonic() - started < RECENT_START_GRACE_SECONDS
            if task.status == "running" or recently:
                raise ToolError("TASK_ALREADY_RUNNING", "task is already running")
            await self._dsm.run_task(task)
            self._started[name] = self._monotonic()
            return {"name": name, "started": True, "started_at": iso(self._clock())}

    async def task_result(self, name: object, tail_lines: object = None) -> dict:
        name = check_task_name(name)
        lines = check_tail_lines(tail_lines)
        task = await self._find(name)
        tail, truncated = read_tail(self._output_dir, name, lines)
        if task.status == "running":
            status = "running"
        elif task.last_exit_code is None:
            status = "unknown"
        else:
            status = "succeeded" if task.last_exit_code == 0 else "failed"
        return {
            "name": name,
            "status": status,
            "exit_code": task.last_exit_code,
            "finished_at": None if status == "running" else task.last_run_at,
            "output_tail": tail,
            "truncated": truncated,
        }
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_outputs.py tests/test_tasks.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/nas_tasker/outputs.py src/nas_tasker/tasks.py tests/test_outputs.py tests/test_tasks.py
git commit -m "feat: bot- task listing, guarded run and result tail"
```

---

### Task 6: 공유 폴더 서비스 (`mount_share`, `unmount_share`)

**Files:**
- Create: `src/nas_tasker/shares.py`
- Test: `tests/test_shares.py`

**Interfaces:**
- Consumes: `DsmClient`, `DsmApiError` (Task 4), `WRONG_PASSWORD_CODES` (Task 4), `OneTimeStore`, `new_token` (Task 2), `check_share_name` (Task 2), `Identity` (Task 2), `iso` (Task 5)
- Produces:
  - `shares.ShareService(dsm, allowlist, public_base_url, mount_ttl_seconds, confirm_ttl_seconds, clock=time.time)`
    - `.mount_share(name, request_id=None) -> dict` (봇 쪽 1단계와 상태 조회)
      - 새 요청을 만들면 `{"status": "pending_user_input", "request_id", "ui_url": "<base>/ui/mount/<id>", "expires_at"}`를 돌려준다.
      - `request_id`를 주면 `{"status": "mounted"|"failed"|"expired"}`나 대기 중인 응답을 돌려준다. 모르는 id도 `expired`로 답한다.
    - `.open_request(request_id) -> MountRequest`. 대기 중이 아니면 `REQUEST_EXPIRED`를 던진다.
    - `.complete_mount(request_id, csrf, password) -> "mounted"` (웹 UI 쪽 2단계)
    - `.unmount_share(name, confirm_token, identity: Identity) -> dict`
  - `shares.MountRequest(id, share, expires_at, csrf, status, attempts)`, `MAX_PASSWORD_ATTEMPTS = 5`

마운트 요청은 이렇게 움직인다.
- 시작은 `pending`이다. 제출하는 동안 `in_progress`가 되어, 같은 링크로 동시에 제출해도 DSM은 한 번만 불린다.
- 암호가 맞으면 `mounted`.
- 암호가 틀리면 `pending`으로 돌아가고, 5번째로 틀리면 `failed`.
- DSM에 닿지 못하면 `pending`으로 돌아가 다시 시도할 수 있다.
- 그 밖의 오류는 `failed`.
- 끝난 요청은 24시간 뒤 메모리에서 지운다.

- [ ] **Step 1: 실패하는 테스트 쓰기** — `tests/test_shares.py`

```python
import pytest

from nas_tasker.errors import ToolError
from nas_tasker.identity import Identity
from nas_tasker.shares import MAX_PASSWORD_ATTEMPTS, ShareService

from .mock_dsm import SHARE_PASSWORD

pytestmark = pytest.mark.anyio

ME = Identity("menkinds@gmail.com", "bearer+access")
OTHER = Identity("reservoir.crabs@gmail.com", "bearer+access")


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def shares(dsm, clock):
    return ShareService(
        dsm,
        frozenset({"secret", "other"}),
        "https://tasker.example.com",
        mount_ttl_seconds=600,
        confirm_ttl_seconds=300,
        clock=clock,
    )


async def code_of(coro):
    with pytest.raises(ToolError) as exc:
        await coro
    return exc.value.code


# --- mount ---------------------------------------------------------------


async def test_mount_step1_returns_link_not_password(shares, mock_dsm):
    result = await shares.mount_share("secret")
    assert result["status"] == "pending_user_input"
    assert result["ui_url"] == f"https://tasker.example.com/ui/mount/{result['request_id']}"
    assert ("SYNO.Core.Share.Crypto", "decrypt") not in mock_dsm.calls


async def test_mount_rejects_unlisted_and_mounted(shares):
    assert await code_of(shares.mount_share("homes")) == "SHARE_NOT_ALLOWED"
    assert await code_of(shares.mount_share("other")) == "ALREADY_MOUNTED"


async def test_mount_recall_reuses_pending_request(shares):
    first = await shares.mount_share("secret")
    again = await shares.mount_share("secret")
    assert again["request_id"] == first["request_id"]


async def test_complete_mount_then_status(shares, mock_dsm):
    req_id = (await shares.mount_share("secret"))["request_id"]
    req = shares.open_request(req_id)
    assert await shares.complete_mount(req_id, req.csrf, SHARE_PASSWORD) == "mounted"
    assert mock_dsm.shares["secret"]["is_mounted"] is True
    assert await shares.mount_share("secret", req_id) == {"status": "mounted"}
    # one-time: the link cannot be used again
    assert await code_of(shares.complete_mount(req_id, req.csrf, SHARE_PASSWORD)) == "REQUEST_EXPIRED"


async def test_bad_csrf_is_forbidden(shares, mock_dsm):
    req_id = (await shares.mount_share("secret"))["request_id"]
    assert await code_of(shares.complete_mount(req_id, "forged", SHARE_PASSWORD)) == "FORBIDDEN"
    assert ("SYNO.Core.Share.Crypto", "decrypt") not in mock_dsm.calls


async def test_wrong_password_can_retry_until_limit(shares):
    req_id = (await shares.mount_share("secret"))["request_id"]
    csrf = shares.open_request(req_id).csrf
    for _ in range(MAX_PASSWORD_ATTEMPTS - 1):
        assert await code_of(shares.complete_mount(req_id, csrf, "bad")) == "WRONG_PASSWORD"
        shares.open_request(req_id)  # still pending
    assert await code_of(shares.complete_mount(req_id, csrf, "bad")) == "WRONG_PASSWORD"
    assert await shares.mount_share("secret", req_id) == {"status": "failed"}


async def test_mount_request_expires(shares, clock):
    req_id = (await shares.mount_share("secret"))["request_id"]
    csrf = shares.open_request(req_id).csrf
    clock.now += 600
    assert await shares.mount_share("secret", req_id) == {"status": "expired"}
    assert await code_of(shares.complete_mount(req_id, csrf, SHARE_PASSWORD)) == "REQUEST_EXPIRED"
    fresh = await shares.mount_share("secret")
    assert fresh["request_id"] != req_id


async def test_unknown_request_id_reads_as_expired(shares):
    assert await shares.mount_share("secret", "lost-after-restart") == {"status": "expired"}
    with pytest.raises(ToolError) as exc:
        shares.open_request("lost-after-restart")
    assert exc.value.code == "REQUEST_EXPIRED"


async def test_request_id_bound_to_share(shares):
    req_id = (await shares.mount_share("secret"))["request_id"]
    assert await code_of(shares.mount_share("other", req_id)) == "INVALID_INPUT"


async def test_dsm_down_during_mount_allows_retry(shares, mock_dsm):
    req_id = (await shares.mount_share("secret"))["request_id"]
    csrf = shares.open_request(req_id).csrf
    mock_dsm.down = True
    assert await code_of(shares.complete_mount(req_id, csrf, SHARE_PASSWORD)) == "DSM_UNREACHABLE"
    mock_dsm.down = False
    assert await shares.complete_mount(req_id, csrf, SHARE_PASSWORD) == "mounted"


async def test_password_not_in_errors(shares):
    req_id = (await shares.mount_share("secret"))["request_id"]
    csrf = shares.open_request(req_id).csrf
    with pytest.raises(ToolError) as exc:
        await shares.complete_mount(req_id, csrf, "my-typo-password")
    err = exc.value
    assert "my-typo-password" not in repr(err) + str(err) + str(err.to_dict())
    assert err.__cause__ is None and err.__suppress_context__


# --- unmount -------------------------------------------------------------


async def test_unmount_two_steps(shares, mock_dsm):
    step1 = await shares.unmount_share("other", None, ME)
    assert step1["status"] == "confirmation_required"
    assert step1["containers_using"] == []
    assert mock_dsm.shares["other"]["is_mounted"] is True
    assert await shares.unmount_share("other", step1["confirm_token"], ME) == {"status": "unmounted"}
    assert mock_dsm.shares["other"]["is_mounted"] is False


async def test_unmount_lists_containers(shares, mock_dsm):
    mock_dsm.shares["secret"]["is_mounted"] = True
    step1 = await shares.unmount_share("secret", None, ME)
    assert step1["containers_using"] == [
        {"name": "jellyfin", "state": "running", "mount_path": "/media"}
    ]


async def test_unmount_token_rules(shares, mock_dsm, clock):
    mock_dsm.shares["secret"]["is_mounted"] = True
    token = (await shares.unmount_share("other", None, ME))["confirm_token"]
    assert await code_of(shares.unmount_share("other", "made-up", ME)) == "INVALID_CONFIRM_TOKEN"
    assert await code_of(shares.unmount_share("secret", token, ME)) == "INVALID_CONFIRM_TOKEN"
    assert await code_of(shares.unmount_share("other", token, OTHER)) == "INVALID_CONFIRM_TOKEN"
    assert await shares.unmount_share("other", token, ME) == {"status": "unmounted"}
    assert await code_of(shares.unmount_share("other", token, ME)) == "INVALID_CONFIRM_TOKEN"

    mock_dsm.shares["other"]["is_mounted"] = True
    late = (await shares.unmount_share("other", None, ME))["confirm_token"]
    clock.now += 300
    assert await code_of(shares.unmount_share("other", late, ME)) == "CONFIRM_TOKEN_EXPIRED"
    assert mock_dsm.shares["other"]["is_mounted"] is True


async def test_unmount_refuses_when_not_mounted_or_lookup_fails(shares, mock_dsm):
    assert await code_of(shares.unmount_share("secret", None, ME)) == "NOT_MOUNTED"
    mock_dsm.fail_containers = True
    assert await code_of(shares.unmount_share("other", None, ME)) == "CONTAINER_LOOKUP_FAILED"
    assert await code_of(shares.unmount_share("homes", None, ME)) == "SHARE_NOT_ALLOWED"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_shares.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nas_tasker.shares'`

- [ ] **Step 3: 구현** — `src/nas_tasker/shares.py`

```python
import hmac
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .dsm import DsmApiError, DsmClient
from .dsm.api import WRONG_PASSWORD_CODES
from .errors import ToolError
from .identity import Identity
from .tasks import iso
from .tokens import OneTimeStore, TokenExpired, TokenInvalid, new_token
from .validation import check_share_name

MAX_PASSWORD_ATTEMPTS = 5
KEEP_FINISHED_SECONDS = 24 * 3600


@dataclass
class MountRequest:
    id: str
    share: str
    expires_at: float
    csrf: str = field(default_factory=new_token, repr=False)
    status: str = "pending"  # pending | in_progress | mounted | failed
    attempts: int = 0


class ShareService:
    def __init__(
        self,
        dsm: DsmClient,
        allowlist: frozenset[str],
        public_base_url: str,
        mount_ttl_seconds: int,
        confirm_ttl_seconds: int,
        clock: Callable[[], float] = time.time,
    ):
        self._dsm = dsm
        self._allowlist = allowlist
        self._base_url = public_base_url
        self._mount_ttl = mount_ttl_seconds
        self._clock = clock
        self._mounts: dict[str, MountRequest] = {}
        self._confirm = OneTimeStore(confirm_ttl_seconds, clock)

    # --- mount: step 1 (bot) -------------------------------------------

    def _status_of(self, req: MountRequest) -> str:
        if req.status in ("pending", "in_progress") and self._clock() >= req.expires_at:
            return "expired"
        return req.status

    def _pending_response(self, req: MountRequest) -> dict:
        return {
            "status": "pending_user_input",
            "request_id": req.id,
            "ui_url": f"{self._base_url}/ui/mount/{req.id}",
            "expires_at": iso(req.expires_at),
        }

    async def mount_share(self, name: object, request_id: object = None) -> dict:
        name = check_share_name(name, self._allowlist)
        if request_id is not None:
            req = self._mounts.get(request_id) if isinstance(request_id, str) else None
            if req is None:
                return {"status": "expired"}  # unknown ids include ones lost on restart
            if req.share != name:
                raise ToolError("INVALID_INPUT", "request_id does not belong to this share")
            status = self._status_of(req)
            return self._pending_response(req) if status == "pending" else {"status": status}

        share = await self._dsm.get_share(name)
        if share.mounted:
            raise ToolError("ALREADY_MOUNTED", "share is already mounted")
        now = self._clock()
        self._mounts = {
            k: r for k, r in self._mounts.items() if r.expires_at + KEEP_FINISHED_SECONDS > now
        }
        for req in self._mounts.values():
            if req.share == name and self._status_of(req) == "pending":
                return self._pending_response(req)
        req = MountRequest(id=new_token(), share=name, expires_at=now + self._mount_ttl)
        self._mounts[req.id] = req
        return self._pending_response(req)

    # --- mount: step 2 (person, web UI) ----------------------------------

    def open_request(self, request_id: str) -> MountRequest:
        req = self._mounts.get(request_id)
        if req is None or self._status_of(req) != "pending":
            raise ToolError("REQUEST_EXPIRED", "this mount link is expired or already used")
        return req

    async def complete_mount(self, request_id: str, csrf: object, password: object) -> str:
        req = self.open_request(request_id)
        if not isinstance(csrf, str) or not hmac.compare_digest(csrf, req.csrf):
            raise ToolError("FORBIDDEN", "invalid form token")
        if not isinstance(password, str) or not password:
            raise ToolError("INVALID_INPUT", "password is required")
        req.status = "in_progress"
        req.attempts += 1
        try:
            await self._dsm.mount_share(req.share, password)
        except DsmApiError as err:
            if err.dsm_error_code in WRONG_PASSWORD_CODES:
                req.status = "failed" if req.attempts >= MAX_PASSWORD_ATTEMPTS else "pending"
                raise ToolError("WRONG_PASSWORD", "wrong password") from None
            req.status = "failed"
            raise
        except ToolError:
            req.status = "pending"  # DSM unreachable: the person may try again
            raise
        except BaseException:
            req.status = "failed"
            raise
        finally:
            password = None  # drop our reference right away  # noqa: F841
        req.status = "mounted"
        return req.status

    # --- unmount ---------------------------------------------------------

    async def unmount_share(self, name: object, confirm_token: object, identity: Identity) -> dict:
        name = check_share_name(name, self._allowlist)
        if confirm_token is None:
            share = await self._dsm.get_share(name)
            if not share.mounted:
                raise ToolError("NOT_MOUNTED", "share is not mounted")
            using = await self._dsm.list_containers_using(name)
            token, expires_at = self._confirm.issue({"share": name, "actor": identity.subject})
            return {
                "status": "confirmation_required",
                "share": name,
                "containers_using": [
                    {"name": c.name, "state": c.state, "mount_path": c.mount_path} for c in using
                ],
                "confirm_token": token,
                "expires_at": iso(expires_at),
            }
        try:
            self._confirm.take(
                confirm_token,
                lambda p: p["share"] == name and p["actor"] == identity.subject,
            )
        except TokenExpired:
            raise ToolError("CONFIRM_TOKEN_EXPIRED", "confirmation expired, start again") from None
        except TokenInvalid:
            raise ToolError("INVALID_CONFIRM_TOKEN", "invalid confirmation token") from None
        share = await self._dsm.get_share(name)
        if not share.mounted:
            raise ToolError("NOT_MOUNTED", "share is not mounted")
        await self._dsm.unmount_share(name)
        return {"status": "unmounted"}
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_shares.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/nas_tasker/shares.py tests/test_shares.py
git commit -m "feat: two-step share mount via browser link and confirmed unmount"
```

---

### Task 7: Cloudflare Access JWT와 베어러 토큰

**Files:**
- Create: `src/nas_tasker/auth.py`
- Create: `tests/jwt_helpers.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `ToolError` (Task 1)
- Produces:
  - `auth.KeyResolver = Callable[[str], key]`, `auth.jwks_resolver(issuer) -> KeyResolver` (`<issuer>/cdn-cgi/access/certs`)
  - `auth.AccessVerifier(issuer, audience, allowed_emails, key_resolver=None)`: `.verify(token: str | None) -> str`는 소문자 이메일을 돌려준다. 토큰이 없거나 틀리면 `UNAUTHENTICATED`, 허용 목록 밖이거나 이메일이 없으면 `FORBIDDEN`.
  - `auth.check_bearer(header: str | None, expected: str) -> None`. 실패하면 `UNAUTHENTICATED`.
  - 테스트용: `jwt_helpers.make_jwt(email=..., *, key=KEY, iss=ISSUER, aud=AUD, exp_in=300, **extra)`, `resolver`, `KEY`, `OTHER_KEY`, `ISSUER`, `AUD`

- [ ] **Step 1: 실패하는 테스트 쓰기**

`tests/jwt_helpers.py`:

```python
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://myteam.cloudflareaccess.com"
AUD = "aud-123"

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def resolver(token):
    return KEY.public_key()


def make_jwt(email="menkinds@gmail.com", *, key=KEY, iss=ISSUER, aud=AUD, exp_in=300, **extra):
    now = int(time.time())
    claims = {"iss": iss, "aud": [aud], "iat": now, "nbf": now, "exp": now + exp_in, **extra}
    if email is not None:
        claims["email"] = email
    return jwt.encode(claims, key, algorithm="RS256")
```

`tests/test_auth.py`:

```python
import time

import jwt
import pytest

from nas_tasker.auth import AccessVerifier, check_bearer
from nas_tasker.errors import ToolError

from .jwt_helpers import AUD, ISSUER, KEY, OTHER_KEY, make_jwt, resolver

ALLOWED = frozenset({"menkinds@gmail.com", "reservoir.crabs@gmail.com"})
BEARER = "t" * 40
GBRAIN_TOKEN = "g" * 40


@pytest.fixture
def verifier():
    return AccessVerifier(ISSUER, AUD, ALLOWED, key_resolver=resolver)


def code_of(fn):
    with pytest.raises(ToolError) as exc:
        fn()
    return exc.value.code


def test_valid_token_returns_lowercased_email(verifier):
    assert verifier.verify(make_jwt("Menkinds@Gmail.com")) == "menkinds@gmail.com"


@pytest.mark.parametrize(
    "token",
    [
        None,
        "",
        "not-a-jwt",
        make_jwt(key=OTHER_KEY),
        make_jwt(aud="other-aud"),
        make_jwt(iss="https://evil.cloudflareaccess.com"),
        make_jwt(exp_in=-10),
        make_jwt(nbf=int(time.time()) + 3600),
        jwt.encode({"iss": ISSUER, "aud": AUD, "email": "menkinds@gmail.com"}, KEY, algorithm="RS256"),
        jwt.encode({"iss": ISSUER, "aud": AUD, "email": "menkinds@gmail.com",
                    "exp": int(time.time()) + 60}, "h" * 40, algorithm="HS256"),
    ],
    ids=["none", "empty", "garbage", "bad-signature", "bad-aud", "bad-iss", "expired",
         "not-yet-valid", "no-exp", "hs256"],
)
def test_invalid_tokens_unauthenticated(verifier, token):
    assert code_of(lambda: verifier.verify(token)) == "UNAUTHENTICATED"


def test_email_outside_allowlist_forbidden(verifier):
    assert code_of(lambda: verifier.verify(make_jwt("stranger@gmail.com"))) == "FORBIDDEN"


def test_service_token_without_email_forbidden(verifier):
    token = make_jwt(None, common_name="abc.access")
    assert code_of(lambda: verifier.verify(token)) == "FORBIDDEN"


def test_key_lookup_failure_is_unauthenticated():
    def broken(token):
        raise OSError("jwks down")

    v = AccessVerifier(ISSUER, AUD, ALLOWED, key_resolver=broken)
    assert code_of(lambda: v.verify(make_jwt())) == "UNAUTHENTICATED"


def test_bearer_ok():
    check_bearer(f"Bearer {BEARER}", BEARER)
    check_bearer(f"bearer {BEARER}", BEARER)


@pytest.mark.parametrize(
    "header",
    [None, "", "Bearer", f"Basic {BEARER}", "Bearer wrong", f"Bearer {GBRAIN_TOKEN}", BEARER],
)
def test_bearer_rejected(header):
    assert code_of(lambda: check_bearer(header, BEARER)) == "UNAUTHENTICATED"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_auth.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nas_tasker.auth'`

- [ ] **Step 3: 구현** — `src/nas_tasker/auth.py`

```python
import hmac
from collections.abc import Callable
from typing import Any

import jwt

from .errors import ToolError

KeyResolver = Callable[[str], Any]  # JWT string -> public key


def jwks_resolver(issuer: str) -> KeyResolver:
    client = jwt.PyJWKClient(f"{issuer}/cdn-cgi/access/certs", cache_keys=True)
    return lambda token: client.get_signing_key_from_jwt(token).key


class AccessVerifier:
    """Verifies the Cloudflare Access JWT ourselves instead of trusting the edge."""

    def __init__(
        self,
        issuer: str,
        audience: str,
        allowed_emails: frozenset[str],
        key_resolver: KeyResolver | None = None,
    ):
        self._issuer = issuer
        self._audience = audience
        self._allowed = allowed_emails
        self._resolve = key_resolver or jwks_resolver(issuer)

    def verify(self, token: str | None) -> str:
        """Return the caller's lower-cased email or raise ToolError."""
        if not token:
            raise ToolError("UNAUTHENTICATED", "Cloudflare Access token is missing")
        try:
            claims = jwt.decode(
                token,
                self._resolve(token),
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
        except Exception:
            raise ToolError("UNAUTHENTICATED", "Cloudflare Access token is invalid") from None
        email = claims.get("email")
        # Service tokens carry no email, so they are refused (spec open question 6).
        if not isinstance(email, str) or email.lower() not in self._allowed:
            raise ToolError("FORBIDDEN", "this account is not allowed")
        return email.lower()


def check_bearer(header: str | None, expected: str) -> None:
    scheme, _, value = (header or "").partition(" ")
    if scheme.lower() != "bearer" or not value:
        raise ToolError("UNAUTHENTICATED", "bearer token is missing")
    if not hmac.compare_digest(value.strip().encode(), expected.encode()):
        raise ToolError("UNAUTHENTICATED", "bearer token is invalid")
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest tests/test_auth.py -q`
Expected: PASS (22 passed)

- [ ] **Step 5: 커밋**

```bash
git add src/nas_tasker/auth.py tests/jwt_helpers.py tests/test_auth.py
git commit -m "feat: verify Cloudflare Access JWT in-app and check bearer token"
```

---

### Task 8: Dispatcher, 인증 미들웨어, HTTP API, 앱 조립

**Files:**
- Create: `src/nas_tasker/dispatch.py`, `src/nas_tasker/middleware.py`, `src/nas_tasker/http_api.py`, `src/nas_tasker/app.py`
- Create: `tests/app_helpers.py`
- Test: `tests/test_http_api.py`

**Interfaces:**
- Consumes: Task 1–7의 모든 것
- Produces:
  - `dispatch.TOOL_ARGS: dict[tool, (required, optional)]`, `dispatch.CallContext(identity: Identity, client_ip: str | None)`, `dispatch.current_call: ContextVar[CallContext | None]`, `dispatch.outcome_of(code) -> "ok"|"denied"|"error"`
  - `dispatch.Dispatcher(tasks, shares, audit, limiter)`: `.call(tool: str, args: object, ctx: CallContext) -> (http_status, payload_dict)`. 모든 호출을 감사 로그에 남긴다. 모르는 도구는 `tool="unknown"`으로 기록한다.
  - `middleware.AuthMiddleware(app, verifier, bearer_token, audit)`: `/mcp`, `/api/`, `/ui/`가 아닌 경로는 404. 인증에 실패하면 감사 로그에 `tool="auth"`로 남긴다.
  - `http_api.build_api_router(dispatcher) -> APIRouter` (`POST /api/{tool}`, JSON 본문)
  - `app.build_app(settings, *, dsm_transport=None, key_resolver=None) -> ASGI app`
  - 테스트용: `app_helpers.make_client(tmp_path, mock_dsm) -> TestClient`, `bot_headers(email=..., bearer=BEARER)`, `ui_headers(email=...)`, `audit_lines(tmp_path)`, `mcp_call(client, tool, arguments=None, headers=None)`, `BEARER`, `MCP_HEADERS`

- [ ] **Step 1: 테스트 도우미와 실패하는 테스트 쓰기**

`tests/app_helpers.py`:

```python
import json

from starlette.testclient import TestClient

from nas_tasker.app import build_app
from nas_tasker.config import load_settings

from .jwt_helpers import make_jwt, resolver
from .mock_dsm import DSM_PASS, DSM_USER

BEARER = "t" * 40
MCP_HEADERS = {"accept": "application/json, text/event-stream"}


def make_client(tmp_path, mock_dsm) -> TestClient:
    out = tmp_path / "output"
    out.mkdir(exist_ok=True)
    settings = load_settings({
        "DSM_URL": "http://dsm.test",
        "DSM_USERNAME": DSM_USER,
        "DSM_PASSWORD": DSM_PASS,
        "TASK_OUTPUT_DIR": str(out),
        "SHARE_ALLOWLIST": "secret,other",
        "MCP_BEARER_TOKEN": BEARER,
        "CF_ACCESS_TEAM_DOMAIN": "myteam.cloudflareaccess.com",
        "CF_ACCESS_AUD": "aud-123",
        "ALLOWED_EMAILS": "menkinds@gmail.com,reservoir.crabs@gmail.com",
        "PUBLIC_BASE_URL": "https://tasker.example.com",
        "AUDIT_LOG_PATH": str(tmp_path / "audit.jsonl"),
    })
    app = build_app(settings, dsm_transport=mock_dsm.transport(), key_resolver=resolver)
    return TestClient(app, base_url="https://tasker.example.com")


def bot_headers(email="menkinds@gmail.com", bearer=BEARER) -> dict:
    return {"cf-access-jwt-assertion": make_jwt(email), "authorization": f"Bearer {bearer}",
            "cf-connecting-ip": "203.0.113.7"}


def ui_headers(email="menkinds@gmail.com") -> dict:
    return {"cf-access-jwt-assertion": make_jwt(email)}


def audit_lines(tmp_path) -> list[dict]:
    path = tmp_path / "audit.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def mcp_call(client, tool, arguments=None, headers=None):
    resp = client.post(
        "/mcp/",
        headers={**MCP_HEADERS, **(headers or bot_headers())},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": tool, "arguments": arguments or {}}},
    )
    return resp
```

`tests/test_http_api.py`:

```python
import pytest

from .app_helpers import BEARER, audit_lines, bot_headers, make_client
from .jwt_helpers import OTHER_KEY, make_jwt


@pytest.fixture
def client(tmp_path, mock_dsm):
    with make_client(tmp_path, mock_dsm) as c:
        yield c


def test_list_tasks_over_http(client, tmp_path):
    resp = client.post("/api/list_tasks", headers=bot_headers(), json={})
    assert resp.status_code == 200
    assert [t["name"] for t in resp.json()["tasks"]] == ["bot-gbrain-rebuild", "bot-disabled"]
    [entry] = audit_lines(tmp_path)
    assert entry["tool"] == "list_tasks" and entry["result"] == "ok"
    assert entry["actor_email"] == "menkinds@gmail.com"
    assert entry["auth_method"] == "bearer+access"
    assert entry["client_ip"] == "203.0.113.7"
    assert set(entry) >= {"ts", "request_id", "args", "error_code", "duration_ms", "dsm_error_code"}


def test_run_task_and_error_shape(client, mock_dsm):
    ok = client.post("/api/run_task", headers=bot_headers(), json={"name": "bot-gbrain-rebuild"})
    assert ok.status_code == 200 and ok.json()["started"] is True
    denied = client.post("/api/run_task", headers=bot_headers(), json={"name": "nightly-backup"})
    assert denied.status_code == 403
    assert denied.json() == {"error": {"code": "NOT_ALLOWED", "message": "only bot- tasks are allowed"}}
    assert mock_dsm.runs == [1]


@pytest.mark.parametrize(
    "headers,status,code",
    [
        ({}, 401, "UNAUTHENTICATED"),
        ({"authorization": f"Bearer {BEARER}"}, 401, "UNAUTHENTICATED"),
        ({"cf-access-jwt-assertion": make_jwt()}, 401, "UNAUTHENTICATED"),
        ({"cf-access-jwt-assertion": make_jwt(), "authorization": "Bearer " + "g" * 40}, 401, "UNAUTHENTICATED"),
        ({"cf-access-jwt-assertion": make_jwt(key=OTHER_KEY), "authorization": f"Bearer {BEARER}"}, 401, "UNAUTHENTICATED"),
        ({"cf-access-jwt-assertion": make_jwt("x@evil.com"), "authorization": f"Bearer {BEARER}"}, 403, "FORBIDDEN"),
    ],
    ids=["nothing", "bearer-only", "jwt-only", "gbrain-token", "bad-jwt", "stranger"],
)
def test_bot_paths_need_jwt_and_bearer(client, tmp_path, mock_dsm, headers, status, code):
    for path in ("/api/list_tasks", "/mcp/"):
        resp = client.post(path, headers=headers, json={})
        assert resp.status_code == status
        assert resp.json()["error"]["code"] == code
    assert mock_dsm.calls == []
    entries = audit_lines(tmp_path)
    assert len(entries) == 2 and all(e["tool"] == "auth" for e in entries)
    assert all(e["result"] == "denied" for e in entries)


def test_unknown_paths_are_404_without_auth(client):
    for path in ("/", "/docs", "/openapi.json", "/webapi/entry.cgi"):
        assert client.get(path).status_code == 404


def test_bad_arguments(client, tmp_path):
    h = bot_headers()
    assert client.post("/api/run_task", headers=h, json={}).json()["error"]["code"] == "INVALID_INPUT"
    assert client.post("/api/run_task", headers=h, json={"name": "bot-a", "x": 1}).status_code == 400
    assert client.post("/api/run_task", headers=h, content=b"{not json").status_code == 400
    assert client.post("/api/run_task", headers=h, json=["bot-a"]).status_code == 400
    assert client.post("/api/delete_task", headers=h, json={}).status_code == 400
    assert {e["tool"] for e in audit_lines(tmp_path)} == {"run_task", "unknown"}


def test_rate_limit_run_task(client):
    h = bot_headers()
    codes = [
        client.post("/api/run_task", headers=h, json={"name": "bot-nope"}).json()["error"]["code"]
        for _ in range(11)
    ]
    assert codes[:10] == ["TASK_NOT_FOUND"] * 10
    assert codes[10] == "RATE_LIMITED"


def test_dsm_down_is_clean_502(client, mock_dsm):
    mock_dsm.down = True
    resp = client.post("/api/list_tasks", headers=bot_headers(), json={})
    assert resp.status_code == 502
    assert resp.json() == {"error": {"code": "DSM_UNREACHABLE", "message": "DSM is unreachable"}}


def test_unmount_confirm_token_not_logged(client, tmp_path):
    step1 = client.post("/api/unmount_share", headers=bot_headers(), json={"name": "other"}).json()
    token = step1["confirm_token"]
    done = client.post("/api/unmount_share", headers=bot_headers(),
                       json={"name": "other", "confirm_token": token})
    assert done.json() == {"status": "unmounted"}
    text = (tmp_path / "audit.jsonl").read_text()
    assert token not in text and "confirm_token_hash" in text


def test_unmount_token_bound_to_caller(client):
    token = client.post("/api/unmount_share", headers=bot_headers(),
                        json={"name": "other"}).json()["confirm_token"]
    other = client.post("/api/unmount_share", headers=bot_headers("reservoir.crabs@gmail.com"),
                        json={"name": "other", "confirm_token": token})
    assert other.json()["error"]["code"] == "INVALID_CONFIRM_TOKEN"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_http_api.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'nas_tasker.app'`

- [ ] **Step 3: 구현**

`src/nas_tasker/dispatch.py`:

```python
import logging
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass

from .audit import AuditLogger, safe_args
from .errors import DENIED_CODES, ToolError
from .identity import Identity
from .ratelimit import RateLimiter
from .shares import ShareService
from .tasks import TaskService

log = logging.getLogger("nas_tasker")

# tool -> (required keys, optional keys)
TOOL_ARGS = {
    "list_tasks": ((), ()),
    "run_task": (("name",), ()),
    "task_result": (("name",), ("tail_lines",)),
    "mount_share": (("name",), ("request_id",)),
    "unmount_share": (("name",), ("confirm_token",)),
}


@dataclass(frozen=True)
class CallContext:
    identity: Identity
    client_ip: str | None


# Set by AuthMiddleware for each authenticated request; read by HTTP and MCP handlers.
current_call: ContextVar[CallContext | None] = ContextVar("current_call", default=None)


def outcome_of(code: str | None) -> str:
    if code is None:
        return "ok"
    return "denied" if code in DENIED_CODES else "error"


def _check_args(tool: str, args: object) -> dict:
    if tool not in TOOL_ARGS:
        raise ToolError("INVALID_INPUT", "unknown tool")
    if not isinstance(args, dict):
        raise ToolError("INVALID_INPUT", "arguments must be a JSON object")
    required, optional = TOOL_ARGS[tool]
    unknown = set(args) - set(required) - set(optional)
    if unknown:
        raise ToolError("INVALID_INPUT", "unknown argument(s): " + ", ".join(sorted(unknown)))
    missing = [k for k in required if k not in args]
    if missing:
        raise ToolError("INVALID_INPUT", "missing argument(s): " + ", ".join(missing))
    return args


class Dispatcher:
    """Single entry point for the five tools, shared by the HTTP API and MCP."""

    def __init__(
        self,
        tasks: TaskService,
        shares: ShareService,
        audit: AuditLogger,
        limiter: RateLimiter,
    ):
        self._tasks = tasks
        self._shares = shares
        self._audit = audit
        self._limiter = limiter

    async def call(self, tool: str, args: object, ctx: CallContext) -> tuple[int, dict]:
        started = time.monotonic()
        code = dsm_code = None
        try:
            checked = _check_args(tool, args)
            self._limiter.check(ctx.identity.subject, tool)
            status, payload = 200, await self._invoke(tool, checked, ctx.identity)
        except ToolError as err:
            code, dsm_code = err.code, err.dsm_error_code
            status, payload = err.http_status, err.to_dict()
        except Exception as err:  # never echo internals to the caller
            log.error("internal error in %s: %s", tool, type(err).__name__)
            code = "INTERNAL"
            status, payload = 500, ToolError("INTERNAL", "internal error").to_dict()
        self._audit.record(
            request_id=uuid.uuid4().hex,
            actor_email=ctx.identity.subject,
            auth_method=ctx.identity.auth_method,
            client_ip=ctx.client_ip,
            tool=tool if tool in TOOL_ARGS else "unknown",
            args=safe_args(tool, args),
            result=outcome_of(code),
            error_code=code,
            duration_ms=round((time.monotonic() - started) * 1000),
            dsm_error_code=dsm_code,
        )
        return status, payload

    async def _invoke(self, tool: str, args: dict, identity: Identity) -> dict:
        if tool == "list_tasks":
            return await self._tasks.list_tasks()
        if tool == "run_task":
            return await self._tasks.run_task(args["name"])
        if tool == "task_result":
            return await self._tasks.task_result(args["name"], args.get("tail_lines"))
        if tool == "mount_share":
            return await self._shares.mount_share(args["name"], args.get("request_id"))
        return await self._shares.unmount_share(args["name"], args.get("confirm_token"), identity)
```

`src/nas_tasker/middleware.py`:

```python
import json
import uuid

from .audit import AuditLogger
from .auth import AccessVerifier, check_bearer
from .dispatch import CallContext, current_call, outcome_of
from .errors import ToolError
from .identity import Identity

BOT_PREFIXES = ("/mcp", "/api/")
UI_PREFIX = "/ui/"


def _headers(scope) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}


async def _send_json(send, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")],
    })
    await send({"type": "http.response.body", "body": body})


class AuthMiddleware:
    """Access JWT on every path; bearer token too on bot paths. Everything else is 404."""

    def __init__(self, app, verifier: AccessVerifier, bearer_token: str, audit: AuditLogger):
        self._app = app
        self._verifier = verifier
        self._bearer = bearer_token
        self._audit = audit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        path = scope["path"]
        is_bot = path == "/mcp" or path.startswith(BOT_PREFIXES)
        if not is_bot and not path.startswith(UI_PREFIX):
            await _send_json(send, 404, ToolError("INVALID_INPUT", "not found").to_dict())
            return
        headers = _headers(scope)
        client_ip = headers.get("cf-connecting-ip")
        email = None
        try:
            email = self._verifier.verify(headers.get("cf-access-jwt-assertion"))
            if is_bot:
                check_bearer(headers.get("authorization"), self._bearer)
        except ToolError as err:
            self._audit.record(
                request_id=uuid.uuid4().hex,
                actor_email=email,
                auth_method="bearer+access" if is_bot else "ui",
                client_ip=client_ip,
                tool="auth",
                args={"path": "/mcp" if path.startswith("/mcp") else path.split("/")[1]},
                result=outcome_of(err.code),
                error_code=err.code,
                duration_ms=0,
                dsm_error_code=None,
            )
            await _send_json(send, err.http_status, err.to_dict())
            return
        identity = Identity(email, "bearer+access" if is_bot else "ui")
        token = current_call.set(CallContext(identity, client_ip))
        try:
            await self._app(scope, receive, send)
        finally:
            current_call.reset(token)
```

`src/nas_tasker/http_api.py`:

```python
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .dispatch import Dispatcher, current_call


def build_api_router(dispatcher: Dispatcher) -> APIRouter:
    router = APIRouter()

    @router.post("/api/{tool}")
    async def call_tool(tool: str, request: Request) -> JSONResponse:
        raw = await request.body()
        try:
            args = json.loads(raw) if raw else {}
        except ValueError:
            args = None  # the dispatcher rejects non-objects with INVALID_INPUT
        status, payload = await dispatcher.call(tool, args, current_call.get())
        return JSONResponse(payload, status_code=status, headers={"cache-control": "no-store"})

    return router
```

`src/nas_tasker/app.py` (이 단계에서는 MCP와 UI가 없다. Task 9와 10에서 더한다):

```python
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from .audit import AuditLogger
from .auth import AccessVerifier, KeyResolver
from .config import Settings
from .dispatch import Dispatcher
from .dsm import DsmClient
from .http_api import build_api_router
from .middleware import AuthMiddleware
from .ratelimit import RateLimiter
from .shares import ShareService
from .tasks import TaskService


def build_app(
    settings: Settings,
    *,
    dsm_transport: httpx.AsyncBaseTransport | None = None,
    key_resolver: KeyResolver | None = None,
):
    dsm = DsmClient(
        settings.dsm_url,
        settings.dsm_username,
        settings.dsm_password,
        verify_tls=settings.dsm_verify_tls,
        transport=dsm_transport,
    )
    audit = AuditLogger(settings.audit_log_path)
    tasks = TaskService(dsm, settings.task_output_dir)
    shares = ShareService(
        dsm,
        settings.share_allowlist,
        settings.public_base_url,
        settings.mount_request_ttl_seconds,
        settings.confirm_token_ttl_seconds,
    )
    dispatcher = Dispatcher(tasks, shares, audit, RateLimiter())

    @asynccontextmanager
    async def lifespan(app):
        yield
        await dsm.aclose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(build_api_router(dispatcher))

    verifier = AccessVerifier(
        settings.cf_access_issuer,
        settings.cf_access_aud,
        settings.allowed_emails,
        key_resolver=key_resolver,
    )
    return AuthMiddleware(app, verifier, settings.mcp_bearer_token, audit)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest -q`
Expected: 전부 PASS (`test_http_api.py` 14 passed)

- [ ] **Step 5: 커밋**

```bash
git add src/nas_tasker/dispatch.py src/nas_tasker/middleware.py src/nas_tasker/http_api.py src/nas_tasker/app.py tests/app_helpers.py tests/test_http_api.py
git commit -m "feat: auth middleware, audited dispatcher and HTTP API"
```

---

### Task 9: MCP 엔드포인트 (도구 5개)

**Files:**
- Create: `src/nas_tasker/mcp_server.py`
- Modify: `src/nas_tasker/app.py`
- Test: `tests/test_mcp.py`

**Interfaces:**
- Consumes: `Dispatcher`, `current_call` (Task 8)
- Produces: `mcp_server.build_mcp(dispatcher) -> MCPServer`. `<PUBLIC_BASE_URL>/mcp/`에서 stateless JSON(`initialize` 없이 `tools/call`)로 동작한다. 도구 결과는 Dispatcher가 만든 payload를 JSON 텍스트로 담는다. 오류도 `{"error": ...}` 형태로 담고 `isError`는 false다.

참고: `mcp` 2.x에서 `FastMCP`의 이름이 `MCPServer`(`mcp.server.mcpserver`)로 바뀌었다. `streamable_http_app()`이 `stateless_http`, `json_response`, `streamable_http_path`, `transport_security`를 인자로 받는다. Host 헤더 검사(DNS rebinding 보호)는 끈다. Access JWT와 베어러 토큰 검사가 그 역할을 대신한다.

- [ ] **Step 1: 실패하는 테스트 쓰기** — `tests/test_mcp.py`

```python
import json

import pytest

from .app_helpers import MCP_HEADERS, audit_lines, bot_headers, make_client, mcp_call


@pytest.fixture
def client(tmp_path, mock_dsm):
    with make_client(tmp_path, mock_dsm) as c:
        yield c


def result_of(resp) -> dict:
    assert resp.status_code == 200
    body = resp.json()["result"]
    return json.loads(body["content"][0]["text"])


def test_exactly_five_tools(client):
    resp = client.post("/mcp/", headers={**MCP_HEADERS, **bot_headers()},
                       json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    names = sorted(t["name"] for t in resp.json()["result"]["tools"])
    assert names == ["list_tasks", "mount_share", "run_task", "task_result", "unmount_share"]


def test_list_tasks_over_mcp_is_audited_with_identity(client, tmp_path):
    data = result_of(mcp_call(client, "list_tasks", headers=bot_headers("reservoir.crabs@gmail.com")))
    assert [t["name"] for t in data["tasks"]] == ["bot-gbrain-rebuild", "bot-disabled"]
    [entry] = audit_lines(tmp_path)
    assert (entry["tool"], entry["actor_email"]) == ("list_tasks", "reservoir.crabs@gmail.com")


def test_errors_come_back_as_json(client, mock_dsm):
    data = result_of(mcp_call(client, "run_task", {"name": "nightly-backup"}))
    assert data["error"]["code"] == "NOT_ALLOWED"
    assert mock_dsm.runs == []


def test_mount_share_over_mcp_returns_link(client):
    data = result_of(mcp_call(client, "mount_share", {"name": "secret"}))
    assert data["status"] == "pending_user_input"
    assert data["ui_url"].startswith("https://tasker.example.com/ui/mount/")
    again = result_of(mcp_call(client, "mount_share",
                               {"name": "secret", "request_id": data["request_id"]}))
    assert again["status"] == "pending_user_input"


def test_mcp_without_auth(client, mock_dsm):
    resp = mcp_call(client, "list_tasks", headers={"authorization": "Bearer " + "t" * 40})
    assert resp.status_code == 401
    assert mock_dsm.calls == []
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_mcp.py -q`
Expected: FAIL. `/mcp/` 경로가 없어서 404이고, `test_exactly_five_tools`에서 `KeyError`나 JSON 오류가 난다.

- [ ] **Step 3: 구현** — `src/nas_tasker/mcp_server.py`

```python
from mcp.server.mcpserver import MCPServer

from .dispatch import Dispatcher, current_call

INSTRUCTIONS = (
    "Runs pre-approved Synology DSM tasks (names start with 'bot-') and mounts or "
    "unmounts allowlisted encrypted shares. Never ask the user for a share password in "
    "chat: mount_share returns a link the user opens in a browser. unmount_share needs "
    "two calls: first without confirm_token, show the user containers_using, then call "
    "again with the confirm_token once the user agrees."
)


def build_mcp(dispatcher: Dispatcher) -> MCPServer:
    mcp = MCPServer("nas-tasker", instructions=INSTRUCTIONS)

    async def call(tool: str, args: dict) -> dict:
        _, payload = await dispatcher.call(tool, args, current_call.get())
        return payload

    @mcp.tool()
    async def list_tasks() -> dict:
        """List DSM scheduled tasks whose names start with 'bot-'."""
        return await call("list_tasks", {})

    @mcp.tool()
    async def run_task(name: str) -> dict:
        """Start a 'bot-' task now. Refused if it is disabled or already running."""
        return await call("run_task", {"name": name})

    @mcp.tool()
    async def task_result(name: str, tail_lines: int = 100) -> dict:
        """Last run status of a 'bot-' task and the tail of its log (1-1000 lines)."""
        return await call("task_result", {"name": name, "tail_lines": tail_lines})

    @mcp.tool()
    async def mount_share(name: str, request_id: str | None = None) -> dict:
        """Start mounting an allowlisted encrypted share. Returns a ui_url for the user to
        type the password in. Call again with request_id to check the result."""
        args = {"name": name} if request_id is None else {"name": name, "request_id": request_id}
        return await call("mount_share", args)

    @mcp.tool()
    async def unmount_share(name: str, confirm_token: str | None = None) -> dict:
        """Unmount an allowlisted encrypted share in two steps (see server instructions)."""
        args = {"name": name}
        if confirm_token is not None:
            args["confirm_token"] = confirm_token
        return await call("unmount_share", args)

    return mcp
```

`src/nas_tasker/app.py`를 고친다. import를 더한다:

```python
from mcp.server.transport_security import TransportSecuritySettings

from .mcp_server import build_mcp
```

`dispatcher = Dispatcher(...)` 줄 바로 아래부터 `app.include_router(build_api_router(dispatcher))`까지를 이렇게 바꾼다:

```python
    dispatcher = Dispatcher(tasks, shares, audit, RateLimiter())

    mcp = build_mcp(dispatcher)
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/",
        json_response=True,
        stateless_http=True,
        # Host checks are replaced by Access JWT + bearer checks in AuthMiddleware.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield
        await dsm.aclose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(build_api_router(dispatcher))
    app.mount("/mcp", mcp_app)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest -q`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add src/nas_tasker/mcp_server.py src/nas_tasker/app.py tests/test_mcp.py
git commit -m "feat: MCP endpoint exposing exactly five tools"
```

---

### Task 10: 비밀번호 입력 웹 UI

**Files:**
- Create: `src/nas_tasker/ui.py`
- Modify: `src/nas_tasker/app.py`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `ShareService.open_request/complete_mount` (Task 6), `AuditLogger`, `safe_args` (Task 3), `current_call`, `outcome_of` (Task 8), `iso` (Task 5)
- Produces: `ui.build_ui_router(shares, audit) -> APIRouter`
  - `GET /ui/mount/{id}`: 폼을 보여 준다. 만료됐거나 모르는 id면 410 안내 페이지.
  - `POST /ui/mount/{id}`: 폼 필드는 `csrf`, `password`. 감사 로그에 `tool="ui_mount"`, `auth_method="ui"`로 남는다.
  - 모든 응답에 `no-store`, `X-Frame-Options: DENY`, CSP, `Referrer-Policy: no-referrer`를 붙인다.

- [ ] **Step 1: 실패하는 테스트 쓰기** — `tests/test_ui.py`

```python
import logging
import re

import pytest

from .app_helpers import audit_lines, bot_headers, make_client, ui_headers
from .mock_dsm import SHARE_PASSWORD


@pytest.fixture
def client(tmp_path, mock_dsm):
    with make_client(tmp_path, mock_dsm) as c:
        yield c


def start_mount(client) -> str:
    return client.post("/api/mount_share", headers=bot_headers(), json={"name": "secret"}).json()["request_id"]


def open_form(client, request_id) -> str:
    resp = client.get(f"/ui/mount/{request_id}", headers=ui_headers())
    assert resp.status_code == 200
    return re.search(r'name="csrf" value="([^"]+)"', resp.text).group(1)


def test_ui_path_needs_only_jwt_not_bearer(client):
    assert client.get("/ui/mount/nope", headers=ui_headers()).status_code == 410
    assert client.get("/ui/mount/nope").status_code == 401


def test_form_page(client):
    request_id = start_mount(client)
    resp = client.get(f"/ui/mount/{request_id}", headers=ui_headers())
    assert 'type="password"' in resp.text and 'autocomplete="off"' in resp.text
    assert 'method="post"' in resp.text
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["cache-control"] == "no-store"


def test_mount_end_to_end(client, mock_dsm, tmp_path):
    request_id = start_mount(client)
    csrf = open_form(client, request_id)
    resp = client.post(f"/ui/mount/{request_id}", headers=ui_headers(),
                       data={"csrf": csrf, "password": SHARE_PASSWORD})
    assert resp.status_code == 200 and "마운트 완료" in resp.text
    assert mock_dsm.shares["secret"]["is_mounted"] is True
    status = client.post("/api/mount_share", headers=bot_headers(),
                         json={"name": "secret", "request_id": request_id}).json()
    assert status == {"status": "mounted"}
    ui_entry = [e for e in audit_lines(tmp_path) if e["tool"] == "ui_mount"][0]
    assert ui_entry["auth_method"] == "ui" and ui_entry["result"] == "ok"
    assert ui_entry["args"]["name"] == "secret"


def test_double_submit_mounts_once(client, mock_dsm):
    request_id = start_mount(client)
    csrf = open_form(client, request_id)
    form = {"csrf": csrf, "password": SHARE_PASSWORD}
    client.post(f"/ui/mount/{request_id}", headers=ui_headers(), data=form)
    second = client.post(f"/ui/mount/{request_id}", headers=ui_headers(), data=form)
    assert second.status_code == 410
    assert mock_dsm.calls.count(("SYNO.Core.Share.Crypto", "decrypt")) == 1


def test_csrf_required(client, mock_dsm):
    request_id = start_mount(client)
    resp = client.post(f"/ui/mount/{request_id}", headers=ui_headers(),
                       data={"csrf": "forged", "password": SHARE_PASSWORD})
    assert resp.status_code == 403
    assert ("SYNO.Core.Share.Crypto", "decrypt") not in mock_dsm.calls


def test_get_never_mounts(client, mock_dsm):
    request_id = start_mount(client)
    client.get(f"/ui/mount/{request_id}?password={SHARE_PASSWORD}", headers=ui_headers())
    assert ("SYNO.Core.Share.Crypto", "decrypt") not in mock_dsm.calls


def test_expired_or_unknown_link_is_friendly(client):
    resp = client.get("/ui/mount/does-not-exist", headers=ui_headers())
    assert resp.status_code == 410 and "만료" in resp.text


def test_password_never_leaks(client, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    typo = "Typo-Pass-Ω-123"
    request_id = start_mount(client)
    csrf = open_form(client, request_id)
    wrong = client.post(f"/ui/mount/{request_id}", headers=ui_headers(),
                        data={"csrf": csrf, "password": typo})
    assert "암호가 틀렸습니다" in wrong.text
    right = client.post(f"/ui/mount/{request_id}", headers=ui_headers(),
                        data={"csrf": csrf, "password": SHARE_PASSWORD})
    status = client.post("/api/mount_share", headers=bot_headers(),
                         json={"name": "secret", "request_id": request_id})
    haystack = "\n".join([
        caplog.text,
        (tmp_path / "audit.jsonl").read_text(),
        wrong.text, right.text, status.text,
    ])
    for secret in (typo, SHARE_PASSWORD, "dsm-secret-pw", "t" * 40, "sid-1"):
        assert secret not in haystack, secret
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_ui.py -q`
Expected: FAIL. 라우트가 없어서 `/ui/mount/...`가 404이고, `assert 404 == 410` 같은 실패가 난다.

- [ ] **Step 3: 구현** — `src/nas_tasker/ui.py`

```python
import html
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .audit import AuditLogger, safe_args
from .dispatch import current_call, outcome_of
from .errors import ToolError
from .shares import ShareService
from .tasks import iso

SECURITY_HEADERS = {
    "cache-control": "no-store",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
    "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'",
}

PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>nas-tasker</title>
<style>body{{font-family:system-ui,sans-serif;max-width:28rem;margin:3rem auto;padding:0 1rem}}
input,button{{font-size:1rem;padding:.5rem;width:100%;box-sizing:border-box;margin-top:.5rem}}
.err{{color:#b00020}}</style></head>
<body>{body}</body></html>"""


def _page(body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(PAGE.format(body=body), status_code=status, headers=SECURITY_HEADERS)


def _form(request_id: str, share: str, csrf: str, expires_at: float, error: str = "") -> HTMLResponse:
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return _page(
        f"<h1>공유 폴더 마운트</h1>"
        f"<p><b>{html.escape(share)}</b> 폴더의 암호를 입력하세요. "
        f"이 링크는 {html.escape(iso(expires_at))}까지 한 번만 쓸 수 있습니다.</p>{err}"
        f'<form method="post" action="/ui/mount/{html.escape(request_id)}" autocomplete="off">'
        f'<input type="hidden" name="csrf" value="{html.escape(csrf)}">'
        f'<input type="password" name="password" autocomplete="off" required autofocus>'
        f"<button type=\"submit\">마운트</button></form>"
    )


MESSAGES = {
    "REQUEST_EXPIRED": "링크가 만료되었거나 이미 사용되었습니다. 봇에게 다시 요청하세요.",
    "FORBIDDEN": "요청을 확인할 수 없습니다. 링크를 다시 열어 주세요.",
    "INVALID_INPUT": "암호를 입력하세요.",
    "DSM_UNREACHABLE": "NAS에 연결할 수 없습니다. 잠시 후 다시 시도하세요.",
}


def build_ui_router(shares: ShareService, audit: AuditLogger) -> APIRouter:
    router = APIRouter()

    @router.get("/ui/mount/{request_id}")
    async def mount_form(request_id: str) -> HTMLResponse:
        try:
            req = shares.open_request(request_id)
        except ToolError as err:
            return _page(f"<p>{MESSAGES[err.code]}</p>", err.http_status)
        return _form(req.id, req.share, req.csrf, req.expires_at)

    @router.post("/ui/mount/{request_id}")
    async def mount_submit(request_id: str, request: Request) -> HTMLResponse:
        started = time.monotonic()
        form = await request.form()
        csrf, password = form.get("csrf"), form.get("password")
        del form
        share = None
        code = dsm_code = None
        try:
            share = shares.open_request(request_id).share
            await shares.complete_mount(request_id, csrf, password)
            response = _page("<h1>마운트 완료</h1><p>이 창을 닫아도 됩니다.</p>")
        except ToolError as err:
            code, dsm_code = err.code, err.dsm_error_code
            if err.code == "WRONG_PASSWORD":
                try:
                    req = shares.open_request(request_id)
                    response = _form(req.id, req.share, req.csrf, req.expires_at, "암호가 틀렸습니다.")
                except ToolError:
                    response = _page("<p>암호를 여러 번 틀려 이 링크는 막혔습니다. 봇에게 다시 요청하세요.</p>", 400)
            else:
                message = MESSAGES.get(err.code, f"마운트하지 못했습니다 ({err.code}).")
                response = _page(f"<p>{html.escape(message)}</p>", err.http_status)
        finally:
            password = None  # noqa: F841
        ctx = current_call.get()
        audit.record(
            request_id=uuid.uuid4().hex,
            actor_email=ctx.identity.subject,
            auth_method=ctx.identity.auth_method,
            client_ip=ctx.client_ip,
            tool="ui_mount",
            args=safe_args("ui_mount", {"name": share, "request_id": request_id}),
            result=outcome_of(code),
            error_code=code,
            duration_ms=round((time.monotonic() - started) * 1000),
            dsm_error_code=dsm_code,
        )
        return response

    return router
```

`src/nas_tasker/app.py`를 고친다. import에 `from .ui import build_ui_router`를 더하고, `app.include_router(build_api_router(dispatcher))` 바로 아래에 한 줄 더한다:

```python
    app.include_router(build_ui_router(shares, audit))
```

다 고친 `app.py`는 다음과 같다(확인용):

```python
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from mcp.server.transport_security import TransportSecuritySettings

from .audit import AuditLogger
from .auth import AccessVerifier, KeyResolver
from .config import Settings
from .dispatch import Dispatcher
from .dsm import DsmClient
from .http_api import build_api_router
from .mcp_server import build_mcp
from .middleware import AuthMiddleware
from .ratelimit import RateLimiter
from .shares import ShareService
from .tasks import TaskService
from .ui import build_ui_router


def build_app(
    settings: Settings,
    *,
    dsm_transport: httpx.AsyncBaseTransport | None = None,
    key_resolver: KeyResolver | None = None,
):
    dsm = DsmClient(
        settings.dsm_url,
        settings.dsm_username,
        settings.dsm_password,
        verify_tls=settings.dsm_verify_tls,
        transport=dsm_transport,
    )
    audit = AuditLogger(settings.audit_log_path)
    tasks = TaskService(dsm, settings.task_output_dir)
    shares = ShareService(
        dsm,
        settings.share_allowlist,
        settings.public_base_url,
        settings.mount_request_ttl_seconds,
        settings.confirm_token_ttl_seconds,
    )
    dispatcher = Dispatcher(tasks, shares, audit, RateLimiter())

    mcp = build_mcp(dispatcher)
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/",
        json_response=True,
        stateless_http=True,
        # Host checks are replaced by Access JWT + bearer checks in AuthMiddleware.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield
        await dsm.aclose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(build_api_router(dispatcher))
    app.include_router(build_ui_router(shares, audit))
    app.mount("/mcp", mcp_app)

    verifier = AccessVerifier(
        settings.cf_access_issuer,
        settings.cf_access_aud,
        settings.allowed_emails,
        key_resolver=key_resolver,
    )
    return AuthMiddleware(app, verifier, settings.mcp_bearer_token, audit)
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest -q`
Expected: 전부 PASS

- [ ] **Step 5: 커밋**

```bash
git add src/nas_tasker/ui.py src/nas_tasker/app.py tests/test_ui.py
git commit -m "feat: browser form for share passwords with CSRF and one-time links"
```

---

### Task 11: 실행 진입점, 컨테이너, Synology 배포 파일

**Files:**
- Create: `src/nas_tasker/__main__.py`, `Dockerfile`, `synology/docker-compose.yml`, `synology/.env.example`, `README.md`
- Test: `tests/test_deploy_files.py`

**Interfaces:**
- Consumes: `build_app`, `load_settings`
- Produces: `python -m nas_tasker`로 서버를 띄운다. uvicorn의 access log는 끈다(`/ui/mount/<id>`가 로그에 찍히지 않게). `httpx` 로거는 WARNING으로 둔다.

- [ ] **Step 1: 실패하는 테스트 쓰기** — `tests/test_deploy_files.py`

```python
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SYNOLOGY = ROOT / "synology"

SPEC_ENV_KEYS = [
    "DSM_URL", "DSM_USERNAME", "DSM_PASSWORD", "DSM_VERIFY_TLS", "TASK_PREFIX",
    "TASK_OUTPUT_DIR", "SHARE_ALLOWLIST", "MCP_BEARER_TOKEN", "CF_ACCESS_TEAM_DOMAIN",
    "CF_ACCESS_AUD", "ALLOWED_EMAILS", "CLOUDFLARE_TUNNEL_TOKEN", "BIND_HOST", "BIND_PORT",
    "PUBLIC_BASE_URL", "AUDIT_LOG_PATH", "CONFIRM_TOKEN_TTL_SECONDS",
    "MOUNT_REQUEST_TTL_SECONDS", "LOG_LEVEL", "TZ",
]


def shipped_files():
    yield ROOT / "Dockerfile"
    yield from SYNOLOGY.iterdir()
    yield from (ROOT / "src").rglob("*.py")


def test_no_chmod_or_chown_anywhere():
    for path in shipped_files():
        text = path.read_text()
        assert not re.search(r"\bch(mod|own)\b|--chown|--chmod", text), path


def test_synology_folder_has_only_deploy_files():
    assert sorted(p.name for p in SYNOLOGY.iterdir()) == [".env.example", "docker-compose.yml"]


def test_compose_hardening():
    text = (SYNOLOGY / "docker-compose.yml").read_text()
    assert "docker.sock" not in text
    assert "privileged" not in text
    assert text.count("network_mode: host") == 2
    assert ":/data/output:ro" in text
    volume_lines = [l for l in text.splitlines() if l.strip().startswith("- ${")]
    assert len(volume_lines) == 2
    assert "no-new-privileges:true" in text and "read_only: true" in text


def test_env_example_lists_every_key_without_values():
    lines = [l for l in (SYNOLOGY / ".env.example").read_text().splitlines()
             if l and not l.startswith("#")]
    keys = [l.split("=", 1)[0] for l in lines]
    assert all(l.endswith("=") for l in lines)
    for key in SPEC_ENV_KEYS:
        assert key in keys, key


def test_server_binds_localhost_by_default():
    from nas_tasker.config import load_settings
    from .test_config import BASE_ENV

    assert load_settings(BASE_ENV).bind_host == "127.0.0.1"
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/bin/pytest tests/test_deploy_files.py -q`
Expected: FAIL — `FileNotFoundError: .../Dockerfile`

- [ ] **Step 3: 구현**

`src/nas_tasker/__main__.py`:

```python
import logging

import uvicorn

from .app import build_app
from .config import load_settings


def main() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn.run(
        build_app(settings),
        host=settings.bind_host,
        port=settings.bind_port,
        log_level=settings.log_level.lower(),
        access_log=False,  # access lines would contain /ui/mount/<request_id>
        proxy_headers=False,
        server_header=False,
    )


if __name__ == "__main__":
    main()
```

`Dockerfile` (주석에도 그 두 명령어 단어를 쓰지 않는다. 테스트가 잡는다):

```dockerfile
FROM python:3.12-slim

# Synology: first user is usually uid 1026, group "users" is gid 100.
# Build with --build-arg APP_UID=... so the audit folder on the host is writable
# without changing any file owner or mode.
ARG APP_UID=1026
ARG APP_GID=100

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --no-create-home --shell /usr/sbin/nologin nastasker

USER ${APP_UID}:${APP_GID}
CMD ["python", "-m", "nas_tasker"]
```

`synology/docker-compose.yml`:

```yaml
services:
  nas-tasker:
    build:
      context: ..
      args:
        APP_UID: ${APP_UID:-1026}
        APP_GID: ${APP_GID:-100}
    image: nas-tasker:local
    container_name: nas-tasker
    network_mode: host
    env_file: .env
    environment:
      TASK_OUTPUT_DIR: /data/output
      AUDIT_LOG_PATH: /data/audit/audit.jsonl
    volumes:
      - ${TASK_OUTPUT_HOST_DIR}:/data/output:ro
      - ${AUDIT_HOST_DIR}:/data/audit
    read_only: true
    tmpfs:
      - /tmp
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    restart: unless-stopped

  cloudflared:
    image: cloudflare/cloudflared:2024.12.2
    container_name: nas-tasker-cloudflared
    network_mode: host
    command: tunnel --no-autoupdate run
    environment:
      TUNNEL_TOKEN: ${CLOUDFLARE_TUNNEL_TOKEN}
    read_only: true
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    restart: unless-stopped
    depends_on:
      - nas-tasker
```

`synology/.env.example`:

```text
# Copy to .env next to docker-compose.yml and fill in. Never commit .env.
DSM_URL=
DSM_USERNAME=
DSM_PASSWORD=
DSM_VERIFY_TLS=
TASK_PREFIX=
TASK_OUTPUT_DIR=
SHARE_ALLOWLIST=
MCP_BEARER_TOKEN=
CF_ACCESS_TEAM_DOMAIN=
CF_ACCESS_AUD=
ALLOWED_EMAILS=
CLOUDFLARE_TUNNEL_TOKEN=
BIND_HOST=
BIND_PORT=
PUBLIC_BASE_URL=
AUDIT_LOG_PATH=
CONFIRM_TOKEN_TTL_SECONDS=
MOUNT_REQUEST_TTL_SECONDS=
LOG_LEVEL=
TZ=
# Host paths for compose volumes (used only by docker-compose.yml)
TASK_OUTPUT_HOST_DIR=
AUDIT_HOST_DIR=
APP_UID=
APP_GID=
```

`README.md`: 아래 내용으로 짧게 쓴다(모두 실제로 할 일이다).

````markdown
# nas-tasker

봇이 Synology DSM의 `bot-` 작업 실행·결과 조회와, 허용된 암호화 공유 폴더 마운트·언마운트만 하게 해 주는 작은 컨테이너. 설계: `docs/superpowers/specs/2026-10-23-nas-tasker-design.md`.

## 개발

    python3 -m venv .venv
    .venv/bin/pip install -e ".[test]"
    .venv/bin/pytest -q

## 배포 (Synology Container Manager)

1. DSM에 nas-tasker 전용 관리자 계정을 만든다. 비밀번호는 길고 무작위로, 다른 용도로는 쓰지 않는다.
2. `bot-` 이름으로 작업 스케줄러 작업을 만든다. 스크립트는 `<출력 폴더>/<작업 이름>/latest.log`에 로그를 쓴다.
3. 감사 로그 폴더를 만든다. 컨테이너 UID(`APP_UID`, 기본 1026)가 쓸 수 있는 폴더여야 한다.
4. Cloudflare Zero Trust에서 이 서비스 전용 터널을 만든다. 공개 호스트명은 `http://127.0.0.1:<BIND_PORT>`로 연결한다. 같은 호스트명에 Access 애플리케이션(Google 로그인, 허용 이메일 2개)을 만들고 AUD를 적어 둔다.
5. `synology/.env.example`을 `synology/.env`로 복사해 채운다. `MCP_BEARER_TOKEN`은 `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`로 만든다.
6. Container Manager → 프로젝트 → 만들기에서 경로로 `synology/`를 고르고 `docker-compose.yml`로 빌드·실행한다.
7. 봇의 MCP 주소는 `<PUBLIC_BASE_URL>/mcp/`, 헤더는 `Authorization: Bearer <MCP_BEARER_TOKEN>`.
````

- [ ] **Step 4: 통과 확인**

Run: `.venv/bin/pytest -q`
Expected: 전부 PASS

Run: `docker build -t nas-tasker:local .` (Docker가 있는 곳에서만)
Expected: 빌드 성공

- [ ] **Step 5: 커밋**

```bash
git add src/nas_tasker/__main__.py Dockerfile synology README.md tests/test_deploy_files.py
git commit -m "feat: container image and Synology compose deployment"
```

---

### Task 12: 실제 DSM API 맞추기와 배포 확인 (YongJin과 함께)

자동 테스트로는 할 수 없는 단계다. NAS에 접속할 수 있는 사람이 확인해야 한다. 코드 변경은 `dsm/api.py`, `dsm/client.py` 파싱, `tests/mock_dsm.py` 세 곳으로 한정한다.

**Files:**
- Modify: `src/nas_tasker/dsm/api.py`, `src/nas_tasker/dsm/client.py`, `tests/mock_dsm.py`, `tests/test_dsm_client.py`

- [ ] **Step 1: DSM 웹 UI 요청 받아 적기.** 브라우저 개발자 도구 Network 탭에서 `entry.cgi` 요청을 필터링한다. 아래 동작을 각각 한 번씩 하면서 `api`, `method`, `version`, 폼 파라미터, 응답 JSON을 적는다. 비밀번호와 `_sid` 값은 적지 않고 지운다.
  - 제어판 → 작업 스케줄러 목록 열기 (목록, 실행 중 여부, 마지막 실행 시각·결과 필드)
  - 작업 하나 "실행"
  - 작업 "결과 보기" (마지막 종료 코드가 목록에 없으면 이 요청을 쓴다)
  - 암호화 공유 폴더 마운트(올바른 암호, 틀린 암호 각각 한 번)와 언마운트
  - Container Manager → 컨테이너 목록, 컨테이너 상세(볼륨 설정)
- [ ] **Step 2: `tests/mock_dsm.py`를 받아 적은 실제 형식으로 고친다.** 테스트를 돌려 실패하는 곳을 확인한다(`.venv/bin/pytest -q`).
- [ ] **Step 3: `dsm/api.py`의 이름·버전·`WRONG_PASSWORD_CODES`와 `client.py` 파싱을 고친다.** 새 메서드가 필요하면 `ALLOWED_METHODS`에 조회 메서드만 더한다. `test_allowlist_has_no_mutating_task_or_docker_methods`가 계속 통과해야 한다.
- [ ] **Step 4: 전체 테스트 통과 확인** — Run: `.venv/bin/pytest -q`, Expected: 전부 PASS
- [ ] **Step 5: NAS에서 연기 테스트.** 배포한 뒤 봇 또는 `curl`로 확인한다.
  - `list_tasks`에 `bot-gbrain-rebuild`만 보이는지
  - `run_task` 뒤 `task_result`가 로그 꼬리를 돌려주는지
  - `mount_share` 링크 → 브라우저에서 암호 입력 → 마운트됨
  - `unmount_share` 두 단계
  - 감사 로그(`<AUDIT_HOST_DIR>/audit.jsonl`)에 비밀값이 없는지: `grep -c "<공유 폴더 암호>" audit.jsonl` 결과가 0
- [ ] **Step 6: 커밋**

```bash
git add src/nas_tasker/dsm tests/mock_dsm.py tests/test_dsm_client.py
git commit -m "fix: align DSM API names and fields with DSM 7.x"
```

---

## 명세서 대조표

| 명세서 | Task |
|---|---|
| §1 도구 5개 + HTTP API | 5, 6, 8, 9 |
| §2 생성/수정/삭제 금지, 임의 API 없음 | 4 (`ALLOWED_METHODS`, `ForbiddenDsmCall`) |
| §2 파일 조작 금지, 출력 읽기 전용 | 5 (`O_RDONLY`), 11 (`:ro`) |
| §2 chmod/chown·Docker 소켓 금지 | 11 (`test_deploy_files.py`) |
| §2 비밀번호 저장·로그 금지, 채팅 전달 금지 | 6, 10 (`test_password_never_leaks`) |
| §3 network_mode host, 127.0.0.1, 전용 터널 | 1 (기본값), 11 |
| §4.1–4.5 입출력·오류 코드 | 5, 6, 8 |
| §4.2 동시 요청 직렬화 | 5 (`test_concurrent_requests_start_once`) |
| §4.4 TTL 10분, 1회용, POST, CSRF, autocomplete off | 6, 10 |
| §4.5 토큰 128비트 이상, 5분, 1회용, share·호출자 묶기 | 2, 6, 8 |
| §5 Access JWT 앱 내 검증, 이메일 허용 목록 | 7, 8 |
| §5 베어러 토큰 상수 시간 비교, GBrain 토큰 거부 | 7, 8 |
| §5 비루트, read-only 루트, no-new-privileges | 11 |
| §5 레이트 리밋 | 2, 8 (`test_rate_limit_run_task`), 6 (암호 5회) |
| §6 `.env` 키 | 1, 11 (`test_env_example_lists_every_key_without_values`) |
| §7 감사 로그 필드·금지 항목·stdout | 3, 8, 10 |
| §8 Mock DSM과 필수 테스트 | 4–10 |
| §8 DSM 세션 만료 재로그인 | 4 (`test_relogin_after_session_expiry`) |
| §9 `synology/`에 배포 파일만 | 11 (`test_synology_folder_has_only_deploy_files`) |
| §10 열린 질문 | 위 "잠정 결정" 표, 12 |
