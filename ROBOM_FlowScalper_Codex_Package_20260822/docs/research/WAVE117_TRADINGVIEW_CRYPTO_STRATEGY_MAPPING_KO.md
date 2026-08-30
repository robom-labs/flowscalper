# Wave 117. TradingView·코인 연구 아이디어와 100후보 중복 대조

## 결론

TradingView에서 흔히 언급되는 단타·추세 전략을 이름만 바꿔 100개 더 만드는 대신, 공개 아이디어의
실제 입력과 행동을 기존 100후보의 20개 alpha 계열에 대조했다. 현재 100후보는 20개 진입계열과
5개 독립 청산계약의 조합이다. 이 중 공개 규칙이 불완전한 F01·F02의 10개는 실행을 차단하고,
F03~F20의 90개를 동일한 동결 시장입력에서 병렬 평가한다.

이 100개는 실거래 계좌 100개가 아니다. 모두 내부 PAPER 연구계좌이며 실제 주문, 거래소 로그인,
API Key, private API, wallet과 입출금은 없다.

## 공개 아이디어 대응표

| 공개 아이디어 | ROBOM 후보 | 처리 |
|---|---|---|
| EMA 다중시간축 눌림 | F03 | 1h·4h 추세와 15m 눌림, 5m 재가속으로 결정화 |
| Donchian·Turtle 돌파 | F04·F05 | 20봉 단타와 55봉 느린 돌파를 분리 |
| 돌파 후 재확인 | F06 | 돌파선 재시험, 구조 회복과 OFI 재가속을 요구 |
| Supertrend·EMA·ADX | F07 | Supertrend 단독이 아니라 ADX·EMA slope·비용 gate 결합 |
| VWAP 눌림·재회복 | F08 | 1h 추세 안에서 session VWAP 회복만 허용 |
| Anchored VWAP | F09 | UTC session 또는 이미 확정된 돌파시점만 anchor로 사용 |
| Bollinger squeeze | F10 | 과거 bandwidth 분위수와 RVOL을 사전 고정 |
| TTM squeeze | F11 | Bollinger-in-Keltner 지속 뒤 완성봉 돌파 |
| ATR 변동성 확장 | F12 | 단기·장기 실현변동성 비율과 추격 제한 |
| RVOL breakout | F13 | 거래량뿐 아니라 거래횟수·taker 방향·spread 확인 |
| Opening range | F14 | 사전 고정 UTC session만 사용 |
| Time-series momentum | F15 | 완성 6h봉의 24h 수익률과 EMA·변동성 정렬 |
| Cross-sectional momentum | F16 | 당시 거래가능 universe의 상·하위 20%만 사용 |
| Queue·microprice·OFI·흡수 | F17~F20 | 실제 공개 호가·체결 event-time 계열로 분리 |

## 직접 확인한 공개 출처

- TradingView의 [전략 개념 문서](https://www.tradingview.com/pine-script-docs/concepts/strategies/)와
  [broker emulator 설명](https://www.tradingview.com/support/solutions/43000786181-broker-emulator/)은
  차트상의 이론 주문과 실제 체결 가정을 구분해야 한다는 근거다.
- TradingView의 [commission 설명](https://www.tradingview.com/support/solutions/43000681703-commission-paid/)은
  수수료를 생략한 성과를 채택하지 않을 근거다.
- TradingView의 [전략 게시 규칙](https://www.tradingview.com/support/solutions/43000764681-strategy-publishing-rules/)은
  과최적화, lookahead와 비현실적 결과를 경계할 근거다.
- [Supertrend·EMA·volume·ATR 예시](https://www.tradingview.com/script/VTDjMpbp-Supertrend-EMA-Vol-Strategy-V5/),
  [Donchian 예시](https://www.tradingview.com/script/laT8fTXp-Donchian-Breakout-Strategy/),
  [RVOL 예시](https://www.tradingview.com/script/gz5FtyXZ-RVOL-Relative-Volume-Breakout-Confirmation/),
  [VWAP 눌림 예시](https://www.tradingview.com/script/huPnA8Rc-VWAP-Pullback-Reclaim-Planner-AGPro-Series/)와
  [Bollinger 전략 목록](https://in.tradingview.com/scripts/bollingerbandstrategy/)은 개념 중복을
  확인하는 자료다.
- [Binance Spot API 공식 문서](https://developers.binance.com/docs/binance-spot-api-docs)와
  [Binance 공개 데이터 저장소](https://github.com/binance/binance-public-data)는 공개시장 입력
  스키마와 과거 자료 출처를 확인하는 자료다.
- [Crypto time-series momentum](https://www.nber.org/papers/w24877),
  [Common risk factors in cryptocurrency](https://www.nber.org/papers/w25882),
  [Order Flow Imbalance](https://arxiv.org/abs/1011.6402)는 F15·F16·F17~F20의 가설 근거다.

## 추가 24개 느린 추세 보조리그

기존 100후보는 5분~6시간 신호와 5개 청산모듈을 넓게 비교한다. 사용자가 선호한 “상승초입,
돌파 뒤 짧은 조정, 흐름 재합류”를 더 직접적으로 검사하기 위해 HYP-117에 1h·4h 전용 24후보를
별도 사전등록했다. 이는 기존 100후보의 숫자를 부풀리는 대체물이 아니라 다음 차이를 확인하는
보조리그다.

- BTC와 전체 종목 breadth로 상승·하락 레짐을 먼저 제한한다.
- 같은 시점의 72h 상대강도 순위로 강한 종목의 LONG과 약한 종목의 SHORT를 분리한다.
- 1h 첫 눌림과 돌파 재확인, 4h 채널 돌파와 상대모멘텀을 분리한다.
- 방향별 `LONG`, `SHORT`, `BOTH`와 `BALANCED`, `SELECTIVE`를 미리 고정한다.
- 고정 900초 청산 없이 TP1·TP2·구조 SL로 판정하고 미결 포지션은 검열표본으로 남긴다.

## 병렬 실행 경계

- 100후보 연구리그는 동일 동결 입력에서 90개 실행가능 후보를 모두 평가하고 10개 차단 후보도
  결과표에 보존한다.
- 입력, 수수료, 슬리피지, 시간순 split과 후보 fingerprint는 실행 전에 고정한다.
- 후보 하나가 실패해도 다른 후보의 입력이나 계좌가 바뀌지 않는다.
- 후보 수가 많다는 이유로 결과 중 최고 승률만 고르지 않는다. 30건 미만은 순위를 매기지 않고
  BASE·STRESS, expectancy, PF, payoff, drawdown, bootstrap, DSR, PBO와 독립 미래표본을 함께 본다.
- 역사 연구는 실시간 서비스와 CPU·메모리·디스크 I/O를 격리하고, 실시간 지연이나 저장장애가
  생기면 연구를 중단한다.
- 역사 gate를 통과한 후보만 별도 BASE·STRESS LIVE_PUBLIC SHADOW 계좌에 올린다. 100개를 그대로
  대시보드 실시간 전략으로 복제해 시스템 지연과 중복표본을 만드는 방식은 채택하지 않는다.

## 해석 금지

- 공개 게시물의 수익률, 좋아요 수, 인기와 백테스트 곡선을 ROBOM 성과로 가져오지 않는다.
- Pine 코드나 유료 전략을 복사하지 않는다.
- 100개 중 가장 좋아 보이는 한 결과를 독립 검증 없이 채택하지 않는다.
- 신호가 적다는 이유로 비용, 진입조건, TP·SL과 안전계약을 낮추지 않는다.
- 테스트 통과는 구현 정확성 증거이지 수익성 증거가 아니다.
