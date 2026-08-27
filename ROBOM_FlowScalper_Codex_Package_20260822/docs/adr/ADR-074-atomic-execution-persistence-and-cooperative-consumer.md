# ADR-074 PAPER 실행상태 원자 저장과 이벤트 소비기 협력 양보

## 상태

Accepted.

## 배경

ADR-073 적용 뒤 진행한 Wave 97 깨끗한 20분 관찰은 처리 p95 105.918ms와 거래 p95
284.762ms를 기록했지만, 최대 queue 464와 event-loop 지연 874ms 때문에
`queue_bounded`, `event_loop_lag_bounded`가 실패했다. 재연결 1회는 계획 회전이었고
sequence gap, resync, drop, persistence fault, critical lag incident는 모두 증가하지 않았다.

최대 queue 구간은 `AGGRESSOR_FLOW_CONTINUATION_V1` BASE·STRESS 두 계좌의 자연
PAPER 거래가 열리고 닫힌 구간과 겹쳤다. 거래는 13.864초 보유 후 `EDGE_DECAY`로
종료됐으며 TP1·TP2·SL에는 도달하지 않았다. 따라서 이번 표본은 사용자가 지적한
1~3초 비정상 종료 재발은 아니지만, 한 실행 전이에서 주문, 체결, 거래, 감사, 전략 계좌와
복구 snapshot을 각각 별도 `synchronous=FULL` 트랜잭션으로 저장하는 구조를 드러냈다.

또한 event sink가 즉시 완료되는 구간에는 consumer가 준비된 queue를 연속으로 비우면서
다른 asyncio 작업에 명시적으로 실행권을 양보하지 않았다. 이 경로는 순서를 지키지만 UI,
watchdog과 제어 응답의 scheduling 기회를 늦출 수 있었다.

## 결정

1. 하나의 시장 이벤트에서 바뀐 주문, 체결, main 거래, shadow 거래, 실행 감사, 변경 계좌와
   복구 snapshot을 `record_execution_state_batch()` 한 트랜잭션으로 저장한다.
2. 손익 불변조건 확인, canonical JSON과 checksum 계산은 `BEGIN IMMEDIATE` 전에 끝내 writer
   잠금 보유시간을 줄인다.
3. 이미 저장한 식별자와 감사 offset은 전체 트랜잭션이 성공한 뒤에만 전진시킨다. 중간 행이
   실패하면 모든 행을 rollback하고 다음 안전 재시도에서 같은 상태를 다시 저장한다.
4. 실행상태 저장 횟수, 최근·최대 소요시간, 완료시각과 마지막 저장 항목 수를 고급진단 및
   실행 서비스 관찰 증거에 추가한다.
5. consumer는 연속 동기 판단이 10ms를 넘으면 `asyncio.sleep(0)`으로 실행권을 양보한다.
   이벤트 순서와 bounded queue, 오류·과부하 fail-close 계약은 그대로 유지한다.
6. 전략 임계값, 자연신호, TP1·TP2·SL, 보유정책, 비용, 위험예산, 11전략·22계좌와 실제주문
   0 경계는 변경하지 않는다.

## 결과

- 한 실행 전이의 복구 상태가 부분 저장되지 않고 한 시점으로 원자적으로 남는다.
- 자연 거래가 발생한 구간의 저장시간을 queue·event-loop 지연과 직접 대조할 수 있다.
- 즉시 완료되는 이벤트가 많아도 dashboard, watchdog과 제어 작업이 주기적으로 실행된다.
- 수정 릴리스의 실제 거래구간과 20분 이상 관찰에서 queue 64 이하, 500ms 초과 event-loop
  지연 0회를 다시 증명해야 한다. 실제 6시간·24시간을 채우기 전에는 둘 다 `NOT_RUN`이다.
- 위 자연 거래는 표본 30건 미만이고 비용 후 손실이므로 수익성은 `NOT_PROVEN`이다.
