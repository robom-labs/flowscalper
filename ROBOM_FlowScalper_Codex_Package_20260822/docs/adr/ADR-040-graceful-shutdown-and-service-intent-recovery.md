# ADR-040. 저장 완료를 기다리는 종료와 서비스 의도 복구

- 상태: Accepted
- 날짜: 2026-08-26
- 범위: FastAPI lifespan 종료와 macOS LaunchAgent 재시작

## 배경

공개시장 PAPER 서비스 종료 중 ASGI lifespan 취소 신호가 persistence worker 대기와 겹치면 `asyncio.CancelledError`가 로그에 남았다. worker 자체는 별도로 종료를 시도했지만, 상위 대기 coroutine이 취소되면 저장 완료 여부를 명확히 보장하기 어려웠다.

또한 backend는 미종료 `LIVE_SHADOW_PAPER` Run의 checksum 검증 snapshot을 복구할 수 있지만 macOS 서비스 실행기는 항상 `ROBOM_MODE=READY`를 강제했다. 이 때문에 비정상 종료나 로그인 재시작 뒤 사용자의 마지막 명시적 LIVE PAPER 관찰 의도가 자동 복구되지 않았다. 브라우저가 닫혀도 PAPER 관찰과 포지션 관리는 서버에서 계속되어야 하므로 실행기와 backend 복구 계약을 일치시켜야 한다.

## 결정

1. lifespan은 persistence worker를 `asyncio.shield()`로 기다린다. 상위 대기가 취소돼도 worker 결과를 `gather(..., return_exceptions=True)`로 끝까지 회수한다.
2. macOS 서비스 실행기는 명시적인 `ROBOM_MODE`가 있으면 이를 우선한다.
3. 명시값이 없으면 `scripts/select_service_mode.py`가 활성 원장을 읽기 전용으로 열고 가장 최근 미종료 Run을 확인한다.
4. 복구 가능한 mode는 `LIVE_SHADOW_PAPER`와 `DEMO_FIXTURE`뿐이다. 파일 없음, 스키마 오류, 읽기 오류, 미종료 Run 없음, 알 수 없는 mode는 모두 `READY`로 fail-closed한다.
5. mode 선택은 Run을 생성·종료·수정하지 않는다. 실제 복구, checksum 검증, fresh-book 재검증과 신규진입 잠금은 기존 backend 계약이 수행한다.
6. 실제 주문, 인증, private API, API Key, secret, wallet 경로는 추가하지 않는다.
7. 원장에 저장된 사용자 수동 pause 의도는 supervisor 연결 성공과 fresh-book 재검증보다 우선한다. 자동 잠금은 정상화 뒤 해제될 수 있지만 사용자가 누른 pause는 명시적인 resume 전까지 어떤 자동 복구도 해제하지 않는다.

## 검증

- 종료 대기 task를 의도적으로 취소한 뒤 persistence task가 완료되는 async 회귀검사를 유지한다.
- 임시 SQLite 원장으로 파일 없음, 열린 LIVE Run, 종료된 Run의 READY fallback을 검증한다.
- 실행기 shell syntax와 PAPER 안전검사를 통과시킨다.
- 실제 배포에서는 열린 main·League PAPER 포지션 0에서 서비스를 교체한 뒤 같은 Run 자동복구, fresh quote 뒤 entry lock 해제, 실제 주문·인증 false를 확인한다.
- 실제 배포에서 수동 pause 뒤 서비스를 재시작해 같은 Run의 `MANUALLY_PAUSED`가 유지되는지 확인하고, resume 뒤 다시 재시작해 `RUNNING` 의도가 유지되는지도 확인한다.
- 서비스 종료 뒤 활성 원장의 `PRAGMA quick_check`와 `PRAGMA foreign_key_check`를 실행한다.

## 한계

- 컴퓨터가 꺼진 동안 localhost를 제공할 수 없다.
- 저장장치가 마운트되지 않았거나 원장을 읽을 수 없으면 READY로 시작하며, 이를 LIVE 복구 성공으로 표현하지 않는다.
- 복구 가능한 Run이 있어도 snapshot이나 시장데이터 재검증이 실패하면 신규 PAPER 진입은 계속 잠긴다.
