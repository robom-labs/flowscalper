# HYP-127. 다년 저회전 4시간 추세 30후보 사전등록

- 상태. `PREREGISTERED_BEFORE_EXECUTION`.
- 등록일. 2026-08-30.
- 가설 ID. `HYP-127-MULTIYEAR-LOW-TURNOVER-TREND-TOURNAMENT`.
- 후보 지문. `2bd1ed549ec51800970469c34b8c548e6451516c4752d6a56485f064b27cfbe1`.
- 연구범위. Binance USDⓈ-M 공개 완성 4시간봉과 공개 펀딩 이력만 사용하는 PAPER 연구다.
- 성과 상태. `NOT_PROVEN`.
- 실자금 준비. `NOT_READY`.

## 연구 질문

장중 단타에서 반복된 비용 손실과 부족한 표본을 피하기 위해 보유시간을 억지로 늘리는 대신,
다년 4시간봉에서 저회전 추세 진입을 사용하면 실제 펀딩과 보수적 왕복비용을 차감한 뒤에도
서로 다른 시장 구간에서 양의 순기대값이 남는 후보가 있는가?

인터넷·논문·TradingView 지표 설명은 가설의 출처일 뿐 성과의 증거가 아니다. 유명 전략 이름,
영상 조회 수, 게시자가 주장한 승률과 수익은 입력값으로 사용하지 않는다. 결과는 이 문서에 고정한
후보와 ROBOM 자체 PAPER 평가 계약으로만 판정한다.

## 고정 데이터 범위

- 기간은 UTC `2021-01-01` 이상 `2026-08-30` 미만이다.
- 종목은 `BTCUSDT`, `ETHUSDT`, `BNBUSDT`, `SOLUSDT`, `XRPUSDT`, `DOGEUSDT`,
  `ADAUSDT`, `AVAXUSDT`, `LINKUSDT`, `DOTUSDT`, `LTCUSDT`, `BCHUSDT`다.
- 봉은 Binance USD-M 공개 `/fapi/v1/klines`의 완성 4시간봉만 사용한다.
- 펀딩은 Binance USD-M 공개 `/fapi/v1/fundingRate`의 실제 시각·비율을 방향별로 적용한다.
- 다운로드 원본은 날짜·종목별 캐시와 SHA-256 manifest로 고정한다.
- 현재 생존한 대형 종목을 고정했으므로 survivorship bias가 있으며 결과에 한계로 남긴다.
- 과거 호가 깊이는 없으므로 실제 bid·ask 체결 가능성은 향후 LIVE_PUBLIC SHADOW에서 따로
  검증한다.

## 고정 후보 30개

아래 5계열마다 `LONG`, `SHORT`, `BOTH`를 분리하고 `BALANCED`, `SELECTIVE`를 적용한다.
따라서 5계열 × 3방향 × 2강도 = 30개다.

| 계열 | 핵심 진입 | BALANCED | SELECTIVE |
|---|---|---|---|
| 4시간 채널 돌파 | 과거 채널을 종가로 돌파한 추세 확장 | 30봉·TP 1.5R/4R | 60봉·강한 레짐·TP 2R/5.5R |
| 돌파 후 첫 재시험 | 직전 돌파 뒤 채널 경계를 처음 재시험하고 복귀 | 30봉·0.40ATR | 60봉·0.22ATR·강한 레짐 |
| 상승·하락 초입 첫 눌림 | 새 EMA20/50 추세 초기에 EMA20 눌림 뒤 직전 극값 재돌파 | 18봉 이내 | 12봉 이내·강한 레짐 |
| 일목 추세 눌림 재개 | 9·26·52 일목선과 구름 방향이 정렬된 눌림 재개 | 완화 레짐 | 구름·기준선 완전 정렬 |
| 추세 중 유동성 훑기 후 복귀 | 추세 방향 반대쪽 고가·저가를 wick으로 훑고 범위 안 복귀 | 18봉·0.65ATR | 36봉·0.38ATR·직전 극값 재돌파 |

정확한 candidate ID와 모든 수치는
`scripts/research_multiyear_trend_tournament.py`의 `PREREGISTERED_CANDIDATES`가 유일한
실행계약이다. 동일 ID의 수치는 결과를 본 뒤 바꾸지 않는다.

## 미래정보·체결·청산 계약

