# 18. 버전·업그레이드·과거자료 정리 정책

## 목적

업그레이드할 때마다 구버전 소스·화면·설정·테스트가 현재 구현 옆에 중복으로 남는 문제를 막는다. 현재 GitHub `main`에는 실행 가능한 최신 소스 한 벌만 둔다. 과거는 짧은 `CHANGELOG.md`, Git commit, tag와 Release로 찾는다.

## 공식 관례에서 채택한 원칙

- Git tag는 중요한 시점, 특히 릴리스 지점을 표시한다. 배포 ZIP과 checksum은 tag에 연결된 GitHub Release asset으로 둔다.
- 이미 배포한 버전의 내용은 덮어쓰지 않는다. 수정은 새 버전으로 배포한다.
- `MAJOR.MINOR.PATCH`에서 호환되지 않는 사용자·API·schema 변화는 MAJOR, 호환되는 기능 추가는 MINOR, 호환되는 오류 수정은 PATCH로 분류한다.
- `CHANGELOG.md`에는 모든 commit이 아니라 사용자가 알아야 할 변화만 적고, 다음 변경은 맨 위의 `아직 배포하지 않음`에 모은다.
- Release notes는 한 릴리스의 설치·검증·주요 변경을 설명하고, changelog는 여러 버전의 짧은 연속 기록 역할을 한다.

