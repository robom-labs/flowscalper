# ADR-022. Snapshot 내 전략 계획 입력 재사용

## 상태

Accepted, 2026-08-25.

## 맥락

Strategy Registry가 A~J 10개로 늘어난 뒤 하나의 공개시장 feature snapshot을 평가할 때 동일한 방향과 청산형식의 entry·TP1·TP2·SL·수량·비용 계획을 전략마다 다시 만들었다. 기존 경로는 전략 10개와 LONG·SHORT 2개를 평가하면서 기본 계획을 먼저 만들고 E~J에서 추세형 계획을 다시 만들어 snapshot마다 `_plan`을 최대 32회 호출했다.

계획은 현재 snapshot, 방향, tick size와 전략 descriptor의 청산형식만으로 결정된다. 같은 snapshot 안에서 `(Side, ExitStyle)`이 같으면 입력과 결과가 같으므로 반복 계산은 전략 독립성이나 보수적 PAPER 체결을 강화하지 않고 공개시장 처리 CPU만 사용한다.

## 결정

1. `StrategySignalEvaluator.evaluate`가 snapshot마다 `(Side, ExitStyle)`을 key로 `PlanInputs`를 지연 생성해 재사용한다.
2. 가능한 조합은 LONG·SHORT와 `REVERSION_70_30`·`TREND_40_60`의 곱인 최대 4개다.
3. 각 전략의 신호 조건, event-time 지속성, Registry mode, 독립 PAPER 계좌와 체결·회계 경로는 바꾸지 않는다.
4. cache 수명은 단일 `evaluate` 호출로 제한한다. snapshot 사이에는 공유하지 않아 오래된 호가·비용·수량을 재사용하지 않는다.
5. 전략 구현 revision은 바꾸지 않는다. 계산 순서만 줄이고 저장 replay checksum과 전략 집계가 동일해야 한다.
6. 실제 주문, private API, 인증, API Key, secret과 wallet 경로는 계속 없다.

## 검증

- 10개 전략과 양방향 20개 결정을 평가해 `_plan` 호출이 서로 다른 4개 `(Side, ExitStyle)` 조합과 정확히 일치하는지 회귀검사한다.
- 저장된 동일 공개시장 Run을 수정본으로 두 번 replay해 event 수, 평가 수, 적격·후보·main·shadow 집계와 checksum이 일치하는지 확인한다.
- 실제 8870 PAPER 런을 시작하고 기본 15분 WebSocket 회전 전후의 실행경로 P95, critical lag, 진입잠금, 비정상 재연결, gap, drop과 저장 fault를 관찰한다.
- 전체 backend·frontend·브라우저·정적·보안 검증을 다시 실행한다.

## 결과와 한계

이 변경은 같은 snapshot의 완전히 동일한 계획 계산을 공유하는 성능 수정이다. 15,045개 저장 공개시장 이벤트를 수정본으로 두 번 replay한 결과 두 실행 모두 checksum `5880f66a673ad64d01dec42853d59e3208497fc6ab6ba6520737b7553bccc94b`, 평가 69,380·적격 9·후보 8·main 0·shadow 9로 이전 기준과 일치했다.

짧은 단위검사나 replay 결정성만으로 6시간·24시간 성능 또는 전략 수익성을 증명하지 않는다. 실제 장시간 런에서 별도 저장과 메모리 일괄 폐기도 지연 원인으로 확인돼 ADR-023으로 분리해 수정했다.
