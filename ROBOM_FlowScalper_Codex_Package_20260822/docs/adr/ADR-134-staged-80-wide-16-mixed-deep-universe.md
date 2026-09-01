# ADR-134. 80개 넓은 감시와 16개 혼합 정밀분석의 단계 확장

## 상태

Accepted and measured with preserved failures, 2026-09-01.

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

## 실제 검증 결과

첫 80/16 불변 릴리스의 실제 설치 서비스 300.029초 관찰은 event +33,811, 전략평가
+26,064, queue 최대 60, 처리·체결 P95 최대 109.670/234.343ms, process CPU 최대
58.034%, 메모리 증가 32.391MB였다. backpressure skip 비율은 1% 미만이고 비계획
재연결·gap·resync·drop·저장결함·buffer drop·critical lag·실제 주문·인증은 0이었다.
따라서 wide 80·deep 16은 이 장비의 5분 배포 gate를 통과했다.

다만 상단 이름의 메인 복귀 수정 릴리스 뒤 60.026초 관찰에서 전략평가 1,450.297ms와
신규 500ms 초과 event-loop 지연 1회를 재현했다. 실패는
`evidence/WAVE144_FINAL_RELEASE_RUNNING_SERVICE_60S.json`에 삭제하지 않고 보존했다.
원인은 매 2초 평가마다 실제 계산에 최대 200개만 필요한 15분·30분·1시간 완성봉을 각각
500개씩 프로세스 경계로 반복 전달하던 경로로 좁혔다. 전략·진입 기준은 바꾸지 않고 각
시간구간의 마지막 200개만 전달해 최악 입력을 1,500개에서 600개로 줄였으며, 500개 원본과
시간구간별 200개 입력의 추세·모멘텀 결과가 같음을 회귀 테스트로 고정했다.

수정 불변 릴리스 `84550d32be2178d79d661d3eaec7f54b68a26c10`의 같은 Run 180.060초
재검증은 event +19,974, 전략평가 +16,104, queue 최대 36, 처리·체결 P95 최대
48.896/233.032ms, 전략평가 최대 394.444ms, process CPU 최대 47.685%, 메모리 증가
12.640MB였다. 신규 500ms 초과 event-loop 지연·재연결·gap·resync·drop·저장결함·
buffer drop·critical lag·실제 주문·인증은 모두 0으로 PASS했다. 적격신호와 신규 거래는
0이므로 이번 결과는 처리용량 증거이지 거래 증가나 수익성 증거가 아니다.

별도 `soak_live.py` 180초는 독립 Run을 만들고 15분 계획회전을 3분 안에 보지 못해 FAIL로
끝났다. 설치 서비스 검증에 사용하지 않고
`evidence/WAVE144_ISOLATED_80_16_SOAK_180S.json`에 범위를 분리해 보존한다.
