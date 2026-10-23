# AI 활용사례 로그 — nas-tasker

> 최종 갱신: 2026-10-23 · 누적 세션 1회 · 상태: 작성중
> 아래 "등록용 초안"을 그대로 복사해 등록하면 됩니다. 빈 항목만 채워 주세요.

---

## 등록용 초안

### 집 NAS 작업을 AI 봇이 안전하게 실행하도록 하는 작은 서버를 명세서부터 배포 직전까지 하루에 만들기

#### 1. 배경 & 문제 정의
어시스턴트 봇이 NAS의 정해진 작업(예: GBrain 재색인)을 대신 실행하고 결과를 확인하게 하고 싶었다. 그런데 NAS 관리자 권한을 봇에게 통째로 줄 수는 없었다. 그래서 할 수 있는 일을 도구 5개로 좁히고, 암호화 폴더의 비밀번호는 봇이 아니라 사람이 브라우저에서 넣게 하는 별도 서버가 필요했다.

#### 2. 시도한 방법
Claude Code로 진행했다. 명세서(GBrain 노트) → 구현 계획(12단계) → 단계별로 테스트 먼저 쓰기(TDD) → 새 검토 에이전트의 전체 검토 → 수정 → PR 순서였다. 계획서에 넣을 코드는 먼저 실제로 돌려 본 뒤 붙여서, 계획서 자체가 검증된 상태가 되게 했다. 실제 NAS 없이 테스트하려고 가짜 DSM 서버를 만들었다.
- **Lessons Learned**:
  - 라이브러리 메이저 버전이 바뀌어 있었다(MCP SDK 2.x). 기억에 의존하지 않고 작은 시제품을 먼저 돌려 본 덕분에 계획서에 틀린 코드가 들어가지 않았다.
  - 전체 검토에서 "명세서 예시대로 `.env`를 복사하면 첫 실행부터 실패한다"는 문제가 나왔다. 테스트 140개가 다 통과해도 사용자가 실제로 밟는 경로는 따로 확인해야 했다.
  - 봇 접속 방식(서비스 토큰을 쓸지)은 코드로 정할 수 없는 문제였다. 열린 질문으로 남겨 사람이 결정하게 한 뒤 반영했다.

#### 3. 재현 가능한 스텝
1. 해결할 문제, 하지 말아야 할 것, 보안 모델, 열린 질문을 명세서에 정리한다.
2. AI에게 "명세서 읽고 구현 계획 짜 줘"라고 요청한다. 계획에 넣을 코드는 실제로 돌려 본 것만 넣게 한다.
3. "직접 실행"을 고르고, 단계마다 테스트 실패 → 구현 → 통과 → 커밋 순서를 지키게 한다.
4. 끝나면 새 검토 에이전트에게 브랜치 전체 검토를 맡기고, 중요한 문제만 테스트 먼저 쓰고 고친다.
5. 정책이 바뀌면 지시서를 새 노트로 써서 넘기고 PR로 받는다.

- 사용 툴/모델: Claude Code — claude-opus-5-5, claude-opus-5-5 (서브에이전트)
- 필요 입력(레퍼런스 이미지, 데이터 등): 설계 명세서, 봇 접속 방식 지시서 (GBrain 노트)
- 참고 링크/첨부파일:

<details><summary>핵심 프롬프트 원문</summary>

> 명세서 읽어보고 구현계획 짜봐
>
> 리포에 docs/superpowers/specs/2026-10-23-nas-tasker-design.md로 첫 커밋해서 넣고, 그다음에 구현 계획을 짜 줘.
>
> 직접 ㄱ

</details>

#### 4. 결과 & 임팩트
- AI 토큰 사용량(누적): 응답 수 105 · 입력 216 · 캐시 생성 767,388 · 캐시 읽기 23,086,611 · 출력 166,360 · 합계 24,020,575 / 응답 수 14 · 입력 28 · 캐시 생성 126,849 · 캐시 읽기 1,281,250 · 출력 22,480 · 합계 1,430,607
- 기타 지표 변화: 하루 동안 명세서 → 계획 → 구현 → 검토 → PR 2개 머지. 자동 테스트 168개.
- 실제 프로젝트/제안서/납품물에 적용되었는가: 예 (menkinds/nas-tasker)
- 적용됐다면 프로젝트명 또는 맥락: menkinds/nas-tasker
- 결과물: 최근 커밋: cb4e484 Merge pull request #2 from menkinds/claude/keen-bardeen-8sy83a; f092e58 fix: mount retry after a lost DSM reply; refuse to start without a writable audit log; 5cc516d Merge pull request #1 from menkinds/claude/keen-bardeen-8sy83a; a1365ba feat: bearer-only /mcp behind a Cloudflare Access bypass; c7e2399 fix: empty .env values use defaults; guard run/result around DSM status lag and lost replies; 8d83fc1 feat: container image and Synology compose deployment; cdb41d3 feat: browser form for share passwords with CSRF and one-time links; 7007960 feat: MCP endpoint exposing exactly five tools / 변경 파일: README_CLAUDE.md
- 스킬: writing-plans×1, executing-plans×1, test-driven-development×1, finishing-a-development-branch×1, save×1

#### 5. 다른 조직에도 적용 가능한 부분
'명세서 → 검증된 계획서 → TDD 실행 → 새 검토자 1회'라는 흐름은 업무 자동화 도구를 만들 때 그대로 쓸 수 있다. AI에게 권한을 줄 때 도구 수를 좁히고 비밀번호는 사람이 직접 넣게 하는 패턴은 다른 내부 시스템 연동에도 쓸 수 있다.

#### 6. 남기고 싶은 팁 / 주의사항
- 계획서의 코드를 AI가 실제로 돌려 봤는지 확인하세요. 기억으로 쓴 코드는 라이브러리 버전이 바뀌면 바로 틀립니다.
- 테스트가 다 통과해도 '설명서대로 처음 설치하는 사람'의 경로는 따로 검토받으세요.
- 실제 장비(NAS) 없이 만든 부분(API 이름)은 추정값이라고 표시해 두고, 배포할 때 확인하세요.

#### 태그
- 부서 태그(필수):
- 유형 태그: `#케이스세션`
- 주제 태그(1~3개): `#NAS자동화` `#MCP` `#TDD`

---

## 세션 기록
<!-- 세션마다 logs/sessions/ 의 파일 링크. 최신이 위. -->

- [[nas-tasker-2026-10-23-1-case]] — 명세서→계획→구현(168 테스트)→봇 접속 지시서 반영, PR #1·#2 머지
