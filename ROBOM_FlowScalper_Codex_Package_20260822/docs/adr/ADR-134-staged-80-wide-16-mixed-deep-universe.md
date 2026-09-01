# ADR-134. 80개 넓은 감시와 16개 혼합 정밀분석의 단계 확장

## 상태

Accepted for measured release validation, 2026-09-01.

## 문제

설치 서비스는 50개 공개 ticker와 12개 depth·trade 종목을 정상 처리했지만, 최근 실제 12개 scanner 행은 모두 전략 조건에서 탈락했다. event, 전략평가, 저장과 WebSocket은 전진했고 queue, drop, gap, resync, 비계획 reconnect와 persistence fault는 0이므로 이 관찰창의 무진입은 엔진 정지가 아니었다.

706개 안팎으로 보이는 화면 숫자는 Binance·Upbit 전체 공개 카탈로그다. 모든 종목을 depth·trade·15개 전략으로 정밀 평가한다는 뜻이 아니다. 이를 그대로 정밀분석하면 CPU, 네트워크, snapshot REST, 저장량과 replay 비용이 함께 늘고 저유동성 종목의 비용 왜곡도 커진다.

ADR-025는 단일 실행 루프와 20개 deep에서 실행호가 p95 1,500ms 초과와 비계획 재연결을 보존했다. 현재는 전략 평가 전용 process, 2초 평가 간격, dashboard cache와 외부 저장 worker가 적용됐지만 기준선 process CPU가 약 46%이고 500ms 초과 event-loop 지연 이력도 있으므로 20개로 즉시 복귀할 근거는 없다.

## 외부 근거의 사용 경계

- Freqtrade 공식 문서는 거래대금 상위 `number_assets: 20` 예와 Volume·PercentChange·Spread·Volatility 필터의 연쇄 사용을 제공하고, 전체 시장 candle 계산은 자원 소모가 크므로 먼저 가벼운 필터로 범위를 줄이라고 권한다.
- QuantConnect 공식 crypto universe 예는 USD 거래대금 상위 10개를 선택한다.
- Bybit 공식 V5 public WebSocket은 Futures topic 수를 현재 별도로 제한하지 않지만 한 요청의 `args` 길이를 21,000자로 제한한다.
- 이 수치는 일반적인 계층형 선별 방식의 근거일 뿐 ROBOM의 적정 처리량이나 수익성을 증명하지 않는다. 최종 상한은 현재 코드와 실제 설치 장비의 측정으로 정한다.

## 결정

1. persistent 공개시장 목표를 wide 80, deep 16으로 단계 확장한다.
2. wide는 active USDT perpetual 중 공개 24시간 quote turnover 상위 80개다.
3. deep 16은 wide 안의 거래대금 상위 8개와, 중복을 제거한 절대 24시간 가격변동 상위 8개로 구성한다. 이는 상승·하락 기회를 함께 포함하며 방향과 실제 진입은 기존 LONG·SHORT 전략 gate가 결정한다.
4. Binance `priceChangePercent`와 Bybit `price24hPcnt`를 같은 percent 단위로 정규화한다. 값이 없으면 0으로 두며 비유한 값은 공개 응답 오류로 거부한다.
5. pin, 복구, open, pending 종목 보호, 최소 30분 체류, 15분 계획 회전과 회전당 최대 4개 교체는 유지한다.
6. 한 연결에서 첫 URL만 사용하는 현재 Binance router 경계를 숨기지 않도록 wide 100 초과 설정은 생성 단계에서 거부한다.
7. UI의 전체 카탈로그 행은 `PAPER 가능` 대신 `전략 후보`로 표시하고, 실제 wide/deep 수를 시장 요약에서 분리해 보여준다.
8. 진입 임계값, 비용, bid·ask 체결, TP1·TP2·SL, 위험예산, Governor, 실제주문·인증 0 계약은 바꾸지 않는다.

## 배포 게이트와 롤백

자동검사 뒤 평평한 PAPER 상태에서 불변 릴리스를 설치하고 같은 Run으로 5분을 실제 관찰한다.

- wide 80, deep 16과 event·전략평가가 계속 전진한다.
- 실행호가 p95와 trade p95는 각각 500ms 이하다.
- process CPU는 80% 미만이고 메모리 증가는 256MB 이하다.
- 전략평가 backpressure skip 비율은 1% 미만이다.
- queue overflow, event drop, gap, resync, 비계획 reconnect, persistence fault, buffer drop, critical-lag fail-open은 0이다.
- 실제 주문과 인증은 false다.

하나라도 로컬 처리용량 기준을 실패하면 실패 증거를 삭제하지 않고 같은 Wave에서 50/12로 되돌린다. 거래소 자체 지연이 1,500ms를 넘으면 기존 fail-closed 진입잠금을 유지하고 로컬 용량 문제와 분리한다. 5분 통과는 6시간·24시간 안정성이나 거래 증가·수익성을 증명하지 않는다.
