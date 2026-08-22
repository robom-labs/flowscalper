# 10. Storage, Replay and Analytics

## 10.1 Storage layers

### SQLite

Use transactional tables for:

- app settings;
- Runs;
- universe snapshots;
- candidates;
- paper orders;
- fills;
- positions;
- trades;
- risk locks;
- system incidents;
- persisted state-machine snapshots.

### Parquet

Partition compressed market and feature data by:

```text
venue/date/symbol/hour/event_type
```

### DuckDB

Use for analytical queries across Parquet and exported reports.

## 10.2 Data retention

Suggested defaults:

- full deep-book events: 7 days;
- candidate/trade windows: retain indefinitely or until user deletes;
- aggregated 1s features/candles: 90 days;
- trade records and Run summaries: indefinite;
- automatic disk-pressure warning and entry pause before storage exhaustion.

Retention must be configurable and visible.

## 10.3 Trade capture window

For every candidate that reaches ARMED or execution:

- retain pre-event market window, initially 2–5 minutes;
- retain entire holding period;
- retain post-exit window, initially 30–60 seconds;
- retain feature and decision snapshots;
- retain book data needed for fill reconstruction.

## 10.4 Replay determinism

A replay should use:

- recorded events;
- recorded Run configuration;
- recorded strategy version;
- recorded fee/latency model;
- deterministic clock and RNG seed.

Expected result: the same recorded events and version reproduce the same decision and fill path, subject to explicitly versioned migrations.

## 10.5 Metrics

Per trade:

- gross/net PnL;
- fees;
- signal-to-arrival slippage;
- depth-walk slippage;
- stop slippage;
- R multiple;
- MAE/MFE;
- entry/exit latency;
- holding time;
- exit reason;
- ambiguity flags;
- data-health incidents.

Per Run:

- net return;
- peak and maximum drawdown;
- profit factor;
- expectancy;
- trade count;
- win/loss size distributions;
- cost burden;
- strategy/symbol/regime contribution;
- consecutive losses;
- downtime and gaps;
- BASE/STRESS divergence.

## 10.6 Statistical honesty

- Always display sample size with win rate.
- Distinguish in-sample, validation, out-of-sample and live-paper periods.
- Do not annualize short samples by default.
- Do not hide fees or excluded trades.
- Preserve rejected candidates for selection-bias analysis.
- Clearly mark assumptions versus observed values.

## 10.7 Export

Provide export to:

- CSV trade list;
- JSON Run summary;
- HTML performance report;
- compressed replay bundle;
- diagnostic logs.

No personal credentials exist in exports.
