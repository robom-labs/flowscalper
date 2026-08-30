# HYP-130. 거래량 확인 4시간 추세 초입·첫 눌림 30후보 사전등록

- 사전등록 상태. `LOCKED_BEFORE_EXECUTION`.
- 실행 상태. `EXECUTED_NO_PROMOTION`.
- 등록일. 2026-08-30.
- 가설 ID. `HYP-130-VOLUME-CONFIRMED-EARLY-TREND-TOURNAMENT`.
- 후보 지문. `dd89c061086900c23ec17c1aef124372e6856fc06d0646b7b72ca6ec7399f58b`.
- 성과 상태. `NOT_PROVEN`.
- 실자금 준비. `NOT_READY`.

## 이번에 고정한 질문

HYP-127의 가격·레짐 중심 4시간 후보와 HYP-128·129의 느린 추세·주별 모멘텀은 비용·표본·
시간순 안정성 gate를 모두 통과하지 못했다. 이번 연구는 그 실패를 지우거나 같은 후보의 수치를
사후 조정하지 않는다.

이번 한 가지 질문은 다음과 같다.

> 완성 4시간봉의 가격 추세에 OBV 방향 전환 또는 거래량 확인을 추가하고, 돌파 추격뿐 아니라
> 변동성 수축 뒤 돌파와 첫 눌림 재개를 분리하면, 실제 펀딩과 계좌 위험 40bp, BASE 13bp·
> STRESS 25bp 뒤에도 여러 시간순 구간에서 반복되는 양의 순기대값 후보가 남는가?

앞선 결과와 외부 자료를 본 뒤 만든 적응 역사 연구다. 마지막 30%도 독립 미래표본이라고
주장하지 않는다.

## 연구 출처와 영상 사용 경계

- `Are simple technical trading rules profitable in bitcoin markets?`는 75,360개 규칙을 비용,
  OOS와 다중검정으로 비교하며 OBV·이동평균·filter·channel 계열을 정의한다.
  <https://doi.org/10.1016/j.iref.2024.05.003>
- `High frequency momentum trading with cryptocurrencies`는 여러 대형 코인의 시간·횡단면
  momentum을 비교하며 한 파라미터가 모든 하위기간에 우월하지 않음을 보존하게 한다.
  <https://doi.org/10.1016/j.ribaf.2019.101176>
- `Technical trading and cryptocurrencies`는 이동평균·channel 규칙을 OOS와 다중시험 경계에서
  검증한다.
  <https://doi.org/10.1007/s10479-019-03357-1>
- `Technical analysis in cryptocurrency markets`는 작은 거래비용 변화와 시장상태가 결과를
  바꿀 수 있음을 제한사항으로 사용하게 한다.
  <https://doi.org/10.1016/j.intfin.2022.101601>

사용자가 요청한 공개 영상도 후보 발굴 자료로 별도 대조했다. 검색결과의 공개 설명에서
`Bollinger Band Width + OBV` 수축 돌파 조합과 다중시간 추세 확인형 Supertrend가 확인됐다.
영상 전체의 모든 장면을 검토했다는 뜻은 아니며, 게시자의 승률·수익률·조회 수는 가져오지
않는다.

