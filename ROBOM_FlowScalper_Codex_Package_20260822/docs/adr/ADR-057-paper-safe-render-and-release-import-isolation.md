# ADR-057. PAPER 안전 화면 복구와 backend 릴리스 import 격리

## 상태

승인. 2026-08-27.

## 배경

ADR-056은 frontend와 backend commit 불일치를 화면에서 차단하고 commit별 실행 릴리스를 만들었다. 그러나 실제 8870의 기준 backend를 유지한 채 개발 worktree의 새 frontend bundle이 제공된 상태에서는 전략 상세가 구형 응답에 없는 필드를 읽어 React root가 비었다. 기준 observer를 중단하지 않고 현재 사이트를 회복할 필요가 있었다.

또한 첫 불변 snapshot E2E에서 별도 release의 `scripts/run_e2e_server.py`를 실행했지만 editable Python 환경이 개발 worktree의 `backend`를 먼저 import했다. 결과적으로 검증 대상 release가 아니라 worktree의 `FRONTEND_DIST`를 제공했고 3개 화면 모두 구형 전략 상세에서 실패했다. 동일 위험은 `python <release>/scripts/run_server.py` 형식의 실제 launcher에도 존재할 수 있었다. frontend만 snapshot이어도 backend import가 worktree를 가리키면 불변 릴리스가 아니다.

## 결정

1. 기준 설치 서비스의 Python process와 Run은 중단하지 않는다. 현재 제공 중인 정적 파일만 기준 backend commit `c57b988353718e03b26b93ac3208e64c5221396e`에서 별도 빌드해 같은 filesystem rename으로 교체한다. 교체 전 mixed bundle은 runtime 임시 복구본으로 보존한다.
2. 기준 정적 복구는 관찰을 계속하기 위한 임시 정합성 복구다. 새 구현 배포나 불변 release 활성화로 기록하지 않는다.
3. React root 최상단에 `AppErrorBoundary`를 둔다. 예기치 않은 render·lifecycle 예외는 빈 화면 대신 메뉴와 PAPER 제어가 없는 한국어 안전 화면으로 전환한다.
4. 안전 화면은 `PAPER 계산만 사용하며 실제 주문은 계속 0`임을 명시하고 사용자가 전체 화면을 다시 불러올 수 있게 한다. 오류 원문과 component stack은 브라우저 console에 남기되 거래 로직이나 서버 상태를 자동 변경하지 않는다.
5. macOS service launcher는 `PYTHONNOUSERSITE=1`과 `PYTHONPATH=<physical-release-root>`를 설정해 애플리케이션 import를 활성 불변 release로 고정한다.
6. launcher는 서버 시작 전에 `backend.__file__`의 물리 경로를 확인한다. `<physical-release-root>/backend`와 다르면 exit 75로 fail-closed하고 8870을 시작하지 않는다.
7. release snapshot E2E도 `PYTHONPATH=<tested-release-root>`를 명시한다. editable 환경에서 다른 소스를 읽은 실행은 제품 실패나 PASS 증거로 쓰지 않고 `INVALID_TEST_ENVIRONMENT`로 기록한다.
8. 전략 임계값·모드·비용·TP·SL·Governor·PAPER 계좌·원장·실제주문 0 경계는 변경하지 않는다.

## 결과

- 실제 기준 8870은 process·Run·observer 재시작 없이 기준 backend와 같은 commit의 화면으로 돌아왔다.
- 전략 상세 또는 다른 React 하위 화면에서 예상하지 못한 예외가 발생해도 사용자에게 완전한 빈 화면을 보여 주지 않는다.
- 설치 서비스의 backend source도 frontend와 같은 물리 release에서만 import된다.
- release 검증이 editable worktree를 잘못 검사하고도 통과하는 증거 오염을 차단한다.

## 검증 경계

수정 전 실제 8870에서 `전략 → 자세히` 뒤 빈 DOM을 재현했다. 기준 frontend 복구 뒤 실제 브라우저에는 11개 `자세히`, 전략 상세 dialog 1개, DOM 18,648자와 alert 0이 확인됐다. 이는 기준 commit 복구 증거이며 새 commit 배포 증거가 아니다.

오류 경계 표적 테스트는 component가 없을 때 먼저 실패했고 구현 뒤 1건 PASS했다. 최종 commit `d8e5bae154ef693c37b88af980d1c5d0031ca806`에서 backend 442, frontend 63, Ruff·mypy·ESLint·TypeScript·PAPER safety·security·repository hygiene를 통과했다. 별도 불변 snapshot build는 JS 522.00kB·gzip 160.69kB로 PASS했으나 기존 500kB 경고가 남는다.

첫 snapshot E2E 3건은 editable import가 worktree를 읽은 검증환경 오류로 실패했고 제품 판정에서 제외했다. release root를 Python import 최우선으로 고정한 최종 snapshot은 desktop·tablet·mobile 3건이 18.2초에 PASS했다. 실제 설치 서비스는 기준 6시간·24시간 observer 때문에 아직 새 release로 전환하지 않았다. 따라서 새 commit의 실제 LaunchAgent·8870·원장 복구·screenshot·GitHub main·Actions는 `NOT_RUN`, 수익성은 `NOT_PROVEN`이다.
