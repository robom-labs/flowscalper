# 20. 연구 근거와 암호화폐 적용 차이

이 문서는 FlowScalper의 연구 전용 후보가 어떤 1차 자료에서 출발했고, 주식시장 연구를 24시간 암호화폐 USDⓈ-M 공개시장에 그대로 옮기지 않기 위해 무엇을 바꿨는지 기록한다. 논문과 거래소 문서는 가설의 출처일 뿐 수익성 증거가 아니다. 실제 채택 판단은 저장된 `LIVE_PUBLIC` 공개시장 이벤트의 시간순 Train·Validation·OOS와 BASE·STRESS 비용 모형으로만 한다.

## 20.1 1차 자료와 적용 범위

| Source ID | 직접 자료 | 원자료가 보여 준 범위 | FlowScalper에서 시험하는 가설 | 그대로 이식하지 않는 부분 |
|---|---|---|---|---|
| SRC-OFI-2010 | Cont, Kukanov, Stoikov, [The Price Impact of Order Book Events](https://arxiv.org/abs/1011.6402) | 미국 주식 50종목에서 단기 가격변화와 top-of-book order-flow imbalance, 시장깊이의 관계를 분석했다. | 깊이보정 OFI, spread·유동성·실행가능 비용을 함께 쓰는 continuation 후보를 연구한다. | 주식 tick 구조, 거래시간, 비용과 종목별 계수는 재사용하지 않는다. |
| SRC-QI-2015 | Gould, Bonart, [Queue Imbalance as a One-Tick-Ahead Price Predictor in a Limit Order Book](https://arxiv.org/abs/1512.03492) | Nasdaq 10종목의 다음 mid-price 움직임과 queue imbalance 관계를 분석했다. | 실제 양방향 호가와 sequence가 정상일 때만 queue 비대칭을 보조 피처로 사용한다. | one-tick 예측력을 PAPER 순수익으로 간주하지 않으며 비용·지연·depth 체결을 별도 검증한다. |
| SRC-MLOFI-2019 | Xu, Gould, Howison, [Multi-Level Order-Flow Imbalance in a Limit Order Book](https://arxiv.org/abs/1907.06230) | Nasdaq 6종목에서 여러 호가 단계의 OFI가 표본외 설명력을 개선하는지 분석했다. | top 10 depth, 깊이보정 OFI, 호가 기울기와 다중단계 공정가 후보를 연구한다. | 연구 표본의 자산·tick size·세션을 crypto 선물에 일반화하지 않는다. |
| SRC-MICROPRICE-2017 | Stoikov, [The Micro-Price: A High-Frequency Estimator of Future Prices](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2970694) | spread와 imbalance로 단기 공정가격을 추정하는 micro-price 접근을 제시한다. | 현재 코드의 microprice와 다중호가 공정가를 방향 확인 입력으로 쓰되 단독 신호로 쓰지 않는다. | 추정 공정가의 방향 적중을 체결가능 수익으로 바꾸어 해석하지 않는다. |
| SRC-PBO-2015 | Bailey et al., [The Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253) | 많은 후보 중 최선만 선택할 때 생기는 백테스트 과적합 확률을 평가한다. | 거래가 0인 사전등록 가설까지 후보 수에 포함하고, 시간순 Run fold의 PBO를 기록한다. | PBO 하나만 낮다고 전략을 승격하지 않는다. |
| SRC-DSR-2014 | Bailey, López de Prado, [The Deflated Sharpe Ratio](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551) | 비정규 수익과 다중 시험의 선택 편향을 고려해 Sharpe 유의성을 조정한다. | 단기 거래 수익을 연환산하지 않고, 시험 수를 반영한 DSR과 bootstrap 기대값 하한을 함께 기록한다. | 표본 부족 DSR은 0이나 PASS로 대체하지 않고 `INSUFFICIENT`로 둔다. |
| SRC-CRYPTO-MOMENTUM-2018 | Liu, Tsyvinski, [Risks and Returns of Cryptocurrency](https://www.nber.org/papers/w24877) | 암호화폐 수익률에서 모멘텀 요인의 표본상 관계를 분석했다. | 완성 시간봉의 중기 모멘텀과 추세 정렬을 비용후 후보로 시험한다. | 논문의 기간·자산·수익률을 현재 USDⓈ-M 초단기 PAPER 성과로 간주하지 않는다. |
| SRC-TSMOM-2012 | Moskowitz, Ooi, Pedersen, [Time Series Momentum](https://www.sciencedirect.com/science/article/pii/S0304405X11002613) | 58개 선물의 월 단위 표본에서 1~12개월 수익 지속성을 분석했다. | 추세 방향·다중 시간축 정렬·고정 위험계획이라는 가설 출처로만 사용한다. | 월 단위 전통자산 결과를 15분·30분 암호화폐 수익성으로 일반화하지 않으며 후속 재검토 연구의 약한 예측력 지적도 함께 고려한다. |
| SRC-CRYPTO-TREND-2020 | Rozario et al., [A Decade of Evidence of Trend Following Investing in Cryptocurrencies](https://arxiv.org/abs/2009.12155) | 암호화폐 과거 표본의 walk-forward 추세추종 결과를 보고했다. | 완성봉 추세·눌림·돌파 재확인 후보를 동일 비용조건의 PAPER 가설로 시험한다. | 보고된 성과를 재현 또는 보장한다고 보지 않고, 현재 공개 bid·ask·BASE·STRESS·미래 OOS에서 별도 반증한다. |
| SRC-BINANCE-AGGTRADE | Binance USDⓈ-M Futures, [Aggregate Trade Streams](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Aggregate-Trade-Streams) | 인증 없는 공개 aggregate trade event 필드와 전송 주기를 정의한다. | event time, 가격, 수량, aggressor 방향을 canonical candle과 flow에 사용한다. | 수신 지연이 큰 trade는 archive에는 보존하되 현재 전략 입력에서는 fail-closed한다. |
| SRC-BINANCE-DEPTH | Binance USDⓈ-M Futures, [Diff. Book Depth Streams](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Diff-Book-Depth-Streams) | 공개 depth delta와 update ID를 이용한 로컬 호가장 복원 절차를 정의한다. | sequence-valid top-of-book의 반대쪽 bid·ask로 진입·청산을 평가한다. | sequence gap, stale, 500ms 초과 실행호가는 진입에 사용하지 않는다. |
| SRC-BINANCE-KLINE | Binance USDⓈ-M Futures, [Kline/Candlestick Streams](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Kline-Candlestick-Streams) | 공개 kline 필드와 마감 여부를 정의한다. | 거래 이벤트로 자체 canonical candle을 만들고 공식 필드 의미를 대조한다. | 진행 중 봉은 연구 피처에 넣지 않고 완성 봉만 사용한다. |

## 20.2 구현된 연구 경계

- 실행 레지스트리 A~K와 연구 후보는 서로 다른 모듈이다. `backend/app/intraday/`와 연구 스크립트는 연구 결과만 만들고 명시적 정책 결정 없이 `StrategyRegistry`를 변경하지 않는다.
- `CandleBuilder`는 event ID 중복을 제거하고 종목별 늦은 이벤트를 무시하며, 1초부터 4시간까지 동일한 경계 규칙으로 완성 봉만 내보낸다.
- OHLCV, quote volume, trade count, taker buy/sell base·quote volume을 canonical candle에 보존한다.
- multi-timeframe feature는 ATR, realized volatility, session VWAP, EMA, Donchian, Bollinger, Keltner, RVOL, taker flow, 상위 시간구간 trend/regime을 동일 시각까지의 완성 봉으로 계산한다.
- 현재 처리 중인 봉이나 `as_of_ts_ms` 뒤의 봉을 사용하면 계산을 거부한다. 동일 event ID와 종목별 out-of-order trade도 피처에 중복 반영하지 않는다.
- `ORIGINAL`과 `MECHANICAL_MIRROR`는 동일 signal timestamp·information set·ATR·최대보유시간을 공유하고 방향, stop, TP1, TP2를 대칭으로 만든다. 둘 중 하나가 이미 pending/cooldown이면 쌍 전체를 진입시키지 않는다.
- `HYPOTHESIS_REVERSE`는 원 신호의 방향만 뒤집지 않는다. range extreme continuation과 false-break adverse-flow처럼 별도 사전조건을 사용한다.
- LONG 진입은 실제 ask, SHORT 진입은 실제 bid를 사용한다. LONG 청산은 bid, SHORT 청산은 ask를 사용하며 BASE 13bp와 STRESS 25bp 비용을 별도로 차감한다.
- stop은 불리한 한 봉 안에서 TP보다 먼저 평가하고, TP1 70%·TP2 30%를 한 거래로 합산한다. 기간 끝의 열린 거래는 성과에 넣지 않고 censored로 기록한다.

## 20.3 사전등록 후보와 시간구간

연구 grid는 12개 horizon/timeframe 조합, 5개 서로 다른 후보 family, 3개 변형으로 총 180개다.

- `MICRO_SCALP`은 1초·5초·15초·30초와 1분 상위 구간을 사용하며 최대 보유 180초다.
- `FAST_INTRADAY`는 1분·3분·5분·15분과 5분·15분·1시간 상위 구간을 사용하며 최대 보유 3,600초다.
- `INTRADAY_SWING`은 15분·30분·1시간·4시간과 1시간·4시간 상위 구간을 사용하며 최대 보유 21,600초다.
- 후보 family는 flow trend pullback, compression+RVOL breakout, range VWAP reversion, higher-timeframe trend entry, absorption/refill reverse다.
- 기계적 미러 60개는 방향 대조 baseline으로만 쓴다. ORIGINAL 60개와 별도 역가설 60개, 합계 120개가 선택 후보 수와 DSR 시험 수에 들어간다.
- 신호나 거래가 0이었던 사전등록 후보도 trial count에서 제거하지 않는다.

## 20.4 시간순 검증과 승격 금지선

- Run ID는 실행 전에 Train 6개, Validation 2개, OOS 5개로 고정한다.
- 각 horizon의 최대 보유시간만큼 split 경계 양쪽을 purge·embargo한다.
- 같은 archive, 코드 commit, config hash, seed, dataset checksum과 결과 checksum을 research manifest에 기록한다.
- 결과에는 no-trade baseline, BASE/STRESS, sample count, win/loss, expectancy, Profit Factor, drawdown, downside deviation, PBO, DSR, deterministic bootstrap 95% 구간, mirror signal parity와 수익 상관을 포함한다.
- 표본 30 미만, BASE 기대값/PF 실패, STRESS 기대값 실패, bootstrap 하한 0 이하, DSR 0.95 미만 또는 PBO 0.20 초과 중 하나라도 있으면 `NOT_PROVEN`이다.
- 모든 수치 gate를 통과해도 기존 전략 ID를 자동 교체하지 않는다. 별도 신규 strategy ID, 코드 리뷰, SHADOW 승인과 충분한 자연 `LIVE_PUBLIC` 표본이 추가로 필요하다.
- 부분 Run 또는 `--maximum-events` 결과는 `PARTIAL_DIAGNOSTIC_NOT_EVIDENCE`이며 수익성 증거로 사용할 수 없다.

## 20.5 알려진 한계

- 확보된 archive는 하나의 공개 거래소·제한된 달력 기간이다. 독립적인 미래 기간과 다른 시장구조에 대한 외적 타당성이 아직 없다.
- funding, open interest와 liquidation은 이번 후보 입력에 넣지 않았다. 공식 공개 endpoint, point-in-time 보존, 결측·지연 품질 검증이 먼저 필요하다.
- PBO·DSR·bootstrap은 과적합 위험을 줄여 보여 줄 뿐 미래 수익을 보장하지 않는다.
- 장중 후보의 코드·테스트 통과와 저장 archive 결과는 실제 장시간 LIVE 성능 검증을 대신하지 않는다.

관련 결정은 `docs/adr/ADR-039-preregistered-intraday-research-and-runtime-separation.md`에 기록한다.

## 20.6 Wave 39~41 공개 5분봉·시간봉 진단

- `scripts/research_public_trend_candidates.py`는 Binance USDⓈ-M 주요 12종목의 완성 5분봉 414,720개를 사용해 사전등록 추세 후보 6개를 동일 BASE·STRESS 비용으로 평가했다. 모든 후보가 두 비용조건에서 음수여서 선택 0, PBO 0.6286, `NOT_PROVEN`이었다.
- `scripts/research_public_hourly_trend_diagnostic.py`는 완성 1시간봉에서 EMA 정렬, 24시간 모멘텀, Donchian 돌파, ADX와 상대거래량을 결합했다. Wave 41 적응 후보는 진단 OOS 42건에서 BASE 기대값 +32.212bp·PF 1.346, STRESS +20.212bp·PF 1.202였다.
- 같은 결과의 bootstrap 95% 하한은 -48.537bp, DSR은 0, PBO는 0.3714로 승격 기준을 실패했다. 후보 선택과 OOS가 완전히 독립된 미래 기간도 아니다.
- Wave 46은 K의 조건을 먼저 고정한 뒤 이전 2025-12-01~2026-04-26 공개시장 구간을 다운로드해 독립 재현했다. 147일·166건에서 BASE 기대값 -18.263bp·PF 0.856, STRESS 기대값 -30.263bp·PF 0.775, bootstrap 95% 하한 -60.868bp였고 양의 기여는 ADA에 64.71% 집중됐다. K는 RETIRED·OFF로 전환하며 소스·불변 거래·두 독립계좌는 보존한다.
- 같은 Wave의 사전등록 15분·30분 pullback·breakout·momentum 후보 4개도 개발 STRESS gate를 통과하지 못해 선택과 Registry 추가가 모두 0이다. 30분 돌파의 BASE 기대값은 +2.257bp였지만 STRESS는 -9.743bp여서 비용 강건성을 실패했다.
- 기계판독 원본은 `evidence/WAVE39_PUBLIC_TREND_RESEARCH.json`, `evidence/WAVE40_PUBLIC_HOURLY_TREND_DIAGNOSTIC.json`, `evidence/WAVE41_PUBLIC_COST_AWARE_TREND_DIAGNOSTIC.json`, `evidence/wave46-strategy-survival/fixed-hourly-prior-holdout.json`, `evidence/wave46-strategy-survival/intraday-trend-diagnostic.json`이다. 이 결과는 전략 조건을 낮추거나 실제 주문 경로를 추가하는 근거가 아니다.

관련 정책 결정은 `docs/adr/ADR-045-cost-aware-hourly-trend-shadow-and-evidence-retirement.md`에 기록한다.
Wave 46의 독립 재현·퇴역·기본 SHADOW 정책은 `docs/adr/ADR-047-strategy-survival-governor-and-outcome-timing.md`에 기록한다.
