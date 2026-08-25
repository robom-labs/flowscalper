# ADR-039. 사전등록 장중 연구와 실행 레지스트리 분리

- 상태는 Accepted다.
- 날짜는 2026-08-26이다.

## 배경

기존 A~J microstructure 전략은 공개 호가·체결의 sub-minute 신호를 보수적으로 실행한다. 단기 이동폭이 비용을 넘지 못한 표본이 있었지만, 승률을 올리기 위해 방향을 즉시 뒤집거나 임계값을 낮추면 선택 편향과 과매매를 키운다. 동시에 1분부터 4시간까지의 완성 봉 계약과 연구용 multi-timeframe 계층이 없어서 더 긴 horizon 가설을 같은 no-lookahead·bid/ask·비용 기준으로 비교하기 어려웠다.

## 결정

1. 실행 `StrategyRegistry`와 별도로 `backend/app/intraday/` 연구 계층을 둔다.
2. 거래 event에서 canonical completed candle을 만들고, 중복·out-of-order·미래 봉은 fail-closed한다.
3. MICRO_SCALP, FAST_INTRADAY, INTRADAY_SWING별 피처, 최대보유시간, purge·embargo를 분리한다.
4. ORIGINAL, 동일 정보집합의 MECHANICAL_MIRROR, 별도 조건의 HYPOTHESIS_REVERSE를 비교한다.
5. 실제 반대쪽 bid·ask, BASE·STRESS 비용, TP1·TP2·SL과 censored 거래 규칙을 모든 후보에 공통 적용한다.
6. 12개 시간구간 조합 × 5개 family × 3개 variant의 180개 사전등록 grid를 고정한다. 무신호 가설도 trial count에서 제외하지 않는다.
7. Train 6 Run, Validation 2 Run, OOS 5 Run의 시간순 split과 horizon별 purge·embargo를 사용한다.
8. PBO, DSR, deterministic bootstrap, no-trade, mirror parity, dataset·config·code·result hash를 기록한다.
9. 연구 결과는 Registry를 직접 변경하지 않는다. OOS gate를 통과해도 신규 ID와 별도 SHADOW 승인 전에는 실행 전략이 아니다.

## 결과

- 기존 microstructure 실행·PAPER 원장·독립계좌는 바뀌지 않는다.
- 연구용 계산은 더 무겁지만 단일 archive pass와 bounded DuckDB 설정으로 반복 가능하게 제한한다.
- 신호가 없는 가설이 숨지 않고 다중검정 분모에 남는다.
- 높은 승률, 낮은 PBO 또는 단위 테스트 PASS 하나만으로 전략을 승격할 수 없다.
- 기존 전략 수와 계좌 수는 Registry payload에서 동적으로 계산되므로, 향후 승인된 신규 ID가 추가돼도 생산 코드의 10·20 하드코딩을 바꾸지 않는다.

## 기각한 대안

- 저성과 전략을 즉시 LONG↔SHORT로 뒤집는 방식은 별도 가설이 아니며 비용·시장충격 때문에 기각했다.
- 진행 중 candle을 사용해 신호 수를 늘리는 방식은 미래 정보·재도색 위험 때문에 기각했다.
- RSI, MACD, 이동평균 조합만 이름을 바꿔 여러 전략으로 등록하는 방식은 독립 alpha 가설과 ablation이 없어 기각했다.
- OOS 성과만 보고 자동으로 ACTIVE를 교체하는 방식은 자연 LIVE 표본, 운영 안정성, manual lock과 rollback 검증을 건너뛰므로 기각했다.

## 검증 계약

- candle aggregation, duplicate, out-of-order와 completed-only no-lookahead 단위검사.
- session VWAP, higher-timeframe as-of, candidate/reverse 분리 단위검사.
- mirror signal timestamp·정보집합·stop/target 대칭과 signal-count parity 검사.
- 실제 bid·ask 진입·청산, stop 우선, staged target, horizon별 purge·embargo 검사.
- 전체 사전등록 180개와 promotable trial 120개 고정 검사.
- 전체 archive JSON·HTML 연구 출력과 manifest checksum 재현성 검사.
- 실제 장시간 서비스 검증은 연구 결과와 별도 PASS·FAIL·NOT_RUN으로 기록한다.
