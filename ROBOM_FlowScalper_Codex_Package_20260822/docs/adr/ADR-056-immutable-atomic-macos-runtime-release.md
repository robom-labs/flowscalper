# ADR-056. 불변·원자적 macOS 실행 릴리스

## 상태

승인. 2026-08-27.

## 배경

설치된 8870 PAPER 서비스의 Python 프로세스는 기준 commit을 이미 import한 채 계속 실행 중이었지만 LaunchAgent와 FastAPI 정적 파일 경로는 개발 worktree를 직접 가리켰다. 장기 observer를 유지한 상태에서 이후 Wave의 `frontend/dist` 빌드가 같은 경로를 덮어썼고, 실제 8870은 구형 backend API와 신형 frontend bundle을 섞어 제공했다.

실제 브라우저에서 시장 화면은 열렸지만 `전략 → 자세히`를 누르면 새 화면이 구형 전략 행에 없는 `required_market_data`를 읽어 React root 전체가 비었다. 이는 전략 자체나 공개시장 장애가 아니라 개발 소스와 설치 실행본을 한 디렉터리에서 제공한 배포 경계 결함이다. Python도 이후 생성되는 subprocess 또는 재시작에서 변경된 worktree를 읽을 수 있어 backend와 frontend 모두 불변 실행 기준이 필요하다.

## 결정

1. macOS 설치 서비스는 개발 worktree가 아니라 `05_RUNTIME/ROBOM_FlowScalper/releases/<full-commit>`의 불변 commit snapshot에서 실행한다.
2. snapshot은 추적 파일 변경이 없는 commit의 `git archive`로 staging 디렉터리에 추출한다. 프론트엔드는 staging snapshot에서만 빌드하며 개발 worktree의 `frontend/dist`를 쓰지 않는다.
3. 릴리스 manifest는 full commit, 생성시각, 프론트엔드 파일별 SHA-256, 공개시장 archive 경로, 활성 원장 경로, 이전·rollback 릴리스와 PAPER 안전 0 계약을 기록한다.
4. 완성된 staging 디렉터리는 같은 filesystem에서 최종 릴리스 경로로 원자 rename한다. `current` 임시 symlink도 `os.replace`로 원자 교체한다.
5. 활성화는 `CODEX_DEPLOY` actor, 이전·새 릴리스, 원인, rollback 경로와 reversibility를 `deployments/*.json`과 `current-deployment.json`에 기록한다. 최초 활성화는 이전 릴리스가 없어 reversible false이며, 다음 활성화부터 직전 릴리스가 rollback point다.
6. LaunchAgent plist는 runtime의 `current/scripts/run_macos_service.sh`만 실행한다. launcher는 symlink의 실제 릴리스 경로를 고정하고 manifest의 공개시장 archive·활성 원장 경로를 사용한다.
7. 4GB 이상의 공개시장 archive와 활성 원장은 릴리스마다 복사하지 않는다. manifest가 기존 안정 경로를 명시하고 source snapshot만 교체한다.
8. backend는 `release_commit`과 `release_isolated`를 dashboard에 공개한다. 프론트엔드 HTML도 같은 full commit을 가진다.
9. 둘 중 하나만 불변 commit이거나 두 commit이 다르면 메뉴·PAPER 제어·세부 화면을 렌더링하지 않고 한국어 버전 불일치 안전 화면만 표시한다. 개발 환경끼리는 `development`로 계속 사용할 수 있다.
10. 긴 진단값과 commit은 화면 폭을 늘리지 않도록 카드 내부에서 줄바꿈하고 commit은 12자로 표시하되 전체 값은 title에 보존한다.
11. runtime release는 Git의 구버전 소스 복사본이 아니다. main에는 최신 소스 한 벌만 유지하고, 검증된 rollback release 삭제는 별도 보존 정책과 명시적 작업으로만 수행한다.
12. 전략 임계값·모드·비용·TP·SL·Governor·원장·계좌·실제주문 0 경계는 변경하지 않는다.

## 결과

- 다음 frontend build나 source 편집이 실행 중 8870의 파일을 바꾸지 않는다.
- backend와 frontend 혼합 배포는 화면 전체 crash 대신 명시적 fail-closed 상태가 된다.
- 배포 commit, 프론트 파일 hash와 rollback point를 기계적으로 대조할 수 있다.
- 공개시장 archive와 대형 원장을 복제하지 않아 전환 I/O를 제한한다.

## 검증 경계

수정 전 실제 8870의 구형 backend·신형 frontend 혼합과 `전략 → 자세히` 빈 화면을 재현했다. 최종 소스에서 backend 441, frontend 62, 정적·타입·보안·PAPER 검사를 통과했다. commit `1bfbd21fab905008314712582b0d1c8b082c8a68`을 별도 임시 runtime에 실제 stage·build·activate했고 manifest·HTML·backend commit이 일치했다. 해당 최종 snapshot의 Playwright desktop·tablet·mobile 3건이 PASS했다. 실제 브라우저 전략 상세는 같은 릴리스 구조의 앞선 일치 snapshot에서 확인했으며 최종 commit의 설치 8870 확인으로 해석하지 않는다.

중간 E2E에서 commit 환경값을 생략한 3건은 의도대로 불일치 안전 화면으로 차단됐다. commit을 일치시킨 다음 실행은 현재 디스크 압박 장문이 root 폭을 늘리고 모바일 진단 클릭을 가로막는 실제 반응형 결함을 발견했다. 장문 줄바꿈 수정 후 최종 3건이 PASS했다.

실제 설치 서비스는 기준 6시간·24시간 observer를 보존하기 위해 아직 새 릴리스로 전환하지 않았다. 따라서 실제 LaunchAgent 재시작, 8870의 새 commit·hash·rollback, 배포 후 원장·LIVE 공개시장·화면 screenshot, GitHub main·Actions는 `NOT_RUN`이다. 전략 수익성은 계속 `NOT_PROVEN`이다.
