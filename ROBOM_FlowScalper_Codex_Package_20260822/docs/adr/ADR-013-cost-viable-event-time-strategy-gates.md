# ADR-013. 비용후 실행가능 계획과 실제 event-time 진입 확인

## 상태

Accepted, 2026-08-24.

## 문제

저장된 공개시장 Run `run-f14214b3b1dd`의 15,045개 이벤트를 기존 코드로 재생하면 41,628번의 전략 평가와 19개의 적격 신호가 나왔지만 CandidatePlan은 한 건도 생성되지 않았다. 모든 적격 신호가 최종 `LIVE_PLAN_INADEQUATE_NET_REWARD_RISK`에서 거부됐다.

원인은 두 단계의 비용 게이트가 서로 다른 가격 구조를 사용한 데 있었다. 전략 평가는 최소 13bp 왕복비용과 3.2R target으로 통과했지만, 최종 planner는 실행가능 bid·ask, worst entry, 양방향 fee, 예상 exit slippage와 REVERSION 70/30 또는 TREND 40/60 분할청산을 다시 적용했다. 기존 0.15~0.20% stop 거리는 최종 순손익비 1.20을 구조적으로 만족시키지 못했다.

또한 A~D runtime adapter는 `pullback_seconds`, `reentry_confirmation_ms`, `confirmation_ms` 같은 시간 필드에 현재 snapshot만 보고 고정 통과값을 넣었다. 이는 문서의 지속성 요구와 달리 단일 update를 여러 update의 확인처럼 취급할 수 있었다. 실제 브라우저 검증 중 CBR PAPER가 진입 약 1초 뒤 edge decay로 종료돼 이 거짓 양성 가능성을 재현했다.

## 결정

1. 최소 순손익비 1.20, 비용, 슬리피지와 전략 신호 임계값은 낮추지 않는다.
2. REVERSION 전략 A/C의 최소 구조위험 거리는 기준가격의 0.80%, TREND 전략 B/D/E/F는 0.30%로 둔다. 더 먼 stop은 수량을 늘리는 근거가 아니며 기존 위험예산이 수량을 줄인다.
3. 전략별 exit style은 평가 전부터 확정하고 최종 CandidatePlanner와 같은 REVERSION 70/30 또는 TREND 40/60 계약을 사용한다.
4. A의 refill·재진입, C의 구조 재진입, B/D의 재가속 확인은 실제 event timestamp로 지속시간을 누적하고 조건이 깨지면 즉시 초기화한다.
5. B/D의 눌림 시간과 되돌림 비율은 현재 이전의 같은 종목 가격 prefix에서 impulse peak, pullback low와 현재 재가속을 계산한다. 현재보다 미래 timestamp인 표본은 무시한다.
6. E/F의 기존 500ms event-time 지속성은 유지한다.
7. 리플레이 후보 수는 main과 BASE/STRESS에 중복 배포된 audit 행 수가 아니라 고유 `candidate_id` 수로 센다.
8. 이 값들은 PAPER 연구 기본값이다. 테스트 통과나 짧은 리플레이를 수익성 증명으로 표현하지 않고, 최소 30개 완료 표본 전에는 UI의 `표본 부족`을 유지한다.

## 검증

- 6전략 × LONG/SHORT의 12개 계획이 최종 실행가능 호가·비용 게이트에서도 순손익비 1.20 이상인지 검증한다.
- 6전략 × LONG/SHORT × TP/STOP의 24개 종단 시나리오에서 진입 직후 TP1·TP2·SL 보호주문, 부분익절, 손절, 비용과 회계를 검증한다.
- 눌림 계산은 롱·숏 대칭, event time, 가격 재가속과 no-lookahead를 검증한다.
- 저장 공개시장 15,045개 이벤트를 두 번 재생해 checksum과 모든 집계가 같은지 검증한다.
- 실제 브라우저에서 READY→연결 중→작동 중과 PAPER·실제 주문 0을 확인한다.

## 결과와 한계

최종 공개시장 재생은 두 번 모두 checksum `f0c9ea71ef2952b35c0b86f68f284676bd6714f64376b0ffa1a00549dd8b2275`, 평가 41,628, 적격 8, 고유 후보 5, shadow 종료거래 7이었다. 엄격한 event-time 적용 후 이 짧은 표본에서는 E만 실행 후보를 만들었고 main A/B 거래는 0이었다. 이는 기준을 낮추지 않은 결과이며 A~F의 수익성이나 장시간 안정성을 증명하지 않는다. 6시간·24시간 soak와 충분한 전략별 표본은 별도 검증이 필요하다.
