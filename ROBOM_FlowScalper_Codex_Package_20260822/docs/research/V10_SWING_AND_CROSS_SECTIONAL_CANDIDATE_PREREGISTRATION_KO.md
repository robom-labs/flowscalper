# V10 중단타·스윙·횡단면 후보 사전등록

## 상태와 적용 경계

이 문서는 2026-09-01 기준 `PRE_REGISTERED_NOT_RUN`이다. 사용자 제공 V10 명세의 신규
후보를 기존 V6 runtime 전략과 V9 연구 추적 후보에 섞지 않고, 성과를 보기 전에 역할·중복
판정·검증 gate·노출 경계를 고정한다. 기준 source commit은
`1a513cea3ac5c788ee97eeb9ade217a5fdf50967`이다.

V10 후보는 정확히 6개다. 모두 공개시장 데이터만 사용하는 PAPER 연구이고 현재 상태는 다음과
같다. 이 commit은 V10 통합 완료 commit이 아니라 V10 파일을 추가하기 직전의 baseline이다.

- 기본 runtime Registry 등록 0개, runtime entry 0개, `ACTIVE` 0개다.
- 기본 사용자 전략 목록과 `/api/strategy-families`, `/api/strategies/summary`,
  `/api/ui/summary`에 노출하지 않는다.
- 기본 스위치 OFF는 이 V10 신규 후보 6개에만 적용한다. 기존 실행 가능 V6 SHADOW 후보 6개의
  기본 연구 ON과 V9의 읽기 전용 연구 추적 ON 상태를 바꾸거나 OFF로 내리지 않는다.
- offline trial manifest·trial ledger에만 기록할 수 있고, 기본 원장·최종 순위·Governor 승격
  입력에서는 제외한다.
- 실제 주문, private API, 인증, API Key, secret, wallet, 입출금, runtime AI 주문판단은 모두
  0이다.
- 코드·단위검사·문서 통과는 수익성 증거가 아니다. BASE·STRESS OOS gate 전 수익성은
  `NOT_PROVEN`, 실자금 준비상태는 `NOT_READY`다.

## 현재 V6·V9 기준선과 숫자의 의미

현재 canonical source의 V6 catalog는 실제 runtime Registry variant 15개와 Registry에 없는 가상
orderflow filter 1개를 합쳐 catalog item 16개다. 기본 사용자 전략 화면은 이 16개를 그대로
나열하지 않고 current entry representative 3개만 보여 준다. 비용후 연구 진입 후보는 6개지만
`ACTIVE` 방향진입은 0개다.

V9는 별도 `v9_research` manifest의 12개 연구 모듈·후보를 읽기 전용 추적 항목으로 보여 준다.
V9의 추적 ON 12개는 entry ON을 뜻하지 않으며 runtime entry 0개, entry enabled 0개,
`ACTIVE` 0개다. V10 6개는 이 V9 manifest에도 합치지 않는다. 따라서 다음 수를 서로 더해
“34개 거래 전략”이라고 표현하면 안 된다.

| 구분 | 현재 수 | 사용자 의미 |
|---|---:|---|
| V6 registered catalog item | 16 | runtime variant 15개와 가상 filter 1개의 catalog다. |
| V6 runtime Registry variant | 15 | 실제 Registry ID 수다. V10으로 늘리지 않는다. |
| V6 current entry representative | 3 | 기본 전략 화면에서 보는 current entry 수다. |
| V6 enabled directional research candidate | 6 | PAPER 연구 진입 가능 후보이며 `ACTIVE` 수가 아니다. |
| V9 research tracking | 12 | 읽기 전용 추적 ON이며 runtime entry·`ACTIVE`는 0이다. |
| V10 preregistered candidate | 6 | 기본 OFF·hidden이며 runtime entry·`ACTIVE`는 0이다. |

## 정확히 6개 후보와 readiness

여기서 `NOT_RUN`은 명세만 동결했고 offline screen을 실행하지 않았다는 뜻이다. `BLOCKED`는
후보를 계산·체결할 필수 public-data pipeline 또는 multi-symbol·multi-leg engine이 아직 연결되지
않았다는 뜻이다. 어느 상태도 SHADOW·CHALLENGER·ACTIVE 또는 수익성 통과를 뜻하지 않는다.

