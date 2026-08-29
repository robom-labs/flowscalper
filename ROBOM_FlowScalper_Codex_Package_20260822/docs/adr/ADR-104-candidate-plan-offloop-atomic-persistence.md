# ADR-104. 후보계획의 LIVE 실행루프 밖 원자 저장

## 상태

수용. 2026-08-30 WAVE116I. 소스와 결정론 회귀에는 반영했으며 불변 서비스 설치 뒤 자연
진입 시점의 운영 검증은 별도 증거로 확정한다.

## 관찰

- 같은 Run `run-2b7135a972dd`에서 2026-08-30 02:34:44 KST에 BTCUSDT
  `AGGRESSOR_FLOW_CONTINUATION_V1` LONG 신호가 자연 조건으로 통과했다.
- BASE·STRESS 두 PAPER 계좌는 실제 공개 bid·ask를 사용해 진입했고, entry·TP1·TP2·SL·수량과
  최대계획손실을 진입 전에 확정했다. 실제 주문과 인증은 모두 0이었다.
- 해당 호가의 동기 처리 최대값은 564ms였고, ADR-095 단계계측은 같은 시각
  `CANDIDATE_PLANNING`을 557.977ms의 최장 단계로 기록했다. 이벤트루프 지연도 550ms였다.
- `_build_candidate_plans()` 안에는 후보가 생길 때마다 외장 SQLite의 `record_candidate()`
  `synchronous=FULL` 트랜잭션을 직접 호출하는 경로가 남아 있었다. 일반 무후보 평가에는 이
  호출이 없어서 장시간 0건 구간에서는 드러나지 않고 실제 자연 진입 순간에만 지연될 수 있었다.
- 같은 자연 거래는 1~3초에 종료되지 않았고 12분 이상 TP·SL 보호관리를 유지했다. 따라서 이번
  결함은 진입조건이나 조기종료 기준이 아니라 후보계획 저장의 실행경로 결합 문제다.

## 결정

1. `_build_candidate_plans()`는 불변 후보행을 메모리 buffer에 추가하고 SQLite를 직접 호출하지
   않는다.
2. LIVE async 소비기는 기존처럼 이벤트 판단을 마친 뒤 worker thread에서
   `_persist_execution_state_safely()`를 실행한다.
3. 후보계획, 주문, 체결, main·shadow 완료거래, 실행감사, 전략계좌와 복구 snapshot을
   `record_execution_state_batch()`의 한 `BEGIN IMMEDIATE`·한 `COMMIT`으로 저장한다.
4. 후보 buffer는 전체 트랜잭션이 성공한 뒤에만 비운다. 중간 행 실패 시 후보를 포함한 모든 행을
   rollback하고 buffer와 실행상태 식별자는 다음 안전 재시도에 남긴다.
5. 종료, Run 전환, venue failover와 저장 replay 직전에는 시장 batch뿐 아니라 후보·실행상태도
   함께 flush한다.
6. 고급진단에 `candidate_persistence_buffer`를 노출해 미확정 후보가 남았는지 확인한다.
7. 전략 신호, 비용, bid·ask 체결, 위험예산, TP1·TP2·SL, EDGE_DECAY와 최대보유시간은 바꾸지
   않는다. 거래를 만들기 위한 기준 완화는 하지 않는다.

## 회귀 계약

- `test_candidate_sqlite_commit_is_deferred_out_of_live_candidate_planning`은 후보계획 단계에서
  `record_candidate()` 직접 호출을 금지하고, 이후 원자 실행배치에서 후보가 저장되는지 검증한다.
- `test_execution_state_batch_commits_all_recovery_rows_together`는 후보부터 복구 snapshot까지 한
  트랜잭션으로 확정되는지 검증한다.
- `test_execution_state_batch_rolls_back_every_row_on_failure`는 자식 행 실패 시 후보행도 함께
  rollback되는지 검증한다.
- `CANDIDATE_PERSISTENCE_OFF_LIVE_LOOP` 누적 회귀계약이 위 anchor 삭제를 차단한다.

## 증거 경계

결정론 테스트 PASS는 동기 SQLite 호출 제거와 원자성만 증명한다. 설치 전 소스 테스트를 현재
8870 서비스의 운영 결과로 표현하지 않는다. 불변 설치 뒤 자연 후보가 다시 발생했을 때
`CANDIDATE_PLANNING`, event-loop 지연, queue, drop, 저장결함과 후보·주문·거래 원장 연결을 함께
확인해야 운영 검증이 완료된다. 6시간·24시간 안정성과 전략 수익성은 각각 실제 시간을 채우고
독립 표본 gate를 통과하기 전까지 `NOT_RUN`, `NOT_PROVEN`이다.
