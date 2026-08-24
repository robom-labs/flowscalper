# 05. Strategy and Signal Specification

## 5.1 Philosophy

The first version uses deterministic market-microstructure rules. It does not ask GPT whether to buy or sell. The engine evaluates whether aggressive order flow produces the expected price response and whether the setup remains attractive after spread, fees and slippage.

The strategies are hypotheses to be tested, not known profit sources.

## 5.2 Core features

Calculate at multiple windows where supported:

- best bid/ask and mid;
- spread in ticks and bps;
- top-1, top-5, top-10 weighted book imbalance;
- microprice and microprice-minus-mid;
- top-10 multi-level microprice/fair-price displacement;
- OFI at 250 ms, 1 s, 3 s and 10 s;
- 3 s OFI normalized by top-10 average depth notional;
- aggressive trade imbalance;
- signed traded notional;
- price-response efficiency;
- refill ratio and refill time;
- add/cancel ratio;
- realized volatility;
- price efficiency ratio;
- micro-VWAP;
- range boundaries and structural swing clusters;
- volume/activity z-scores;
- data quality and lag.

Use robust scaling based on rolling median/MAD or rolling percentiles. Do not compare raw OFI across unrelated symbols without normalization.

## 5.3 Regime classifier

States:

- `TREND_UP`
- `TREND_DOWN`
- `RANGE`
- `SHOCK`
- `DEGRADED`
- `WARMUP`

Suggested inputs:

- 30 s and 60 s efficiency ratio;
- 30 s and 120 s realized volatility percentiles;
- micro-VWAP slope normalized by volatility;
- OFI sign persistence;
- high/low structure;
- spread/depth regime;
- processing/data freshness.

`SHOCK`, `DEGRADED` and `WARMUP` prohibit new entries.

## 5.4 Strategy A — Liquidity Sweep Absorption Reversal

### Long setup

1. Detect a validated support/reference level:
   - clustered 1 m/5 m swing low;
   - rolling range low;
   - repeated defended price;
   - optional micro-VWAP deviation boundary.
2. Price trades below the level by a minimum sweep distance but not beyond the maximum allowed extension.
3. Aggressive sell activity is elevated relative to that symbol's recent distribution.
4. Downward price-response efficiency is weak: large signed sell flow produces limited additional decline.
5. Bid depth/refill appears after the sweep.
6. OFI turns positive or materially improves.
7. Microprice crosses or stabilizes above mid.
8. Executable price returns above the reference level and persists for the configured confirmation duration.
9. Regime is RANGE or weak/non-shock trend compatible with reversal.
10. Cost, stop, target, risk and data gates pass.

### Short setup

Apply the exact symmetric logic at resistance/highs.

### Starting parameter ranges

Use a small documented research grid, not aggressive optimization:

- reference lookback: 5, 15, 30 minutes;
- sweep extension: 0.5–2.5 local noise units;
- flow z-score threshold: 1.5–3.0;
- re-entry confirmation: 300–1,000 ms;
- refill window: 500–3,000 ms;
- maximum current spread: venue/symbol percentile plus hard cap.

### Required rejection cases

- price continues efficiently in sweep direction;
- refill is only a fleeting single update;
- book is stale or sequence-invalid;
- spread widens into shock range;
- market-wide shock filter active;
- no viable cost-adjusted target;
- stop is too close to noise or too far from reward.

## 5.5 Strategy B — Compression Breakout Pullback Reacceleration

### Long setup

1. Short-term realized volatility and range width are compressed relative to the symbol's recent history.
2. Liquidity and spread remain normal; compression is not caused by data staleness.
3. Price breaks above a validated boundary with positive OFI and aggressive buy flow.
4. Price response confirms that buy flow actually moves price.
5. Do not enter the initial impulse.
6. Wait for a pullback lasting approximately 1–10 seconds.
7. Pullback retraces a configurable fraction, initially 20–60% of the impulse.
8. Aggressive selling during pullback has weak adverse price impact.
9. Bid depth/refill and microprice recover.
10. OFI, trade imbalance and executable price reaccelerate upward for the confirmation period.
11. Regime is TREND_UP or transition into trend without SHOCK.
12. Cost, stop, target, risk and data gates pass.

