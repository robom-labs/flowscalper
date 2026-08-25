# ADR-034. 전체 런타임 전략 비용후 선별과 LSA 기본 중지

- 상태: Accepted
- 날짜: 2026-08-25
- 범위: A~J 런타임 전략의 저장 `LIVE_PUBLIC` 시간순 선별과 기본 mode

## 배경

현재 자연 PAPER 표본에서 A `LSA_REVERSAL_V1`은 현재 전략 revision 기준 BASE 4건이 모두 손실이었다. 표본이 적다는 이유만으로 이 결과를 일반화할 수는 없지만, 신규 손실 노출을 계속 허용하기 전에 실제 런타임 evaluator 자체를 저장 공개시장 데이터에서 독립적으로 선별할 필요가 있었다.

## 검증 방법

`scripts/research_strategy_revision.py`가 A~J의 실제 `StrategyRegistry`와 `StrategySignalEvaluator`를 호출하도록 확장했다. 저장된 `LIVE_PUBLIC` 13개 Run을 시간순 train 8개와 더 늦은 holdout 5개로 나누고, 같은 시점 이전의 피처만 사용했다. 500ms 평가, 실제 ask·bid 진입과 반대호가 청산, 30초 고정 horizon, BASE 13bp와 STRESS 25bp를 적용했다. 고정 horizon은 전략 전체 청산정책의 수익성을 증명하지 않으며, 동일 조건의 비용후 방향성 선별에만 사용한다.

- A는 train 25건에서 BASE 승률 8%, 기대값 -21.139bp, PF 0.072였다. 후기 holdout 10건은 승리 0건, 기대값 -13.767bp, PF 0이었다.
- B는 train 1건, holdout 0건으로 수익성을 판단할 수 없었다.
- C는 train 4건, holdout 1건으로 부족했고 양쪽 모두 BASE 기대값이 음수였다.
- D는 train 4건, F는 train 3건이었고 BASE 승리는 없었다. holdout 표본은 없었다.
- G/I/J는 train과 holdout에서 자연신호가 없었다.
- E/H는 ADR-032의 대량 표본 실패를 다시 확인했다.

## 결정

1. A는 `SHADOW`에서 기본 `OFF`로 내리고 `EXPERIMENTAL`로 표시한다. 과거 원장, 코드, LONG·SHORT 제어와 사용자의 명시적 재활성화 기능은 보존한다.
2. B는 유일한 기본 `ACTIVE`로 유지하지만 표본 1건 때문에 수익성은 `NOT_PROVEN`이다.
3. C/D/F/G/I/J는 엄격한 자연신호를 계속 수집하도록 `SHADOW`로 유지한다. 부족한 표본을 만들기 위해 기준을 낮추지 않는다.
4. E/H는 기존 `OFF`를 유지한다. 기본 상태는 7개 감시, 3개 검증 중지다.
5. 구현 revision을 `2026-08-25-wave32`로 올려 이전 revision 표본과 성과를 분리한다.
6. 실제 주문, private API, API Key, secret, wallet과 실거래 경로는 계속 0이다.

## 결과와 한계

A의 신규 PAPER 진입은 기본값에서 중지되므로 이미 반복 확인된 비용후 손실 노출을 줄인다. 이것은 높은 승률이나 수익성을 만든 결과가 아니다. B와 SHADOW 전략도 충분한 현재 revision 자연표본 전에는 순위, 우수전략 또는 수익성 주장을 하지 않는다. 6시간·24시간 장시간 안정성 검증도 별도 `NOT_RUN` 상태로 남긴다.
