# ADR-099. 전 전략 일괄 gate 리플레이

## 상태

채택한다. 동결 공개시장 이벤트 한 벌에 같은 진입 veto를 비교할 때 등록 전략마다 archive를 다시
읽지 않고, 한 무원장 PAPER 런타임에서 11전략·22개 BASE·STRESS 계좌를 동시에 평가한다.

## 문제

기존 Strategy League 리플레이는 11전략을 한 번에 평가했지만 연구 gate 대상은 한 전략 ID만
허용했다. TP1 비용·도달가능성처럼 모든 전략에 동일하게 적용할 veto를 비교하려면 269만 이벤트를
전략마다 최대 11번 다시 읽어야 했다. 실행시간과 I/O가 늘고, 서로 다른 실행 시점의 archive 또는
런타임 상태를 잘못 섞을 위험도 커진다.

## 결정

1. `ALL_REGISTERED_STRATEGIES` 대상을 명시하면 기존 전략이 `QUALIFIED`한 경우에만 같은 gate를
   적용한다. Gate는 신호를 만들거나 방향을 바꾸지 못한다.
2. 각 전략은 적용 전 통과, 적용 후 통과, gate 거부 수와 이유를 독립 집계한다.
3. 단일전략 replay에서는 전체대상을 거부하고, 등록 전략 전체 replay에서만 허용한다.
4. 모든 전략은 같은 event order, archive checksum, commit, 전략버전과 BASE·STRESS 비용을 쓴다.
5. 현재 LIVE 공개시장 서비스의 queue·지연·event 전진·저장·PAPER 안전을 감시하는 기존 자동중단
   wrapper를 그대로 사용한다.
6. 일괄 후보와 기준선은 같은 구현 commit에서 각각 한 번 실행하고 Run·이벤트·archive byte를
   대조한다. 이전 구현 기준선은 참고결과로 보존하되 후보 승격 비교에는 쓰지 않는다.
7. 이 최적화는 계산을 줄일 뿐 30개 독립기회, BASE·STRESS, OOS, bootstrap, DSR, PBO, drawdown,
   집중도와 현재버전 forward gate를 낮추지 않는다.
8. 결과는 PAPER 연구 전용이며 실제 주문, 인증, private API와 wallet 경로는 계속 0이다.
9. live-safe wrapper는 `/tmp/robom-flowscalper-strategy-league-replay.lock`을 실행 전체 동안 배타적으로
   잠근다. 같은 archive를 읽는 두 번째 연구 리플레이는 시작 전에 차단해 LIVE PAPER와 첫 리플레이의
   CPU·I/O를 서로 오염시키지 않는다.

## 검증

- `backend/tests/test_runtime_strategy_research_replay.py`는 모든 등록전략이 gate 대상으로 표시되고
  전략별 적용 전 수가 적용 후 통과와 거부의 합과 같은지 검증한다.
- `backend/tests/test_live_safe_strategy_league_runner.py`는 LIVE 안전 wrapper가 전체대상을 자식
  명령에 보존하고 동시에 한 archive 리플레이만 허용하는지 검증한다.
- `scripts/compare_all_strategy_gate_trials.py`는 한 일괄 후보를 전략별 가상 단일대상 결과로
  분리한 뒤 기존 엄격 비교기를 재사용하고, 공통 입력·전략별 gate 회계·계획 수 증가를 먼저
  fail-closed로 검사한다.
- `backend/tests/test_strategy_gate_trial_comparison.py`는 일괄 비교 완료, 대상 누락·회계 불일치와
  후보 계획 수 증가의 무효화를 검증한다.
- `config/regression_contracts.json`은 이후 변경에서 일괄 경로와 테스트가 사라지면 CI를 실패시킨다.