### Short setup

Apply symmetric logic below support.

### Required rejection cases

- initial impulse is already too extended;
- pullback breaks structural invalidation;
- counter-flow moves price efficiently;
- spread/depth quality deteriorates;
- target is blocked by nearby liquidity and net reward is inadequate;
- data gap or processing lag occurs.

## 5.6 Strategies G and H — SHADOW-only research hypotheses

`MULTILEVEL_MICROPRICE_MOMENTUM_V1` uses a top-10 cross-weighted bid/ask VWAP fair price. It requires the multi-level fair price, top microprice, 250 ms and 3 s OFI, one-second aggressor flow and price-response efficiency to align for at least 750 ms. It is rejected in WARMUP, DEGRADED or SHOCK and remains SHADOW by default.

`DEPTH_ADJUSTED_OFI_IMPULSE_V1` converts three-second OFI to notional, divides it by average top-10 bid/ask depth notional and expresses the result in basis points. It requires a directional robust z-score of at least 2.0 from the prior same-symbol history, aligned OFI, aggressor flow, microprice and price response for at least 500 ms. It remains SHADOW by default.

Both strategies are PAPER-only, evaluate LONG and SHORT symmetrically, use independent BASE/STRESS accounts and cannot enter shared capital unless a user explicitly changes the mode after adequate evidence. The starting thresholds are research controls, not profitability claims. See ADR-016.

## 5.7 Strategy I — OFI and trailing-return confluence

`OFI_RETURN_CONFLUENCE_V1` tests a distinct SHADOW-only hypothesis. It requires depth-adjusted three-second OFI robust z of at least 1.5, aligned 250ms and three-second OFI, a same-direction three-second trailing return of at least 2bp, microprice displacement of at least 0.20bp and price-response efficiency of at least 0.30 for 1,000ms. The return anchor is the nearest same-symbol prefix snapshot at or before the three-second horizon and may be at most 1.5 seconds older; future timestamps are ignored.

The strategy does not require trade imbalance as an additional gate because it isolates OFI plus recent price-path confluence rather than duplicating E/F/H. It remains EXPERIMENTAL, PAPER-only and SHADOW by default. Thresholds are starting research controls, not evidence of profitability. See ADR-019.

## 5.8 Candidate scoring

A candidate score must be decomposable:

```text
candidate_score =
    structure_quality
  + flow_confirmation
  + price_response_quality
  + liquidity_quality
  + regime_fit
  - cost_penalty
  - latency_penalty
  - shock_penalty
  - uncertainty_penalty
```

The UI must show component scores and rejection reasons. A high score alone cannot override a hard safety gate.

## 5.9 Cold-start behavior

Before sufficient data:

- use rule-only paper decisions;
- display `CALIBRATING` instead of a fabricated TP probability;
- store all qualified and near-miss candidates, not only executed trades;
- collect enough outcome labels for later validation.

## 5.10 No forced trade count

The research target may be several trades per day across the universe, but the engine must allow zero trades. Never lower thresholds to satisfy a count target.

## 5.11 Executable cost geometry and temporal confirmation

- REVERSION A/C use a minimum 0.80% structural stop distance. TREND B/D/E/F/G/H/I use a minimum 0.30% distance.
- The distance is not added risk. Risk-based sizing reduces quantity so the account risk budget remains unchanged.
- Final eligibility is recalculated from executable bid/ask, worst entry, both-side fees, expected exit slippage and the configured split exits. Net reward-risk below 1.20 remains rejected.
- A-D confirmation durations use venue event timestamps and reset immediately when alignment breaks.
- B/D pullback duration, maximum retrace and price reacceleration use only the same-symbol history prefix whose timestamp is earlier than the decision snapshot.
- These are documented PAPER research defaults, not evidence of profitability. See ADR-013.