근거는 [GitHub Releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases), [Git tag](https://git-scm.com/book/en/v2/Git-Basics-Tagging.html), [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html), [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)이다.

## 저장 위치별 단일 책임

| 위치 | 보존하는 것 | 두지 않는 것 |
|---|---|---|
| GitHub `main` | 최신 backend·frontend·설정·schema·테스트·현재 문서 | 구버전 소스 폴더, 복사본, ZIP, 운영 DB, cache |
| 저장소 최상위 `.github` | GitHub Actions workflow와 PR checklist | 제품 source·runtime data |
| `CHANGELOG.md` | 버전별 중요한 변화 3~7줄 | commit 전체 복사, 장문의 테스트 로그 |
| Git tag | 특정 버전의 정확한 source commit | 이동하는 최신 포인터 |
| GitHub Release | 그 tag의 배포 ZIP·SHA-256·최종 증거 | 원시 운영 SQLite·Parquet·로그 |
| 외장 runtime data | Run별 불변 PAPER 원장과 공개시장 archive | 소스 복사본을 버전마다 중복 저장 |
| 로컬 migration archive | 더는 runtime이 읽지 않는 구형 데이터의 복구용 보관본 | 현재 실행 경로 |

## 현재 폴더 규칙

```text
ROBOM_FlowScalper_Codex_Package_20260822/
├── backend/          현재 PAPER backend만 보존
├── frontend/         현재 React UI만 보존
├── config/           현재 지원하는 설정 예시
├── schemas/          현재 schema와 명시적 migration 계약
├── docs/             현재 제품·기술 계약과 ADR
├── evidence/         현재 버전 수용기준을 설명하는 제한된 증거
├── scripts/          실행·검증·릴리스 자동화
├── CHANGELOG.md      과거 버전의 짧은 요약
├── VERSION           현재 제품 버전의 단일 원본
└── README.md         현재 사용자 안내
```

다음 이름은 현재 트리에 만들지 않는다.

- `old`, `legacy`, `backup`, `copy`, `복사본` 폴더.
- `frontend-v1`, `backend_old`, `App_backup.tsx` 같은 병렬 구버전 구현.
- 버전별 저장소 전체 복사본.
- `*.zip`, `*.sqlite3`, `*.parquet`, `*.log`, `*.tsbuildinfo` 같은 생성·실행 파일.

과거 코드를 보고 싶으면 새 폴더를 만들지 않고 tag 또는 Git history를 checkout한다.

## 기능 또는 UI 업그레이드 절차

1. 현재 `main`과 현재 실행화면을 기준선으로 기록한다.
2. 요청을 `교체`, `추가`, `유지`, `제거`, `데이터 migration`으로 나눈다.
3. 교체 대상은 기존 component·route·copy·CSS·test를 찾아 같은 변경에서 새 구현으로 바꾸고 더는 참조되지 않는 코드는 제거한다.
4. 호환기간이 꼭 필요한 API나 schema만 deprecated 상태로 한 버전 유지한다. 단순 UI 교체에는 legacy flag를 만들지 않는다.
5. 저장 schema 변경은 명시적인 정방향 migration과 복구 테스트를 제공한다. 과거 Run을 현재 Run에 합치거나 재작성하지 않는다.
6. 기능 test, 회귀 test, lint, typecheck, build, security, 화면 검증을 실행한다.
7. `VERSION`, `CHANGELOG.md`, 관련 문서와 증거를 함께 갱신한다.
8. 저장소 위생검사를 통과한 한 commit을 tag하고 새 Release를 만든다.

## UI 교체의 완료 기준

- 새 화면만 기본 navigation과 route에서 접근된다.
- 이전 label·button·panel·CSS selector·mock data가 현재 코드에서 검색되지 않는다.
- 같은 값을 old/new 두 상태에서 동시에 관리하지 않는다.
- 저장된 사용자 설정이 있으면 한 번의 명시적 migration으로 현재 형식이 된다.
- mobile·tablet·desktop에서 현재 수용기준을 다시 검증한다.
- 과거 screenshot은 현재 화면 증거로 사용하지 않는다.

## 데이터와 schema 업그레이드 규칙

- Run은 `run_id`, app version, strategy version, config hash와 schema version을 기록한다.
- 현재 runtime은 현재 schema로 migration된 원장만 쓴다.
- migration 실패는 새 PAPER 진입을 잠그고 과거 데이터를 묵시적으로 버리지 않는다.
- 구형 데이터가 더는 runtime에 필요 없으면 먼저 활성 파일이 아님을 확인하고 외장 migration archive로 이동한다.
- archive 이동은 원본 경로·크기·SHA-256·이동 시각을 텍스트 manifest에 기록한다.
- 오래된 데이터의 자동 삭제는 하지 않는다. 별도 보존기간 또는 삭제 요청이 있을 때만 checksum 검증 후 제거한다.

## 버전 올리는 기준

| 변경 | 예시 | 다음 버전 예시 |
|---|---|---|
| PATCH | 지연 계산 오류, 깨진 layout, 재연결 버그 수정 | `0.2.1-paper` |
| MINOR | 새 전략, 새 화면, 새 replay 기능, 호환 가능한 schema 확장 | `0.3.0-paper` |
| MAJOR | 기존 Run·API·사용 흐름과 호환되지 않는 계약 변경 | `1.0.0-paper` |

0.x 개발 단계에서도 사용자가 보는 동작이나 저장 형식이 깨지면 changelog와 migration 문서에 명확히 기록한다. 실제 주문 없는 `-paper` 경계는 버전이 올라가도 자동으로 해제되지 않는다.

## 릴리스 체크리스트

- `VERSION`과 README·frontend package·Python base version이 일치한다.
- `CHANGELOG.md`의 아직 배포하지 않음 항목을 새 버전으로 옮겼다.
- `make repo-hygiene`가 PASS다.
- backend·frontend test, lint, typecheck, build, security가 PASS다.
- 실제 주문·private API·인증 경로가 0이다.
- ZIP 내부 `BUILD_COMMIT`과 checksum이 검증된다.
- tag와 Release는 이미 배포된 이름을 덮어쓰지 않고 새 버전으로 만든다.
- 현재 `main`에는 최신 소스 한 벌만 남아 있다.

## AI가 업그레이드할 때 지켜야 할 짧은 규칙

“새 기능을 옆에 추가”하기 전에 기존 기능의 진입점·상태·스타일·테스트를 먼저 찾는다. 교체 요청이면 기존 구현을 같은 변경에서 제거한다. 과거는 코드 복사본이 아니라 `CHANGELOG.md` 한두 줄과 tag·Release에 남긴다. 데이터 migration이 필요하면 삭제하지 말고 archive·검증·새 schema 순서로 처리한다.
