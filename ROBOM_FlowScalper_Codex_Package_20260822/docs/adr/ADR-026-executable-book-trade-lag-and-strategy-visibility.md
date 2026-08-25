# ADR-026. 실행호가·체결 지연 분리와 전략 감시 가시성

## 상태

Accepted, 2026-08-25.

## 맥락

기존 supervisor는 주문장 `DEPTH_UPDATE`와 체결 `TRADE`의 지연을 한 실행경로 분위수에 섞었다. 공개 `aggTrade`가 늦게 도착한 짧은 구간에는 실제 진입가격을 결정하는 bid·ask 주문장이 정상이어도 전체 p95와 `critical_lag_event_count`가 함께 상승해, 사용자가 화면에서 원인을 구분하기 어려웠다. 반대로 늦은 체결을 전략 피처에 그대로 넣으면 과거 체결이 현재 candle과 체결흐름을 뒤늦게 바꾸는 문제가 생긴다.

Binance USDⓈ-M 공식 문서에서 aggregate trade stream은 거래 이벤트를 실시간으로 제공하고, diff depth stream은 100ms·250ms·500ms 갱신속도를 선택할 수 있으며, individual book ticker는 최우선 bid·ask 변화의 실시간 stream이다. 이 계약은 서로 다른 공개 stream의 전달 지연을 한 숫자로 축약하지 않고 목적별로 관측해야 한다는 기술 근거다.

- [Binance Aggregate Trade Streams](https://developers.binance.com/en/docs/derivatives-trading-usds-futures/websocket-market-streams/Aggregate-Trade-Streams)
- [Binance Diff. Book Depth Streams](https://developers.binance.com/en/docs/derivatives-trading-usds-futures/websocket-market-streams/Diff-Book-Depth-Streams)
- [Binance Individual Symbol Book Ticker Streams](https://developers.binance.com/en/docs/derivatives-trading-usds-futures/websocket-market-streams/Individual-Symbol-Book-Ticker-Streams)

## 결정

1. 신규 PAPER 진입가격과 체결 가능성을 판단하는 실행호가 p95는 sequence-valid `DEPTH_UPDATE`·`ORDERBOOK`만으로 계산한다.
2. `TRADE` 지연 p95와 50종목 wide scanner 지연 p95는 각각 별도 telemetry로 기록한다. wide scanner는 후보선정용 관찰이며 진입판정 호가가 아님을 시스템 화면에 명시한다.
3. Binance `aggTrade`가 거래소 보정시각 기준 500ms보다 늦으면 `TRADE_LAG_STALE`로 기록하고 불변 공개시장 archive에는 보존하되, 해당 늦은 이벤트를 candle·FeatureEngine·전략평가에는 넣지 않는다.
4. 한 종목의 늦은 trade가 관찰된 동안 그 종목의 다음 주문장 feature는 `data_healthy=false`로 전달한다. 신선한 trade가 도착해야 자동 회복한다. 늦은 체결을 무시했다고 전략을 fail-open하지 않는다.
5. 실행호가 p95 1,500ms, sequence gap, 저장·복구·위험 잠금은 기존대로 모든 신규 PAPER 진입을 fail-closed한다. 신호·비용·TP·SL·위험 임계값은 낮추지 않는다.
6. 전략 화면은 각 전략이 평가한 방향·비용 경로 수와 가장 최근 거절 이유를 초보자용 문구로 표시한다. `정상 감시 중`, `준비 중`, `PAPER 진입 중`, `안전 대기`, `확인 필요`, `꺼짐`을 구분한다.
7. 차트는 선택한 종목의 열린 PAPER 포지션이 있으면 방향, 전략, BASE·STRESS, entry, TP1, SL과 같은 종목의 추가 진행 건수를 차트 위에 표시한다. 시장 화면에는 현재 모든 PAPER 진입 목록을 별도로 제공한다.
8. 실제 주문, private API, 인증, API Key, secret과 wallet 경로는 계속 0이다.

## 검증

- supervisor 단위검사는 1,600ms `TRADE`가 trade telemetry에는 들어가지만 실행호가 entry lock을 직접 켜지 않는지 확인한다.
- Binance adapter 단위검사는 500ms를 넘긴 aggregate trade가 `is_stale=true`, `TRADE_LAG_STALE`이 되는지 확인한다.
- 런타임 단위검사는 늦은 trade가 candle·FeatureEngine을 바꾸지 않고 신선한 trade 뒤 해당 종목이 회복되는지 확인한다.
- UI 단위검사와 실제 Chromium E2E는 차트의 열린 포지션 banner, 전체 진행 목록, 전략별 감시상태·경로 수, 시스템의 호가·체결·scanner 분리와 반응형 overflow를 확인한다.
- 실제 서비스에서는 시작 한 번으로 `RUNNING`에 도달한 뒤 실제 실행호가 p95, 체결 p95, wide scanner p95, queue, gap, drop, reconnect, persistence fault와 전략 10개의 평가경로를 함께 기록한다.

## 실제 결과와 한계

수정 전 독립 공개 WebSocket 8초 표본에서 BTCUSDT·SOLUSDT·BNBUSDT depth의 p50은 20.3ms, p95는 21.63ms, 최대는 24.83ms였고 1,500ms 초과는 0건이었다. 수정 뒤 실제 `run-b39e9a83991b`에서는 실행호가·체결 p95가 보통 수십~수백 ms, 50종목 wide scanner p95가 약 1.5~1.7초로 서로 분리됐다. queue·비계획 reconnect·gap·drop·persistence fault는 관찰 표본에서 0이었고 실행호가 critical active와 entry lock은 false였다.

실제 브라우저에서 자연스럽게 열린 PAPER 포지션 6건이 목록과 차트 banner에 표시됐고, 이후 자연 종료 뒤 진행 거래가 0건으로 바뀌었다. 전략 화면에서는 A~J 10개 모두 12종목×LONG·SHORT의 24개 경로를 평가했고, 진입하지 않은 전략은 최근 조건 대기 이유를 표시했다. 이는 모든 전략이 현재 구현경로를 실행한다는 증거이지 전략 수익성이나 장시간 무지연을 증명하지 않는다. 6시간·24시간 안정성과 충분한 전략별 성과 표본은 별도 검증이다.
