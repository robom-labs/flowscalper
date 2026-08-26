# ADR-044 리비전 기반 PAPER 진입 의도와 비차단 기록 복구

## 상태

채택. 2026-08-26 Wave 38에서 구현하고 실제 로컬 서비스로 검증했다.

## 문제

사용자가 누른 신규진입 허용·일시정지 의도와 자동 안전잠금이 하나의 `paused` 값처럼 보였다. 그래서 자동 안전대기 중 재개 버튼의 의미가 모호했고, 중복 요청·오래된 화면의 요청·재시작 복구를 구분하는 revision과 idempotency 계약도 부족했다.

또한 2.57GB 활성 SQLite 원장이 있는 실제 서비스 재시작에서 HTTP 포트가 열리기 전 현재 전략 거래 cache를 동기 구축했다. 실측 내부 시작시간 165.615초 중 142.831초가 이 cache 준비에 쓰였다. 과거 재생 결과 API도 저장된 53개 결과의 전체 결정경로를 한 번에 보내 2,680,397 bytes가 되었고 첫 요청이 10초를 넘었다.

## 결정

1. 사용자의 PAPER 신규진입 의도를 `ENTRY_ENABLED`와 `ENTRY_PAUSED`로 별도 관리하고 자동 안전잠금과 분리한다.
2. 의도 변경은 `expected_revision` CAS와 `Idempotency-Key`를 받는다. 같은 key·같은 요청은 동일 결과를 돌려주고, 다른 요청이나 오래된 revision은 현재 상태를 포함한 409 충돌로 거절한다.
3. 각 변경은 actor, reason, revision과 함께 `PAPER_ENTRY_INTENT_TRANSITION` incident로 불변 기록한다. 같은 Run 복구와 자동 venue 전환에도 의도와 revision을 보존하고, 새 Run에서만 초기화한다.
4. 자동 안전대기 중 사용자의 진입 의도가 허용이어도 런타임은 계속 fail-closed한다. 화면은 안전대기 해제를 사용자 재개로 오해하지 않게 버튼을 비활성화하고 자동 복구 조건을 설명한다.
5. 기존 미종료 Run의 거래 cache는 HTTP 서비스가 열린 뒤 lifespan background task에서 준비한다. 복구·PAPER 안전 판정은 cache 준비와 분리하고, cache가 준비되기 전에는 검증되지 않은 통계를 노출하지 않는다.
6. replay Run 목록은 writer 연결과 별도의 query-only 연결로 읽고 source Run마다 최신 replay 하나만 기본 결과로 반환한다. API의 `decision_path`는 최근 20개로 제한하되 SQLite의 전체 저장 결과는 변경하거나 삭제하지 않는다.
7. 최신 source Run 조회를 위해 `replay_runs(source_run_id, created_at_ms DESC, replay_run_id DESC)` index를 추가한다.

## 검증과 경계

- 같은 실제 Run과 사용자 의도를 보존한 최종 재시작은 HTTP 응답 10.180초, 내부 시작 3.651초, 동기 cache 0초였다. background cache는 0.903초에 완료됐다.
- 실제 거래기록은 현재 버전 43건을 9.068ms에 반환했고, 과거 재생 Run 79개는 3.811ms에 반환했다.
- replay 결과는 source Run별 최신 16개·33,397 bytes·결정경로 최대 20개였고 첫 요청 84.871ms, 반복 요청 2.082ms였다.
- 현재 2,135,559-event Run의 정밀 이벤트 100건 최초 화면 로딩은 약 14.7초여서 `PASS_WITH_LIMIT`다. 동일 화면 재로딩은 약 0.9초였다. 전체 저장 이벤트 전략검증과 원장 원본은 축약하지 않는다.
- 60초 실제 LIVE 표본에서 event는 4,291건 전진했고 실행경로 p95 최대 36.001ms, queue 최대 5, 비계획 reconnect·gap·resync·drop·저장 fault·buffer drop은 0이었다.
- 전략 기준·비용·TP/SL은 거래 수나 승률을 만들기 위해 낮추지 않았다. 전략별 자연 BASE 표본은 0~6건이므로 수익성은 `NOT_PROVEN`이다.
- 수정 후 6시간·24시간 soak와 활성 원장 full quick check는 실행하지 않았으므로 `NOT_RUN`이다.

## 결과

사용자 의도와 자동 안전상태를 감사 가능하게 분리했고, 큰 원장이 HTTP 시작과 기록·재생 기본 화면을 막던 경로를 제거했다. 이 결정은 실제 주문·private API·인증 경로를 추가하지 않으며 PAPER 전용 불변조건을 유지한다.