- 완성된 현재 봉과 그 시점까지 존재한 데이터만 신호에 사용한다.
- 신호 다음 4시간봉 시가에 진입하고 신호봉 종가로 소급 체결하지 않는다.
- 구조적 손절과 ATR buffer를 진입 전에 고정하며 진입 뒤 불리한 방향으로 넓히지 않는다.
- 최초 위험거리는 0.65~4.0ATR만 허용한다.
- TP1 40%, TP2 60%다. `BALANCED`는 1.5R/4R, `SELECTIVE`는 2R/5R 이상이다.
- 같은 봉에서 손절과 목표가가 모두 닿으면 손절을 먼저 적용한다.
- TP1 뒤 잔여 수량 손절은 STRESS 왕복비용을 확보하는 유리한 방향으로만 이동한다.
- 고정 최대보유시간과 일반 근거약화 청산은 사용하지 않는다.
- 데이터 끝까지 TP·SL이 닿지 않은 포지션은 `CENSORED_OPEN`으로 보존하고 승패에서 제외한다.
- 후보마다 최대 동시 2포지션과 UTC 하루 최대 2진입을 적용한다.
- 실제 주문, private API, API Key, 인증, secret, wallet과 입출금 경로는 0이다.

## 비용·펀딩 계약

- BASE 왕복 실행비용은 13bp다.
- STRESS 왕복 실행비용은 25bp다.
- 포지션 방향과 보유구간에 맞는 실제 공개 펀딩 cashflow를 gross 성과에 더한다.
- 진입 시각 또는 종료 봉에 정확히 걸려 보유 여부가 모호한 유리한 펀딩 credit은 제외한다.
- 같은 모호한 경계의 불리한 펀딩 cost는 포함한다.
- 과거 bid·ask depth가 없으므로 이 결과만으로 실체결 가능성을 주장하지 않는다.

## 시간순 분할과 선택 계약

- 전체 기간을 50% Train, 20% Validation, 30% 진단 OOS로 시간순 분할한다.
- Train·Validation 및 Validation·OOS 경계에 각각 7일 embargo를 둔다.
- development 60건과 validation 20건 미만은 순위를 매기지 않는다.
- Train·Validation에서 STRESS 기대값이 양수이고 PF gate를 통과한 후보만 선발 대상이다.
- 한 계열에서 최대 1개만 뽑고 최대 5개 계열을 진단 OOS로 보낸다.
- 진단 OOS 30건, BASE·STRESS 양의 기대값, BASE PF 1.15, STRESS PF 1.05,
  bootstrap 95% 하한 양수, DSR 0.95, PBO 0.20 이하, 최대 종목 기여 50% 이하를 모두
  통과해야 `ADAPTIVE_HISTORICAL_PASS_FORWARD_REQUIRED`로 표시한다.
- 승률 70%는 사용자 목표를 보여주는 진단값일 뿐 기대값·PF·drawdown·다중검정 gate를
  대신하지 않는다.

## 적응 경계와 승격 금지

앞선 HYP-116L·HYP-117·HYP-118 결과를 본 뒤 시간축과 비용모형을 선택했으므로 마지막 30%도
독립 미래표본이 아니다. 따라서 역사 gate를 모두 통과하더라도 성과 상태는 `NOT_PROVEN`, 실자금
준비는 `NOT_READY`다.

역사 통과 후보만 향후 실제 공개 bid·ask 깊이의 BASE·STRESS 독립 SHADOW 계좌에 새 버전으로
등록할 수 있다. 자연기회를 최소 30개 쌓고 사전등록한 forward gate를 통과하기 전에는 ACTIVE,
실전 주문 또는 실자금 후보로 승격하지 않는다. 실패·희소·탈락 후보의 결과도 삭제하지 않는다.

## 가설 근거

- NBER `Risks and Returns of Cryptocurrency`는 암호화폐 time-series momentum을 가설로
  다루지만 이 구현의 수익을 입증하지 않는다.
  <https://www.nber.org/papers/w24877>
- `Technical analysis in cryptocurrency markets: Do transaction costs and bubbles matter?`는
  이동평균·돌파 규칙과 거래비용 민감도를 연구한다.
  <https://doi.org/10.1016/j.intfin.2022.101601>
- `Momentum Crashes`는 추세 전략이 반전 국면에서 크게 손실날 수 있는 위험 근거다.
  <https://www.nber.org/papers/w20439>
- `The Deflated Sharpe Ratio`는 반복 후보시험의 선택편향 보정 근거다.
  <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551>
- Binance 공식 공개시장 문서는 봉과 펀딩 endpoint의 데이터 계약 근거다.
  <https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data>

결함 수정 또는 새 수치 가설은 새 가설 번호와 새 결과 파일로 분리한다. 이전 원장과 실패 결과를
보존하며, 같은 데이터의 반복 조회를 독립 증거처럼 세지 않는다.
