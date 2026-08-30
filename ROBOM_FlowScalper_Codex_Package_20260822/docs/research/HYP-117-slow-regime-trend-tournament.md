# HYP-117. 느린 추세·시장 레짐·상대강도 24후보 사전등록

- 상태. `PREREGISTERED_BEFORE_EXECUTION`.
- 등록일. 2026-08-30.
- 연구범위. Binance USDⓈ-M 공개 완성 5분봉을 1시간·4시간봉으로 집계한 PAPER 연구다.
- 성과 상태. `NOT_PROVEN`.
- 실자금 준비. `NOT_READY`.

## 연구 질문

기존 5~30분 후보보다 느린 1시간·4시간 구조에서 상승·하락 추세의 초입, 돌파 후 재확인,
첫 눌림 회복과 종목 상대강도를 시장 전체 레짐과 함께 고정하면, 충분한 시간순 표본과
BASE·STRESS 비용 뒤에도 재현 가능한 양의 순기대값 후보가 남는가?

승률 70%는 장기 탐색 목표를 표시하는 진단값일 뿐 합격 기준이 아니다. 승률이 높아도 비용 후
기대값, Profit Factor, payoff, drawdown, 종목 집중, bootstrap, DSR, PBO 중 하나라도 실패하면
승격하지 않는다.

## 고정 후보 24개

네 계열마다 `LONG`, `SHORT`, `BOTH`를 분리하고, 서로 다른 수치계약을 가진 `BALANCED`와
`SELECTIVE`를 적용한다. 따라서 4계열 × 3방향 × 2강도 = 24개다.

| 계열 | 주기 | 핵심 진입 | BALANCED | SELECTIVE |
|---|---:|---|---|---|
| 4시간 채널 돌파 | 4h | 시장 레짐·상대강도·ADX가 정렬된 완성봉 채널 돌파 | 20봉·TP 1.5R/4R | 40봉·TP 2R/5R |
| 1시간 돌파 재확인 | 1h | 돌파 다음 눌림이 채널을 지키고 종가 재회복 | 20봉·0.45ATR 허용 | 36봉·0.30ATR 허용 |
| 1시간 첫 눌림 회복 | 1h | 새 EMA 추세의 첫 눌림 뒤 직전 고가·저가 재돌파 | 20봉·TP 1.5R/4R | 32봉·TP 2R/5R |
| 4시간 상대모멘텀 지속 | 4h | 동일시점 종목 순위와 BTC·시장 breadth가 같은 방향 | 18봉·상하위 33% | 30봉·상하위 17% |

정확한 candidate ID와 모든 임계값은
`scripts/research_slow_regime_trend_tournament.py`의 `PREREGISTERED_CANDIDATES`가 유일한
실행계약이다. 문서와 코드 fingerprint가 다르면 실행을 유효한 사전등록으로 취급하지 않는다.

## 진입과 청산 계약

- 완성된 봉과 그 시점까지 존재한 시장·종목 정보만 쓴다.
- 진입은 신호봉 다음 봉 시가다. 신호봉 종가 체결로 소급하지 않는다.
- 손절은 돌파선, 눌림 저점·고점과 ATR buffer로 정하고 진입 뒤 넓히지 않는다.
- 최초 위험거리는 0.65~4.0 ATR만 허용한다.
- TP1은 40%를 청산하고 TP2는 나머지 60%를 청산한다.
- TP1 뒤 손절은 STRESS 왕복비용을 덮는 가격으로만 줄인다.
- 한 봉에서 손절과 목표가가 모두 닿으면 손절을 먼저 적용한다.
- 일반적인 고정 최대보유시간이나 900초 강제청산은 쓰지 않는다.
- 데이터 끝까지 TP·SL이 닿지 않은 포지션은 `CENSORED_OPEN`으로 보존하고 승패에서 제외한다.
- 데이터·원장·시스템 안전종료는 전략 청산과 별도이며 제거하지 않는다.

## 포트폴리오와 비용

- 각 후보는 다른 후보와 계좌를 공유하지 않는다.
- 후보별 최대 동시 포지션은 2개, 하루 신규진입은 2개다.
- 역사 kline은 당시 실행가능 호가 깊이를 담지 않으므로 BASE 13bp와 STRESS 25bp를 차감한다.
- 같은 봉에서 목표와 손절의 순서를 알 수 없으면 보수적으로 손절을 먼저 적용한다.
- 통과 후보도 실제 공개 bid·ask 깊이를 쓰는 독립 LIVE_PUBLIC BASE·STRESS SHADOW 미래표본이
  최소 30개 쌓이기 전에는 `NOT_PROVEN`이다.
- 실제 주문, private API, API Key, 인증, secret, wallet과 입출금 경로는 0이다.

## 시간순 판정 계약

- 입력은 최소 180일이며 50% Train, 20% Validation, 30% 진단 OOS로 나눈다.
- 경계마다 7일 embargo를 둔다.
- development 닫힌 거래 60건, validation 20건 미만은 순위를 매기지 않는다.
- development와 validation STRESS가 모두 양의 기대값·PF를 보이는 후보만 서로 다른 계열에서
  최대 4개를 진단 OOS로 보낸다.
- OOS는 닫힌 거래 30건, BASE·STRESS 양의 기대값, BASE PF 1.15 이상, STRESS PF 1.05 이상을
  요구한다.
- bootstrap 95% 기대값 하한은 0 초과, DSR은 0.95 이상, PBO는 0.20 이하를 요구한다.
- 양의 성과가 한 종목에 50% 넘게 집중되면 실패한다.
- 이 24개를 모두 다중시험 trial 수로 기록한다.
- 현재 구간은 기존 결과를 본 뒤 설계한 적응 진단이다. 역사 gate를 모두 통과해도 독립 미래
  OOS 전에는 승격하지 않는다.

## 연구 출처와 경계

- [Crypto time-series momentum](https://www.nber.org/papers/w24877)은 암호화폐 모멘텀을 검토할
  가설 근거다.
- [Time Series Momentum](https://www.sciencedirect.com/science/article/pii/S0304405X11002613)은
  느린 추세 지속을 검토할 방법론 근거다.
- [Time series momentum and volatility scaling](https://www.sciencedirect.com/science/article/pii/S1386418116301379)은
  변동성에 따라 위험을 제한할 근거다.
- [Momentum replication critique](https://www.sciencedirect.com/science/article/pii/S0304405X19301953)는
  표본·방법 선택에 따라 성과가 약해질 수 있다는 반증 근거다.
- [Donchian Breakout Strategy](https://www.tradingview.com/script/laT8fTXp-Donchian-Breakout-Strategy/),
  [RVOL breakout confirmation](https://www.tradingview.com/script/gz5FtyXZ-RVOL-Relative-Volume-Breakout-Confirmation/),
  [VWAP pullback/reclaim](https://www.tradingview.com/script/huPnA8Rc-VWAP-Pullback-Reclaim-Planner-AGPro-Series/)은
  공개 아이디어의 구조를 대조하는 자료다.

외부 논문, TradingView 설명과 게시자의 과거 결과는 ROBOM의 수익성 증거가 아니다. Pine 코드를
복사하지 않고 공개된 개념을 미래정보 없는 결정적 PAPER 규칙으로 다시 정의했다.

## 실행 전 변경 금지

후보, 비용, split, 손절, 목표, 표본 gate와 fingerprint를 고정한 뒤 실행한다. 결과를 보고 같은
candidate ID의 수치를 바꾸지 않는다. 결함 수정이나 새 수치 가설은 이전 결과를 삭제하지 않고 새
버전과 새 가설로 분리한다.