| # | Candidate ID | Family | 역할 | 현재 stage | Readiness | 채택 사유 |
|---:|---|---|---|---|---|---|
| 1 | `SWING_MULTI_HORIZON_TREND_4H1D_V1` | `TREND_PULLBACK` | `ENTRY` | `RESEARCH_SPEC` | `RESEARCH_SPEC` | 15분·30분보다 느린 1D/4H/1H 정렬과 6시간~5일 runner를 같은 family challenger로 검증해 시간축 분산 가능성을 본다. |
| 2 | `DAILY_DONCHIAN_RETEST_1D4H_V1` | `BREAKOUT_RUNNER` | `ENTRY` | `RESEARCH_SPEC` | `RESEARCH_SPEC` | 일봉 55일 돌파를 즉시 추격하지 않고 4H retest·1H 재확인을 요구해 큰 추세의 비대칭 payoff를 연구한다. |
| 3 | `CFTC_CME_BITCOIN_CROWDING_FILTER_V1` | `MARKET_REGIME_FILTERS` | `FILTER` | `RESEARCH_SPEC` | `BLOCKED_SOURCE_PIPELINE` | 공개 CFTC TFF의 늦게 발표되는 주간 포지션을 방향 신호가 아닌 crowding quality·veto로 검증한다. |
| 4 | `CRYPTO_FUTURES_CURVE_REGIME_FILTER_V1` | `POSITIONING_LIQUIDATION` | `FILTER` | `RESEARCH_SPEC` | `BLOCKED_SOURCE_PIPELINE` | 현물·perpetual·근월·원월의 공개 term structure로 crowded long·backwardation을 구분하며 독립 entry를 만들지 않는다. |
| 5 | `RESIDUAL_14D_RELATIVE_STRENGTH_V1` | `TREND_PULLBACK` | `ENTRY_RESEARCH` | `RESEARCH_SPEC` | `BLOCKED_POINT_IN_TIME_UNIVERSE` | BTC·ETH·시장수익률을 제거한 point-in-time 횡단면 상대강도를 4H 구조 진입과 결합해 단일종목 추세와 다른 선택 효과를 검증한다. |
| 6 | `BASIS_MOMENTUM_CROSS_SECTIONAL_RESEARCH_V1` | `MARKET_NEUTRAL` | `MARKET_NEUTRAL_MULTI_LEG` | `RESEARCH_SPEC` | `BLOCKED_ENGINE` | basis·funding·curve의 횡단면 설명력을 방향 sign 고정 없이 학습하고 dollar-neutral 다중 leg로만 검증한다. |

집계 불변조건은 `candidate_count=6`, 방향 entry 3개(`ENTRY` 2개와 `ENTRY_RESEARCH` 1개),
filter 2개, market neutral 1개, `runtime_entry_registered_count=0`, `active_count=0`이다.

## 후보별 고정 연구 계약

### 1. Multi-horizon trend swing

`SWING_MULTI_HORIZON_TREND_4H1D_V1`은 완료된 1D 봉에서 close가 EMA50 위,
EMA50이 EMA200 위, 5일 EMA50 기울기/ATR20이 0.15 이상, ADX14가 18 이상인 추세만
허용한다. 완료된 12h·24h·72h 변동성 조정 수익률의 부호 score가 LONG +2 이상 또는 SHORT
-2 이하일 때 4H EMA20 눌림을 최대 12시간 arm한다. 1H 이전 고점·저점 재돌파, body ratio,
CLV, RVOL과 taker imbalance가 같은 방향으로 확인돼야 한다.

BASE cost coverage는 3.0 이상, STRESS는 1.75 이상으로 고정한다. TP1은 1.5R에서 25%,
나머지 75% runner는 2R부터 3.5 ATR4H 또는 완료 Donchian10 trail을 사용한다. 7일은 일반
시간청산 목표가 아니라 data·funding·recovery 안전상한이다. SHORT는 대칭 규칙을 별도 trial로
검증한다.

### 2. Daily Donchian retest runner

