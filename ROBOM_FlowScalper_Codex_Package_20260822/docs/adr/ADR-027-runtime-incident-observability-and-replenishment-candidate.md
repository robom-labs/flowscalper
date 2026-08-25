# ADR-027. 런타임 지연 사건 관측과 유동성 재충전 후보 기각

- 상태: 채택.
- 날짜: 2026-08-25.

## 배경

단일 현재 p95와 최대 저장시간만으로는 짧은 외부 공개시장 지연, 이벤트 수신 공백과 별도 저장 process의 느린 flush가 같은 시각에 발생했는지 사후 대조하기 어렵다. 또한 Liquidity replenishment와 limit-order-book resiliency 연구는 충격 뒤 호가 복구 속도가 정보가 될 수 있음을 뒷받침하지만, 그 사실만으로 암호화폐 PAPER 스캘핑의 비용후 우위를 증명하지 않는다.

참고 연구는 Large의 전자 지정가호가장 복원력 측정 연구([DOI 10.1016/j.finmar.2006.09.001](https://doi.org/10.1016/j.finmar.2006.09.001))와 Cont·Kukanov·Stoikov의 깊이로 정규화한 order-flow imbalance 가격충격 연구([DOI 10.1093/jjfinec/nbt003](https://doi.org/10.1093/jjfinec/nbt003))다.

## 결정

1. 기존 실행호가 p95 1,500ms fail-closed 기준과 자동회복 정책은 바꾸지 않는다.
2. 각 임계지연 전이의 시작·복구 시각, 최근·최장 지속시간과 사건 수를 남긴다.
3. 수신 monotonic 시각 기준 최근·최대 이벤트 공백, 500ms 초과 횟수와 최근 발생시각을 남긴다.
4. 시장 저장 flush의 최근 완료·최대 발생·2초 이상 발생 횟수와 시각을 남긴다. 저장과 시장 지연의 인과는 시각이 겹친다는 추가 증거 없이 단정하지 않는다.
5. 후보 K `LIQUIDITY_REPLENISHMENT_FAILURE_CONTINUATION`은 Registry에 추가하지 않는다. 12개 저장 `LIVE_PUBLIC` Run에서 현재 snapshot 이전 정보만으로 750ms 지속성을 확인하고 실제 ask·bid에서 진입·15초 종료한 연구 표본은 train 88개와 최신 holdout 25개였다. holdout 총수익 평균은 1.46bp였지만 BASE 13bp를 반영한 평균은 -11.54bp였고, train BASE 비용후 평균도 -13.196bp였다. 자연신호를 만들기 위해 임계값을 낮추지 않는다.

## 결과

- 다음 임계지연이나 멈춤 의심 시 정확한 사건시간과 저장시간을 대조할 수 있다.
- 이벤트 공백은 원인 확정값이 아니라 진단값이며, 실제 실행호가 p95·잠금·reconnect·gap·drop과 함께 해석한다.
- 검증되지 않은 후보를 전략 수만 늘리기 위해 추가하지 않는다.
- 실제 주문, private API, 인증, API Key, secret과 wallet 경로는 계속 0 또는 false다.
