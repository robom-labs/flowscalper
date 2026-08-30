# HYP-131. 작은 손실·큰 추세수익 비대칭 runner 60후보 사전등록

- 사전등록 상태. `LOCKED_BEFORE_EXECUTION` commit `1ada60d3a51ca35ce35e45c776270d41bdad8abb`.
- 실행 상태. `EXECUTED_NO_PROMOTION`.
- 등록일. 2026-08-30.
- 가설 ID. `HYP-131-ASYMMETRIC-TREND-RUNNER-TOURNAMENT`.
- 후보 지문. `63f6e317014bc342ee21c21bdec128dcd5b4e01f1dc109c38e7639e09308a44c`.
- 성과 상태. `NOT_PROVEN`.
- 실자금 준비. `NOT_READY`.

## 질문과 적응 연구 경계

사용자가 원하는 구조는 승률을 억지로 높이는 전략이 아니다. 잘못된 추세 시도는 최초 구조적
손절로 작게 끝내고, 실제 추세가 이어질 때 고정 익절로 수익을 잘라내지 않아 소수의 큰 승자가
여러 작은 손실과 비용을 상쇄하는 양의 비대칭 구조다.

HYP-130의 30개 거래량 확인 진입은 고정 TP1·TP2를 사용했다. 한 후보가 진단 OOS 평균은
양수였지만 bootstrap, DSR과 PBO를 실패했다. 이 결과를 본 뒤 청산 구조를 바꾸므로 HYP-131은
적응 역사 연구다. 마지막 30%도 독립 미래표본이라고 주장하지 않는다.

이번 한 가지 질문은 다음과 같다.

> HYP-130의 사전등록 진입 30개를 그대로 두고 고정 TP1·TP2와 부분익절을 제거한 뒤, +1R부터
> 이전 완성봉만 사용하는 22봉 Chandelier 추적손절로 끝까지 보유하면 실제 펀딩, 계좌 위험
> 40bp, BASE 13bp·STRESS 25bp 뒤에도 시간순 안정성과 양의 비대칭 gate를 함께 통과하는가?

## 공식·공개 근거와 제외한 주장