`DAILY_DONCHIAN_RETEST_1D4H_V1`은 완료 일봉 종가가 이전 55개 완료 일봉 고점보다
0.10 ATR20 이상 돌파하고 body ratio 0.50, CLV 0.75, RVOL20 1.25, ADX14 20 이상일 때만
arm한다. 돌파점에서 1 ATR1D를 넘는 과도한 추격은 거부한다. 이후 6개 완료 4H 봉 안에서
Donchian level의 -0.40~+0.30 ATR4H retest와 종가 회복을 확인하고, 1H retest-bar 돌파·RVOL·
taker imbalance가 확인돼야 한다.

TP1은 2R에서 20%, 나머지 80% runner는 4 ATR4H 또는 완료 Donchian20 trail을 사용한다.
14일은 안전상한이다. 기존 `BREAKOUT_RETEST_30M_V2`를 바로 교체하지 않고 같은 family에서
고정 입력 challenger 비교만 한다.

### 3. CFTC·CME Bitcoin crowding filter

`CFTC_CME_BITCOIN_CROWDING_FILTER_V1`의 primary series는 TFF Futures Only Bitcoin
CFTC code `133741`이다. Micro Bitcoin `133742`는 sensitivity에서만 분리한다. Leveraged Funds와
Asset Manager의 net position/open interest를 직전 156개 공개 주간 관측으로 robust Z 변환하며
104주 미만은 `UNCALIBRATED`, data age 10일 초과는 `UNAVAILABLE`이다.

보고 기준 화요일을 그 시점에 이미 알았던 것처럼 사용하지 않는다. 연도별 공식 release schedule의
예정 공개시각과 프로그램의 최초 관측시각을 구분하고 휴일·shutdown 지연을 반영한다. COT만으로
LONG·SHORT를 만들 수 없고 risk 증가도 금지한다. 예를 들어 leveraged-long Z, funding Z,
basis Z가 각각 +1.5 이상인 crowded long은 LONG veto 후보일 뿐이다. 반드시 filter OFF/ON A/B로
BASE·STRESS delta를 검증한다.

### 4. Crypto futures curve regime filter

`CRYPTO_FUTURES_CURVE_REGIME_FILTER_V1`은 public spot index, perpetual mark, near·far quarterly
future, expiry, OI와 funding을 사용한다. `annualized_basis=(future/spot-1)*365/days_to_expiry`,
`curve_slope=far_basis-near_basis`, `basis_momentum_7d=near_basis_t-near_basis_t-7d`를 과거
365일로만 표준화한다.

near-basis·funding·OI Z 중 2개가 +2 이상이면 `CROWDED_LONG_CURVE`, near basis가 음수이고
Z가 -1.5 이하이면 `BACKWARDATION_STRESS` 후보로 분류한다. 독립 entry와 risk 증가는 금지한다.
동시에 관측 가능한 만기가 하나뿐이면 curve를 추정하지 않고 `FILTER_DATA_UNAVAILABLE`로
fail closed한다.

### 5. Residual 14-day relative strength

`RESIDUAL_14D_RELATIVE_STRENGTH_V1`은 매 시점의 top-50 liquid perpetual universe를 보존해
survivorship bias를 차단한다. 현재 일봉을 제외한 이전 180개 완료 일봉으로 각 alt 수익률을
BTC·ETH·동일시점 equal-weight market 수익률에 회귀하고, 완료 residual 14일·28일 합의
point-in-time percentile을 계산한다.

LONG은 14일 percentile 80 이상, 28일 60 이상, 1D EMA50>EMA200과 4H uptrend를 요구한다.
최대 3종목, correlation cluster당 최대 1종목이며 4H pullback 또는 breakout trigger 없이는
진입하지 않는다. point-in-time universe·multi-symbol portfolio engine이 검증되기 전 runtime
연결은 `BLOCKED`다.

### 6. Basis-momentum cross-sectional market neutral

`BASIS_MOMENTUM_CROSS_SECTIONAL_RESEARCH_V1`은 perp basis, near-quarter basis,
7일 basis momentum, curve slope, funding과 OI change를 train 구간에서만 standardize하고 ridge와
walk-forward로 검증한다. 논문 abstract만 보고 방향 sign을 고정하지 않는다. 3개 fold 중 최소
2개에서 sign이 같지 않으면 폐기한다.

