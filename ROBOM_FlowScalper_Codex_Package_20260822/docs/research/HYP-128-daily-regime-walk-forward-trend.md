# HYP-128. 일봉 느린 레짐·시간순 안정성 추세 30후보 사전등록

- 사전등록 상태. `LOCKED_BEFORE_EXECUTION`.
- 실행 상태. `EXECUTED_NO_SELECTION`.
- 등록일. 2026-08-30.
- 가설 ID. `HYP-128-DAILY-REGIME-WALK-FORWARD-TREND-TOURNAMENT`.
- 후보 지문. `f5b58ee6b16a3fb651d69d86a6fefa2dcb6d8ebac97a6ea54a05a618bca8ee21`.
- 성과 상태. `NOT_PROVEN`.
- 실자금 준비. `NOT_READY`.

## 실패에서 바꾼 한 가지 구조

HYP-127의 4시간 후보는 Train·Validation에서 강했던 롱 3계열이 2024-12 이후 진단 OOS에서
모두 음수로 바뀌었고 PBO는 0.60이었다. 이번 가설은 그 결과를 숨기거나 같은 수치를 다시
조정하지 않는다.

신호시간을 완성 UTC 일봉으로 낮춰 장중 잡음과 회전을 줄이고, SELECTIVE 후보는 일봉
EMA50·EMA200 정렬을 요구한다. 한 번의 Train·Validation 합계가 좋은 후보를 바로 고르지 않고,
development를 6개 시간순 구간으로 다시 나눠 최근 두 구간을 포함한 반복 안정성을 선발 전에
요구한다.

이 결정 자체가 HYP-127 결과를 보고 내린 적응 결정이므로 마지막 30%도 독립 미래표본이라고
주장하지 않는다.

## 고정 데이터

- UTC `2021-01-01` 이상 `2026-08-30` 미만 Binance USDⓈ-M 공개시장 데이터다.
- HYP-127과 같은 12종목의 완성 4시간봉 6개가 시간순으로 모두 있는 UTC 날짜만 일봉으로
  집계한다. 빠진 봉이 하나라도 있는 날짜는 사용하지 않는다.
- 종목은 `BTCUSDT`, `ETHUSDT`, `BNBUSDT`, `SOLUSDT`, `XRPUSDT`, `DOGEUSDT`,
  `ADAUSDT`, `AVAXUSDT`, `LINKUSDT`, `DOTUSDT`, `LTCUSDT`, `BCHUSDT`다.
- 실제 공개 펀딩 이력은 방향별 cashflow로 적용한다.
- 원본 bar·funding SHA-256과 파생 일봉 SHA-256을 종목별 manifest에 기록한다.

## 고정 후보 30개

아래 5계열마다 `LONG`, `SHORT`, `BOTH`와 `BALANCED`, `SELECTIVE`를 조합한다.
따라서 5계열 × 3방향 × 2강도 = 30개다.

| 계열 | 핵심 진입 | BALANCED | SELECTIVE |
|---|---|---|---|
| 일봉 채널 돌파 | 20일 또는 55일 채널 종가 돌파 | 완화 레짐·TP 1.5R/4R | EMA50·200 정렬·TP 2R/5.5R |
| 돌파 뒤 첫 재시험 | 이전 일봉 돌파 후 경계 재시험·복귀 | 20일·0.40ATR | 55일·0.22ATR·느린 정렬 |
| 추세 초입 첫 눌림 | EMA20·50 새 추세의 첫 눌림 뒤 직전 극값 재돌파 | 10일 이내 | 6일 이내·느린 정렬 |
| 일목 눌림 재개 | 일봉 9·26·52선과 26일 선행구름 추세 재개 | 완화 정렬 | 구름·기준선 완전 정렬 |
| EMA 눌림 지속 | EMA20 눌림 뒤 방향 재돌파 | 완화 레짐 | EMA50·200 정렬·좁은 재시험 |

정확한 candidate ID와 임계값은
`scripts/research_daily_regime_trend_tournament.py`의
`PREREGISTERED_DAILY_CANDIDATES`가 유일한 실행계약이다. 결과 뒤 같은 ID의 값을 바꾸지 않는다.

## 체결·보호 계약

- 완성 일봉과 그 시점까지 존재한 데이터만 신호에 사용한다.
- 신호 다음 UTC 일봉 시가에 진입한다.
- entry·구조적 SL·TP1·TP2를 진입 전에 확정하고 손절을 불리한 방향으로 넓히지 않는다.
- 최초 위험거리는 0.65~4.0ATR만 허용한다.
- TP1 40%, TP2 60%다.
- 같은 일봉에서 SL과 TP가 모두 닿으면 SL을 먼저 적용한다.
- TP1 뒤 잔여 손절은 STRESS 왕복비용을 확보하는 방향으로만 이동한다.
- 고정 최대보유와 일반 근거약화 청산은 없다.
- 데이터 끝까지 TP·SL이 닿지 않은 포지션은 `CENSORED_OPEN`으로 보존하고 채점하지 않는다.
- 후보별 최대 동시 2포지션·UTC 하루 최대 2진입을 적용한다.

## 비용·펀딩 계약

