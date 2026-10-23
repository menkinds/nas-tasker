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
4. Cloudflare Zero Trust에서 이 서비스 전용 터널을 만든다. 공개 호스트명은 `http://127.0.0.1:<BIND_PORT>`로 연결한다. 같은 호스트명에 Access 애플리케이션(Google 로그인, 허용 이메일 2개)을 만들고 AUD를 적어 둔다. 봇용으로 `/mcp` 경로만 Access를 건너뛰는 Bypass 애플리케이션을 따로 만든다. `/mcp`는 앱의 베어러 토큰으로만 열리고, 나머지 경로(`/api/`, `/ui/`)는 앱이 Access JWT를 직접 검증한다.
5. `synology/.env.example`을 `synology/.env`로 복사해 채운다. `MCP_BEARER_TOKEN`은 `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`로 만든다.
6. Container Manager → 프로젝트 → 만들기에서 경로로 `synology/`를 고르고 `docker-compose.yml`로 빌드·실행한다.
7. 봇의 MCP 주소는 `<PUBLIC_BASE_URL>/mcp/`, 헤더는 `Authorization: Bearer <MCP_BEARER_TOKEN>`. 같은 IP에서 인증이 10번 실패하면 그 IP는 10분 동안 막힌다.
