# ADR-021. 호가 기울기 비대칭 SHADOW 전략

## 상태

Accepted, 2026-08-25.

## 맥락

A~I는 쓸기 반전, 압축·OFI·aggressor flow·queue imbalance·top10 microprice와 단기수익률 동행을 이미 평가한다. 같은 변수의 임계값만 바꾼 전략은 독립 가설이 아니며 Strategy League 표본을 불필요하게 늘린다.

Næs와 Skjeltorp의 원 논문은 호가장 기울기가 거래량·변동성과 유의한 관계를 가진다고 보고한다. Cenesizoglu, Dionne, Zhou의 원 논문은 매수·매도 및 낮은·높은 호가 단계의 기울기가 가격동학에 서로 다른 영향을 줄 수 있음을 분석한다. Binance 공식 공개 WebSocket 문서는 diff depth가 가격 단계별 새 수량과 sequence ID를 제공하고, partial depth가 상위 5·10·20단계를 제공함을 명시한다. 이는 공개 top10 snapshot에서 양쪽 기울기를 계산할 입력 근거다.

- Næs, R. and Skjeltorp, J. A., *Order Book Characteristics and the Volume-Volatility Relation*, DOI 10.1016/j.finmar.2006.04.001, https://papers.ssrn.com/sol3/papers.cfm?abstract_id=565323
- Cenesizoglu, T., Dionne, G. and Zhou, X., *Asymmetric Effects of the Limit Order Book on Price Dynamics*, DOI 10.1016/j.jempfin.2021.11.002, https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2878945
- Binance Spot API, *WebSocket Streams*, https://developers.binance.com/en/docs/binance-spot-api-docs/web-socket-streams

위 연구는 암호화폐 PAPER 전략의 수익성을 증명하지 않는다. 신호 설계의 측정 근거일 뿐이며 장기간 독립 표본으로 반증 가능하게 유지해야 한다.

## 결정

1. `BOOK_SLOPE_ASYMMETRY_V1`을 J 전략으로 추가한다.
2. top10 각 단계에서 중간가격까지 거리 bp와 누적 명목깊이를 계산하고, 거리 1bp당 누적 명목깊이의 평균을 bid·ask별 기울기로 정의한다.
3. 통계는 현재 snapshot을 넣기 전 동일 종목 최대 1,200개 과거창만 사용한다.
4. LONG은 ask 기울기 percentile 0.15 이하, bid percentile 0.50 이상, bid/ask 기울기비 1.50 이상을 요구한다. SHORT는 정확히 대칭이다.
5. 최소 과거표본 32개, spread 8bp 이하, 250ms·3초 OFI, 1초 aggressor flow, microprice 0.15bp, 가격반응효율 0.25와 1,000ms 지속을 함께 요구한다.
6. WARMUP·DEGRADED·SHOCK와 공통 비용후 순손익비 미달은 거부한다.
7. J는 EXPERIMENTAL·SHADOW·LONG/SHORT 기본 켜짐이며 자동 승격·자동 중지·임계 완화를 하지 않는다.
8. 레지스트리는 A~J 10개 전략과 BASE·STRESS 20개 독립 PAPER 계좌가 된다.
9. 실제 주문, private API, 인증, API Key, secret과 wallet 경로는 계속 없다.

## 검증

- top10 기울기 계산의 결정성·유한성과 양쪽 차이를 단위검사한다.
- J의 LONG·SHORT 대칭, 과거표본·percentile·기울기비·지속·비용 거절을 검사한다.
- 같은 저장 공개시장 Run을 두 번 replay해 checksum과 평가·후보·거래 집계가 같은지 확인한다.
- 전체 10전략의 양방향 TP1·TP2·SL, BASE·STRESS 회계와 복구를 회귀검사한다.
- 실제 8870 화면에서 10행·20방향·20계좌, SHADOW J와 실제 주문 0을 확인한다.

## 결과와 한계

새 전략 버전은 이전 구현 revision의 거래를 현재 성과에서 분리한다. 과거 원장은 삭제하지 않는다. 자연 적격신호나 완료 거래가 없으면 `NOT_PROVEN`으로 기록하며 기준을 낮추지 않는다. 짧은 replay·브라우저·soak는 수익성 증거가 아니다.