| 공개 영상 설명 | 추출 가능한 개념 | 이번 결정 |
|---|---|---|
| [Bollinger Band Squeeze Trading Strategy](https://www.youtube.com/watch?v=O7UAwPSQ7Kw) | Band Width 수축과 OBV 돌파 확인 | `SQUEEZE_BREAKOUT`의 2차 가설 근거. 공식 계산과 완성봉으로 재정의 |
| [I Coded a Supertrend Strategy Backtest](https://www.youtube.com/watch?v=Yl5WCVMllC4) | 상위시간 추세와 하위시간 진입 분리 | 느린 EMA 정렬 대조에만 반영. Supertrend 성과는 가져오지 않음 |
| [Donchian Channel Strategy for Crypto](https://www.youtube.com/watch?v=DX03SapMezE) | 채널 돌파와 위험관리 | 구체 규칙이 공개 설명만으로 불충분하고 기존 HYP-127·F04/F05와 중복되어 새 후보로 복제하지 않음 |

유튜브·TradingView의 `best`, `secret`, `profitable`, 승률 또는 수익 캡처는 검증값이 아니다.
외부 Pine·비공개 지표·유료방 규칙을 복사하지 않는다. 동일 개념을 이름만 바꿔 독립 표본처럼
세지 않는다.

## 고정 데이터와 미래정보 경계

- UTC `2021-01-01` 이상 `2026-08-30` 미만 Binance USDⓈ-M 공개시장 자료다.
- 종목은 `BTCUSDT`, `ETHUSDT`, `BNBUSDT`, `SOLUSDT`, `XRPUSDT`, `DOGEUSDT`,
  `ADAUSDT`, `AVAXUSDT`, `LINKUSDT`, `DOTUSDT`, `LTCUSDT`, `BCHUSDT`다.
- 완성 4시간 OHLCV만 신호와 지표에 사용한다.
- 같은 시각 12종목이 모두 준비된 snapshot에서 BTC 느린 방향, 시장 breadth와 72시간 상대순위를
  계산한다.
- 현재 완성봉 종가에서 신호를 확정하고 다음 4시간봉 시가에만 진입한다.
- 이후 봉을 바꿔도 이전 OBV, 평균, 신호와 진입계획이 바뀌지 않아야 한다.
- 실제 공개 펀딩 이력을 포지션 방향별 cashflow로 적용한다.
- 종목별 bar·funding SHA-256과 전체 dataset 지문을 결과에 보존한다.

## 고정 후보 30개

아래 5계열마다 `LONG`, `SHORT`, `BOTH`와 `BALANCED`, `SELECTIVE`를 조합한다.
따라서 5계열 × 3방향 × 2강도 = 30개다.

| 계열 | 완성봉 진입 신호 | BALANCED | SELECTIVE |
|---|---|---|---|
| OBV 이동평균 교차 | 정규화 OBV fast가 slow를 방향별 band 밖으로 완성봉 확인 | 6/24, 1봉 확인 | 12/48, 2봉 확인·느린 정렬 |
| OBV·가격 동시 돌파 | 가격과 OBV가 같은 방향의 과거 극값을 함께 종가 돌파 | 24봉 | 48봉·느린 정렬 |
| 거래량 확인 수축 돌파 | 최근 채널폭이 고정 한도 이내인 구간을 OBV 방향과 함께 돌파 | 6%·18봉 | 4%·36봉·느린 정렬 |
| 추세 극값 filter turn | 최근 극값에서 고정 1% 또는 2% 반등·반락하고 직전 극값 돌파 | 1%·12봉 | 2%·24봉·느린 정렬 |
| OBV 뒤 첫 눌림 | 최근 OBV 교차 뒤 EMA20 첫 재시험과 직전 봉 방향 재돌파 | 최대 12봉·0.45ATR | 최대 18봉·0.25ATR·느린 정렬 |

`BALANCED`는 72시간 momentum 1%, 상대순위 55%, breadth 53%, ADX 14,
상대거래량 0.55 이상이다. `SELECTIVE`는 각각 2.5%, 70%, 58%, ADX 18,
상대거래량 0.80과 EMA50·EMA200 느린 정렬을 요구한다. SHORT는 같은 기준을 대칭 적용한다.

정확한 ID와 모든 수치는
`scripts/research_volume_confirmed_early_trend_tournament.py`의
`PREREGISTERED_VOLUME_TREND_CANDIDATES`가 유일한 실행계약이다. 결과 뒤 같은 ID의 값을
바꾸지 않는다.

## 진입·보호·계좌 위험 계약

- entry, 구조적 SL, TP1, TP2와 수량에 대응하는 계좌 위험을 진입 전에 확정한다.
- 구조적 SL은 신호봉과 앞의 두 봉 극값에 BALANCED 0.25ATR, SELECTIVE 0.35ATR 여유를 둔다.
- 최초 위험거리는 0.65~4.0ATR만 허용한다.
- BALANCED는 TP1 1.5R, TP2 3.5R이고 SELECTIVE는 TP1 2.0R, TP2 4.5R이다.
- TP1에서 40%, TP2에서 60%를 처리한다.
- 같은 4시간봉에서 SL과 TP가 모두 닿으면 SL을 먼저 적용한다.
- TP1 뒤 잔여 손절은 STRESS 왕복비용을 확보하는 방향으로만 이동한다.
- 고정 최대보유시간과 일반 근거약화 청산은 없다.
- 데이터 끝까지 결판나지 않은 포지션은 `CENSORED_OPEN`으로 보존하고 채점하지 않는다.
- 후보별 최대 동시 2포지션, UTC 하루 최대 2진입이다.
- 거래별 계좌 위험예산은 40bp다. 구조적 손절거리로 notional fraction을 계산하며 1.0을 넘지
  않는다. 최대 두 포지션의 총 초기 위험은 80bp를 넘지 않는다.
- 손절 확대, 물타기, 마틴게일, 피라미딩과 자동 위험증가는 없다.

## 비용·펀딩 계약

- BASE 왕복 실행비용 13bp와 STRESS 25bp를 notional fraction만큼 차감한다.
- 실제 공개 펀딩을 방향과 보유구간에 맞게 계좌 손익에 반영한다.
- 경계가 모호한 유리한 펀딩 credit은 제외하고 불리한 cost는 포함한다.
- 역사 4시간봉에는 당시 실행가능 bid·ask 깊이와 정확한 봉 내부 순서가 없다. 역사 통과도
  실제 체결 가능성 또는 미래 수익을 증명하지 않는다.

## 시간순 선발·과최적화 gate

- 50% Train·20% Validation·30% 진단 OOS이며 큰 경계마다 7일 embargo를 둔다.
- development 70%를 6개 연속 fold로 나눈다.
- fold별 양 끝 7일을 제외하고 STRESS 완료거래 8건 이상인 fold를 최소 5개 요구한다.
- 최소 4개 fold와 가장 최근 2개 fold에서 STRESS 기대값 양수·PF 1 초과를 요구한다.
- development 완료거래 60건, Validation 20건, development STRESS 기대값 양수·PF 1.05,
  Validation STRESS 기대값 양수·PF 1 초과를 모두 요구한다.
- 같은 계열은 최대 1개, 전체 최대 5개만 진단 OOS로 보낸다.
- 진단 OOS 완료거래 30건, BASE·STRESS 기대값 양수, BASE PF 1.15, STRESS PF 1.05,
  bootstrap 95% 기대값 하한 양수, DSR 0.95, PBO 0.20 이하와 한 종목 양의 기여 50% 이하를
  모두 요구한다.
- 30개 후보는 모두 PBO·DSR의 다중시험 수에 포함한다.
- 승률 70%는 참고 진단이며 표본·비용·손익비·drawdown과 위 gate를 대신하지 않는다.

역사 gate를 모두 통과해도 `ADAPTIVE_HISTORICAL_PASS_FORWARD_REQUIRED`일 뿐이다. 실제 공개
bid·ask BASE·STRESS SHADOW의 현재 버전 자연표본 30건과 별도 미래기간을 통과하기 전에는
수익성 `NOT_PROVEN`, 실자금 `NOT_READY`다. 통과 후보가 없으면 Registry와 런타임 변경은 0으로
유지한다. 실패·미결·탈락 기록은 삭제하지 않는다.

실제 주문, private API, API Key, secret, 인증, wallet, 입출금과 runtime AI 주문판단은 계속
0이다.

## 실행 결과

- 실행 commit. `673d31e4f29fbe038a9a39cf6f9dfc7849e58b7d`.
- dataset 지문. `89f778387acebe91361449e20c95917ab33281134fa25bf8cb69a3d59314338a`.
- 입력. 12종목 완성 4시간봉 148,824개와 실제 공개 펀딩 이벤트 74,487개.
- 전체 후보 중복 평가. 원신호 8,242개, 포트폴리오 선택 4,124개, 완료거래 4,112개,
  데이터 끝 미결 12개.
- walk-forward 안정성 통과. 4개.
- Train·Validation·walk-forward 동시 선발. 1개.
- 전체 역사 강건성 gate 통과. 0개.
- PBO. `0.8571428571`.

선발된 `T130_OBV_PRICE_BREAKOUT_4H_BOTH_SELECTIVE`는 진단 OOS 68건에서 BASE 기대값
+4.801 계좌 bp·PF 1.208, STRESS 기대값 +3.824 계좌 bp·PF 1.162였다. 그러나 bootstrap
95% 기대값 하한은 -8.951 계좌 bp, DSR 확률은 0, PBO는 0.8571이었다. 따라서 양의 평균이
다중시험에서 우연히 선택됐을 가능성을 배제하지 못했고 최종 gate를 실패했다.

방향별 사후 진단에서는 LONG 34건이 STRESS 기대값 -4.707 계좌 bp·PF 0.830, SHORT
34건이 +12.355 계좌 bp·PF 1.627이었다. 이는 양방향 후보의 결과를 연 뒤 확인한 차이이므로
SHORT만 잘라 승격하는 근거로 사용하지 않는다. 사전등록된 별도 SHORT 후보도 필요한
시간순 안정성 gate를 통과하지 못했다.

전체 tournament를 두 번 실행해 생성시각을 제외한 canonical SHA-256
`f6c277ab71b90336b72ed3acc41dcfbcbcc8f11e68f45e7daf690c17099b4dfa`가 일치했다. 동시
LIVE_PUBLIC 180.027초 관찰은 event +12,597, 전략평가 +79,360, queue 최대 7, 처리·체결
p95 최대 25.060·70.030ms였고 신규 500ms 초과 loop 지연, 비계획 재연결, gap, drop,
저장 fault, 실제 주문과 인증은 0이었다.

Registry와 PAPER SHADOW 승격은 0개다. 결과는
`RESEARCH-HYP130-89f778387ace-b3595bafeea4`로 append-only 시험이력에 보존한다. 다음 연구는
같은 역사에서 SHORT를 사후 조정하지 않고, 파라미터 무변경 외부 venue 또는 이후 시점
검증과 국제 공개영상의 기계적으로 완전한 규칙을 별도 가설 ID로 분리한다.

현 수용상태는
`HYP130_EXECUTED_OOS_NEAR_MISS_REJECTED_NO_PROMOTION_NOT_PROVEN_NOT_READY`다.
