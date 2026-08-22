# 04. Dynamic Symbol Universe

## 4.1 Objective

Monitor dozens of real contracts while avoiding symbols whose spread, depth, activity or data quality makes short-horizon paper trading misleading.

## 4.2 Eligibility filter

A contract is eligible only when all applicable conditions pass:

- active trading status;
- linear USDT perpetual contract;
- not prelaunch, settling, delivering or closed;
- quote and margin asset compatible with the Run;
- valid tick and quantity step;
- stable bid/ask availability;
- sufficient 24-hour quote turnover;
- acceptable rolling spread;
- acceptable executable depth;
- recent trades and no persistent data gaps;
- not a stablecoin/stablecoin pair;
- not a leveraged-token representation;
- not explicitly denylisted;
- minimum listing age when metadata permits.

## 4.3 Default sizing

- discovery candidates: all eligible active contracts;
- wide scan: up to 50;
- deep scan: 10;
- candidate shortlist: 3;
- open paper positions: 1.

These are maximums. The system may lower them based on CPU load, memory, WebSocket health and processing lag.

## 4.4 Ranking score

Use a normalized score such as:

```text
universe_score =
    + w_turnover * turnover_percentile
    + w_depth * executable_depth_percentile
    + w_activity * trade_activity_percentile
    + w_data * data_quality_score
    - w_spread * spread_percentile
    - w_volshock * shock_penalty
    - w_gap * gap_penalty
```

All inputs should be venue-relative and robust to outliers.

## 4.5 Recommended conservative initial filters

These are starting defaults, not universal facts:

- minimum 24h quote turnover: 20,000,000 USDT;
- maximum median spread: 8 bps;
- maximum current spread: 12 bps;
- minimum top-book executable notional: configurable by intended order size;
- minimum listing age: 90 days when available;
- minimum observed live-data warmup: 10 minutes before eligibility;
- no critical sequence gap in the current warmup window.

Use configuration and rolling venue statistics. Record the exact values in each Run.

## 4.6 Mandatory majors

BTC, ETH and SOL may receive discovery priority, but they do not bypass current spread, data-health or cost gates.

## 4.7 Deep-scan promotion

Promote a wide symbol when one or more conditions occur:

- approach to a validated short-term structural high/low;
- abnormal but non-shock trade activity;
- volatility compression near a range boundary;
- improving liquidity and narrowing spread;
- candidate precursor score above threshold.

Use hysteresis:

- minimum deep-scan dwell time;
- promotion threshold greater than demotion threshold;
- cooldown after demotion;
- no more than a configured number of changes per minute.

## 4.8 Exclusion reasons

Expose deterministic codes and Korean descriptions:

- `NOT_TRADING`
- `NOT_USDT_PERPETUAL`
- `NEW_LISTING`
- `LOW_TURNOVER`
- `WIDE_SPREAD`
- `LOW_DEPTH`
- `STALE_BOOK`
- `STALE_TRADES`
- `SEQUENCE_GAP`
- `SHOCK_STATE`
- `DENYLISTED`
- `SYSTEM_CAPACITY`

## 4.9 Symbol aliases and multipliers

Do not assume a symbol's displayed name equals one base token. Contracts such as multiplier-prefixed symbols must use venue metadata for contract size and display. Never infer economic exposure from the name string alone.