top quantile LONG과 bottom quantile SHORT를 dollar neutral로 묶고 최대 3 pair만 허용한다.
funding·fee·slippage·legging을 모두 포함하며 atomic multi-leg fill·unwind·partial-fill 안전 계약이
없으면 runtime에 연결하지 않는다.

## V6·V9 중복과 차이

| V10 후보 | 겹치는 기존 기능 | 새로 분리해 검증할 차이 | 노출 결정 |
|---|---|---|---|
| Multi-horizon trend swing | V6 `TREND_PULLBACK_RECLAIM_15M_V2`, `MULTISPEED_TREND_RECLAIM_30M_V2` | 1D regime와 4H setup, 1H trigger, 6시간~5일 runner | 새 기본 행이 아니라 같은 family hidden challenger다. |
| Daily Donchian retest | V6 `BREAKOUT_RETEST_15M_V2`, `BREAKOUT_RETEST_30M_V2`, legacy CBR | 55일 일봉 돌파 뒤 4H retest와 최대 14일 runner | current 교체 없이 hidden challenger다. |
| CFTC crowding filter | V6 `MARKET_REGIME_FILTERS`, V9 downside-risk·freshness 연구 | 주간 CFTC 포지션, publication lag, BTC global veto | entry가 아닌 hidden filter다. |
| Futures curve regime | V6 `POSITIONING_LIQUIDATION`, 기존 funding·OI·basis 조건 | near·far 만기 curve와 만기별 연율화, 한 계약이면 unavailable | entry가 아닌 hidden filter다. |
| Residual relative strength | V6 trend family, V9 e-BH·Pareto selection | point-in-time 횡단면 universe와 BTC·ETH·시장 residual portfolio | multi-symbol engine 전 hidden entry research다. |
| Basis momentum cross-section | V9 `COPULA_COINTEGRATED_PAIRS_1H_V2` | copula tail-dependence pair가 아니라 basis factor의 횡단면 long-short | atomic engine 전 별도 hidden market-neutral research다. |

신호가 기존 family와 80% 이상 겹치는 결과가 나오면 새 사용자 전략행을 만들지 않고 기존 family의
filter 또는 variant로 흡수한다. 겹침률도 최종 OOS를 보기 전에 정의한 동일 입력에서 계산한다.

## 사전등록 검증 gate

모든 후보는 다음 순서를 건너뛸 수 없다.

`RESEARCH_SPEC → OFFLINE_SCREEN → WALK_FORWARD → EVENT_REPLAY → FULL_PAPER_REPLAY → SHADOW → CHALLENGER → ACTIVE`

### Swing entry gate

- 최종 OOS unique opportunity 150개 이상, calendar 365일 이상, symbol 8개 이상이다.
- BASE EV와 STRESS EV가 각각 0 초과, PF 1.20 이상, payoff 1.70 이상이다.
- OOS EV bootstrap lower bound가 0 초과, DSR 0.95 이상, PBO 0.20 이하다.
- 독립 기간 2개 이상, 한 symbol 기여 25% 미만, 한 trade 기여 10% 미만이다.
- 완료 봉·point-in-time universe·고정 수수료·slippage·funding으로 no-lookahead를 재검증한다.

### Weekly filter gate

- 최소 104주이며 release-lag-aware backtest를 사용한다.
- filter retention 50% 이상이고 OFF 대비 delta EV lower bound가 0 초과다.
- 단독 direction·risk 증가가 0이며 unavailable·uncalibrated 상태는 fail closed한다.

### Market-neutral gate

- 독립 cycle 50개 이상, span 365일 이상이다.
- BASE·STRESS net이 각각 0 초과다.
- atomic fill test를 통과하고 funding·basis·fee·slippage·legging·partial-fill unwind를 포함한다.

어느 gate도 표본을 BASE·STRESS 계좌행으로 두 배 계산하지 않는다. 동일 opportunity의 비용가정은
한 기회로 묶고, final OOS를 본 뒤 threshold·universe·feature sign을 바꾸면 새 hypothesis version으로
다시 사전등록한다.

## 공식 출처와 사용 한계

### CFTC