- [A Century of Evidence on Trend-Following Investing](https://www.aqr.com/-/media/AQR/Documents/Insights/Journal-Article/AQR-JPM-Fall-2017.pdf)은 손실을 제한하고 이익을 길게 보유하는 장기 추세 가설의 1차 근거다.
- [Trend Following in Focus](https://www.aqr.com/-/media/AQR/Documents/Whitepapers/Trend-Following-in-Focus_September-2018.pdf)는 소수의 크고 지속적인 추세가 비교적 작은 손실들을 상쇄할 수 있다는 비대칭 가설의 근거다.
- [Time Series Momentum](https://doi.org/10.1016/j.jfineco.2011.11.003)은 여러 선물시장의 시간계열 추세 지속을 연구한다. 암호자산 수익성 증명으로 옮기지 않는다.
- [Trend-following Strategies for Crypto Investors](https://www.monash.edu/__data/assets/pdf_file/0011/3744821/Trend-following-Strategies-for-Crypto-Investors.pdf)는 BTC·ETH 일봉 추세와 비용·변동성 조절을 분석한다. 과거·저빈도 결과를 현재 프로그램의 성과로 주장하지 않는다.
- [TradingView Chandelier Exit 공식 설명](https://www.tradingview.com/support/solutions/43000773013-chandelier-exit/)의 고점·저점과 ATR 배수 공식을 사용한다.
- [TradingView Donchian Channels 공식 설명](https://www.tradingview.com/support/solutions/43000502253-donchian-channels-dc/)과 [Supertrend 공식 설명](https://www.tradingview.com/support/solutions/43000634738-supertrend/)은 추세와 손절 아이디어를 대조하는 데만 사용했다. 기존 HYP-127과 100후보에 이미 있어 이름만 바꾼 후보로 복제하지 않는다.

TradingView 공개 스크립트, 유튜브, 검색결과의 `secret`, `best`, 승률·수익률 캡처는 검증값이
아니다. 공개 설명에서 완전한 기계 규칙을 추출할 수 없는 아이디어는 구현하지 않는다. 네이버
카페는 공개 검색과 직접 페이지 접근이 robots·로그인 경계로 막힌 글이 있어 읽었다고 주장하지
않고 후보 근거에서 제외한다.

## 고정 후보와 체결 규칙

- HYP-130의 진입 30개를 수정 없이 사용한다.
- 각 진입에 `CHAND22_ATR3`와 `CHAND22_ATR4`를 결합해 60개를 동시에 평가한다.
- 신호는 완성 4시간봉에서 확정하고 다음 봉 시가에 진입한다.
- 최초 손절은 HYP-130과 같은 신호봉·직전 두 봉 구조적 극값과 ATR 여유다.
- 최초 위험거리는 0.65~4ATR만 허용하고 계좌 위험은 거래당 40bp, notional은 1배 이하로 제한한다.
- +1R에 도달한 봉이 완전히 끝난 뒤부터 Chandelier를 활성화한다.
- LONG은 이전 완성 22봉 최고가에서 ATR14의 3배 또는 4배를 뺀 값, SHORT는 이전 완성
  22봉 최저가에 같은 배수를 더한 값을 사용한다.
- 현재 봉의 고가·저가·ATR은 현재 봉의 손절가 계산에 사용하지 않는다.
- 추적손절은 손실 방향으로 넓어지지 않는다.
- 같은 봉에서 최초 손절과 +1R이 모두 닿으면 최초 손절을 먼저 적용한다.
- 손절가를 넘어 시가 갭이 나면 손절가가 아니라 더 불리한 시가로 체결한다.
- 고정 익절, 부분익절, 일반 근거약화 청산, 고정 최대보유시간은 없다.
- 데이터 종료까지 손절이 확정되지 않은 포지션은 `CENSORED_OPEN`으로 남기고 채점하지 않는다.
- 물타기, 마틴게일, 피라미딩, 손절 확대와 자동 위험증가는 없다.
- 후보별 최대 동시 2포지션, UTC 하루 최대 2진입이다.

정확한 ID와 수치는
`scripts/research_asymmetric_trend_runner_tournament.py`의
`PREREGISTERED_ASYMMETRIC_TREND_CANDIDATES`가 유일한 실행계약이다. 결과를 본 뒤 같은 ID의
값을 바꾸지 않는다.

## 비용·시간순·양의 비대칭 gate

- Binance USDⓈ-M 공개 4시간 완성봉과 실제 공개 펀딩을 사용한다.
- BASE 왕복 13bp와 STRESS 25bp를 notional fraction만큼 차감한다.
- 50% Train·20% Validation·30% 진단 OOS와 7일 embargo를 유지한다.
- development 60건, Validation 20건, 진단 OOS 30건 미만은 순위를 매기거나 통과시키지 않는다.
- 6개 development walk-forward fold 중 평가 가능 5개, 양수 4개와 최근 2개 양수를 요구한다.
- 기존 BASE·STRESS 기대값·PF, bootstrap 95% 하한, DSR, PBO와 종목 집중 gate를 모두 유지한다.
- 추가로 OOS STRESS payoff 1.50 이상, STRESS 수익분포 왜도 양수, 단일 최대 승자 3R 이상을
  모두 요구한다.
- 승률 70%는 gate가 아니다. 낮은 승률도 비용 후 기대값, payoff, PF, drawdown과 강건성 gate를
  통과하면 가설과 일치할 수 있다.

역사 gate를 모두 통과해도 Registry에 바로 넣지 않는다. 실제 bid·ask 깊이와 BASE·STRESS
독립 계좌를 쓰는 미래 LIVE_PUBLIC SHADOW 자연표본 30건이 추가로 필요하다. 통과 후보가
없으면 Registry 변경은 0이다. 실패와 미결 결과도 삭제하지 않는다.

실제 주문, private API, API Key, secret, 인증, wallet, 입출금과 runtime AI 주문판단은 계속
0이다.

## 실행 결과

- 입력. 12종목 완성 4시간봉 148,824개와 실제 공개 펀딩 이벤트 74,487개.
- 전체 후보 중복 평가. 원신호 17,918개, 독립 포트폴리오 선택 10,221개, 완료거래
  10,211개, 데이터 끝 미결 10개.
- walk-forward 안정성 통과. 7개.
- Train·Validation·walk-forward 동시 선발. 4개.
- 전체 역사 강건성 gate 통과. 0개.
- PBO. `0.80`.

가장 두드러진 `T131_SQUEEZE_BREAKOUT_4H_BOTH_BALANCED_CHAND22_ATR4`는 진단 OOS
75건에서 BASE 기대값 +11.500 계좌 bp·PF 1.613, STRESS 기대값 +10.095 계좌 bp·PF
1.513이었다. 승률은 BASE 40.0%, STRESS 38.7%로 낮았지만 STRESS payoff 2.400,
수익분포 왜도 2.351, 최대 승자 9.670R로 사전등록한 양의 비대칭 형태는 나타났다. 승자 보유
중앙값 11,280분은 패자 4,560분보다 길었다.

그러나 bootstrap 95% 기대값 하한은 -4.372 계좌 bp, DSR 확률은 0, 전체 60개 후보 PBO는
0.80이었다. 다른 세 선발 후보도 평균과 양의 왜도는 통과했지만 bootstrap·DSR·PBO 또는
종목집중·payoff gate를 실패했다. 따라서 양수 평균을 미래 수익성으로 해석하지 않고 Registry와
LIVE SHADOW 승격은 0으로 유지한다.

전체 tournament를 두 번 실행해 생성시각을 제외한 canonical SHA-256
`7a79d2a9b441908bf969e5abc65d5dbac43b37bc61f24d0ffc6575330b664027`가 일치했다.
연구 두 번과 전체 회귀·프론트 빌드를 겹친 최초 300.017초 guard는 신규 500ms 초과
event-loop 지연 1회로 `FAIL`이며 삭제하지 않는다. 다른 검사를 멈추고 `nice -n 15` 연구
한 번만 겹친 150.018초 분리 guard는 event +10,658, 전략평가 +65,800, queue 최대 19,
처리·체결 p95 최대 31.215·77.898ms, 신규 500ms 초과·critical·비계획 재연결·gap·drop·
저장 fault·실제 주문·인증 0으로 `PASS`였다.

현 수용상태는
`HYP131_POSITIVE_SKEW_OBSERVED_ROBUSTNESS_FAILED_NO_PROMOTION_NOT_PROVEN_NOT_READY`다.