- BASE 왕복 실행비용 13bp와 STRESS 25bp를 각각 차감한다.
- 포지션 방향과 보유구간에 맞는 실제 공개 펀딩을 더한다.
- 진입 시각 또는 종료 일봉에 걸려 보유 여부가 모호한 유리한 펀딩 credit은 제외한다.
- 같은 경계의 불리한 펀딩 cost는 포함한다.
- 과거 호가 깊이가 없으므로 역사 통과도 실제 bid·ask 체결 가능성을 증명하지 않는다.

## 선발 전 walk-forward 안정성 계약

- 전체는 50% Train·20% Validation·30% 진단 OOS이며 각 큰 경계에 7일 embargo를 둔다.
- development 70%를 별도로 6개 연속 fold로 나눈다.
- 각 fold의 양 끝 7일을 제외하고 STRESS 완료거래 8건 이상이어야 평가 가능한 fold다.
- 평가 가능한 fold가 최소 5개여야 한다.
- 최소 4개 fold에서 STRESS 기대값 양수·PF 1 초과여야 한다.
- 가장 최근 2개 development fold가 모두 양수여야 한다.
- 그 뒤에도 development 완료거래 60건·Validation 20건과 기존 STRESS gate를 통과해야 한다.
- 같은 계열은 최대 1개, 전체 최대 5개만 진단 OOS로 보낸다.

## 최종 역사 진단 gate

- OOS 완료거래 30건 이상이다.
- BASE·STRESS 기대값이 모두 양수다.
- BASE PF 1.15, STRESS PF 1.05 이상이다.
- bootstrap 95% 기대값 하한이 양수다.
- DSR 0.95 이상, PBO 0.20 이하, 최대 한 종목 양의 기여 50% 이하다.
- 승률 70%는 참고 진단이며 다른 gate를 대신하지 않는다.

모든 gate를 통과해도 결과는 `ADAPTIVE_HISTORICAL_PASS_FORWARD_REQUIRED`일 뿐이다. 실제 공개
bid·ask BASE·STRESS SHADOW에서 새 버전 자연표본 30개와 독립 미래구간을 통과하기 전에는
수익성 `NOT_PROVEN`, 실자금 `NOT_READY`, Registry 변경 0을 유지한다.

## 근거와 반대 근거

- NBER `Risks and Returns of Cryptocurrency`는 일봉·주봉 time-series momentum을 보고했다.
  <https://www.nber.org/papers/w24877>
- `Technical analysis in cryptocurrency markets`는 일봉·1분봉 이동평균과 돌파, 거래비용
  민감도를 연구했다.
  <https://doi.org/10.1016/j.intfin.2022.101601>
- `Dynamic time series momentum of cryptocurrencies`는 동적 추세와 변동성 확장 가설을
  제공한다.
  <https://doi.org/10.1016/j.najef.2021.101428>
- 반대로 `Cryptocurrencies and momentum`은 143종목에서 유의한 momentum payoff를 찾지
  못했다. 이 반대 결과를 제외하지 않는다.
  <https://doi.org/10.1016/j.econlet.2019.03.028>
- `The Deflated Sharpe Ratio`와 PBO는 반복 후보시험의 선택편향을 통제하는 근거다.
  <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551>

외부 논문과 지표 이름은 성과 보증이 아니다. 실패·희소·탈락 결과를 삭제하지 않으며, 결함 수정
또는 새 임계값은 새 가설 번호와 새 지문으로만 실행한다. 실제 주문, private API, API Key,
secret, 인증, wallet과 입출금 경로는 계속 0이다.

## 실행 뒤 고정 결과

- 실행 commit은 `7f233956ef84913157c2b9efc879ecf126e50aee`다.
- 파생 일봉 dataset 지문은
  `7127b105c60190cd205601d85f4b42105e85f724178dbe619ba54fa99031d989`다.
- 12종목에서 종목당 2,067개, 합계 24,804개 완성 일봉을 사용했다.
- 30개 모두 최소 표본과 6개 fold 안정성 선발을 함께 통과하지 못했다.
- 평가 가능한 fold의 후보별 최댓값은 4개, 양수 fold 최댓값은 3개였다.
- Train·Validation·walk-forward 선발 후보와 진단 OOS 진입 후보는 모두 0개다.
- PBO는 0.2571428571로 고정 상한 0.20을 넘었다.
- Registry·PAPER SHADOW 승격은 0개다.
- 결과는 `NOT_PROVEN`, 실자금 준비는 `NOT_READY`다.

채널 돌파 롱 BALANCED는 development 44건·Validation 19건, EMA 눌림 양방향 BALANCED는
development 51건·Validation 19건이었다. 일부 합계와 최근 fold가 양수여도 사전등록 최소
60건·20건과 최소 5개 평가 가능 fold를 충족하지 못했으므로 순위를 매기지 않았다. 기준을
19건이나 4개 fold로 낮추지 않는다.

전체 결과는 `evidence/WAVE128_DAILY_REGIME_TREND_TOURNAMENT.json`, 요약 검증은
`evidence/WAVE128_DAILY_REGIME_TREND_TOURNAMENT_QA.json`, append-only 시험 기록은
`RESEARCH-HYP128-7127b105c601-8379fdb564d0`에 보존한다.