- [CFTC TFF Futures Only 현재표](https://www.cftc.gov/dea/futures/financial_lf.htm)와
  [2026-06-02 고정 보관본](https://www.cftc.gov/sites/default/files/files/dea/cotarchives/2026/futures/financial_lf060226.htm)에서
  Bitcoin `133741`, Micro Bitcoin `133742`를 확인했다. 이는 CFTC 계약시장 code이지 거래소 ticker가
  아니다.
- [CFTC COT release schedule](https://www.cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm)은
  일반적으로 금요일 미국 동부시간 3:30pm 공개와 휴일 지연 일정을 제공한다. `America/New_York`
  timezone으로 DST를 반영하며 고정 UTC offset을 쓰지 않는다.
- [CFTC historical viewable 안내](https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalViewable/index.htm)의
  archive 표시일은 report date이지 개별 보고서의 실제 초 단위 release timestamp가 아니다.
  `scheduled_release_at`, 자체 수집한 `first_observed_at`, `ingested_at`을 분리한다. CFTC가 제공하지
  않은 값을 `official_actual_release_at`이라고 만들지 않는다.

### CME

- [CME Group의 24/7 출시 발표](https://investor.cmegroup.com/news-releases/news-release-details/cme-group-announces-launch-247-cryptocurrency-futures-and)는
  crypto futures·options 확장 거래가 2026-05-29 실제 가동됐다고 확인한다.
- [CME Globex notice](https://www.cmegroup.com/notices/electronic-trading/2026/05/20260525.html)는
  2026-05-29 4:00pm CT 전환, 주간 maintenance, 주말 거래의 다음 영업일 trade-date 처리를
  구분한다.
- [CME의 24/7 시장구조 설명](https://www.cmegroup.com/articles/2026/aligning-cryptocurrency-derivatives-with-spot-markets-measuring-the-247-trading-opportunity.html)은
  과거 금요일 4:00pm CT~일요일 5:00pm CT 폐장 구조가 전환 뒤 같은 전제로 유지되지 않음을
  설명한다.

외부 논문·공식 설명은 가설과 데이터 계약의 근거일 뿐 FlowScalper의 승률·EV·수익성 증거가
아니다. 이 문서에 넣지 않은 DOI·URL은 별도 원문 확인 전 만들지 않는다.

## 폐기한 가설

`CME_WEEKEND_GAP_FILL`의 판정은 `REJECTED / OBSOLETE_REGIME`이다. 2026-05-29 CME crypto
24/7 전환 이후에도 금요일 정규 폐장과 일요일 재개장이 항상 존재한다고 가정하는 현재 전략은
등록하지 않는다.

이 판정은 모든 주말 가격불연속이나 risk가 사라졌다는 뜻이 아니다. maintenance, halt, 얕은
유동성과 연속 급변은 별개다. 과거 가설을 다시 연구하려면 전환 전·후 microstructure epoch를
분리하고 calendar date와 CME trade date를 구분해야 하며, 두 regime을 한 현재 전략으로 섞을 수
없다.

## 현재 증거 상태

| 항목 | 상태 | 의미 |
|---|---|---|
| 6개 후보 명세·역할·중복 판정 | `PRE_REGISTERED_NOT_RUN` | 결과를 보기 전 문서 계약만 동결했다. |
| V10 기계판독 registry와 자체 단위검사 | `PASS` | exact ID·role·readiness·집계·출처·SHA·PAPER 안전 계약 10개가 통과했다. |
| 기본 runtime Registry·API·UI 비노출 | `PASS_FIXTURE_ONLY` | 별도 fixture 계약 1개가 통과했으며 설치된 8870 화면 증거는 아니다. |
| offline·walk-forward·event replay·full PAPER replay | `NOT_RUN` | 입력 동결과 실제 연구 실행을 하지 않았다. |
| SHADOW·CHALLENGER·ACTIVE | `NOT_RUN / ACTIVE_0` | 승격 실행이 없고 `ACTIVE`는 0이다. |
| 실제 설치 서비스·브라우저 | `NOT_RUN` | source fixture는 설치된 8870 화면 증거가 아니다. |
| 30분·6시간·24시간 관찰 | `NOT_RUN` | 실제 wall-clock 경과를 채우지 않았다. |
| 수익성·실자금 | `NOT_PROVEN / NOT_READY` | 사전등록은 수익성이나 실자금 준비 증거가 아니다. |
