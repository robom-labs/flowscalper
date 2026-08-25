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

## 10.8 Phase 03 trade focus and strategy-symbol analytics

- `focus_positions` normalizes actual entry, initial/current stop, TP1/TP2, quantities, planned loss, fee/slippage, net PnL, account equity, stage, data health and permanent PAPER flags.
- Strategy×symbol reports group completed ledger trades by strategy, profile and symbol. Ranking is withheld below 30 samples and always shows costs and sample status.
- A replay focus request uses at least 20 minutes pre-roll and 5 minutes post-roll where stored events exist. Frames are capped at 50,000; state changes and first/last frames are preserved while market-only frames may be downsampled.
- Replay markers are cursor-bounded. Entry, partial fill and exit information cannot appear before its event timestamp.
- `ReplayClock` uses `performance.now`, frame timestamp deltas and allowed speeds 0.5/1/2/5/10/20/40/80. Speed changes presentation only.

## 10.9 Phase 03 latency and replay hardening

- New market-event files partition by `venue/run/date/symbol/hour/event_type`; the Run dimension prevents an active Run from repeatedly scanning or appending into another Run's dense partition.
- The live persistence worker writes at 2,000-event thresholds, records flush count/last/max milliseconds and flushes a final sub-threshold batch on shutdown.
- Binance trade coalescing is exact within symbol, aggressor side and 250ms bucket. Quantity and notional are summed, price is VWAP and source/output counts remain observable.
- Focus replay inserts deterministic `PAPER_LEDGER_TRANSITION` frames at the stored entry and exit timestamps. These frames originate from the immutable PAPER trade/fill ledger, never from invented market prices, and guarantee an honest CLOSED review even when post-roll market events are absent.

## 10.10 Current strategy-version performance scope

- The default strategy, profile and strategy×symbol reports include only independent `LIVE_PUBLIC` shadow trades whose full `strategy_version` equals the current implementation revision.
- Prior-version trades remain immutable and queryable. The current UI and API disclose how many prior-version samples were excluded instead of deleting or silently mixing them.
- Legacy shadow payloads are checksum-verified first and may be enriched in memory from their immutable Run `config_json` and `config_hash`; the stored payload and checksum are never rewritten.
- New completed shadow trades persist both the Run `config_hash` and full `strategy_version`.
- `DEMO_FIXTURE` and `REPLAY` samples never enter current LIVE_PUBLIC win rate, expectancy, Profit Factor, cost, drawdown or holding-time statistics.
- See `docs/adr/ADR-017-current-strategy-version-performance-scope.md` for the decision and regression boundaries.

## 10.11 Large replay isolation and focus cache

- While LIVE public observation is active, full Run replay, timeline reads and trade-focus replay share one process lock and execute in a low-priority child process with independent SQLite and Parquet readers.
- A `nice(19)` child process applies a one-core 10% cooperative CPU budget to each checkpoint interval across archive decoding, strategy ingestion, event sorting, duplicate checks and streaming SHA-256. The interval calculation prevents old high-load work from creating unbounded later sleep debt. Replay completion time is secondary to uninterrupted LIVE ingestion.
- Replay checksum schema 3 length-prefixes each normalized event and decision-path item into separate streaming SHA-256 digests. The final canonical material contains only those digests, counts, config, version and final state, so it does not duplicate the full event list in memory.
- New archive batches expose `venue_ts_ms`, `symbol`, `event_type` and `batch_checksum` columns. Time-bounded UI reads select relevant manifests, verify the complete selected batch checksum before filtering and decode only matching rows; truncated batches fail even when the remaining filtered rows look valid. Legacy batches keep the full checksum-compatible fallback.
- Trade-focus reads are bounded to the configured pre/post trade window. Completed sessions are zlib-compressed in schema v7 `replay_focus_cache` and verified by SHA-256 before reuse.
- Full Run replay, timeline and trade-focus requests share one lock. A concurrent request receives HTTP 409 `REPLAY_BUSY` instead of waiting behind a long replay and appearing frozen.
- The default LIVE history view includes only main PAPER trades whose `sample_type` is `LIVE_PUBLIC` and whose strategy implementation version equals the current build. Older immutable trades remain stored and are reported as excluded.
- LIVE event lag uses a public venue-time offset estimated from the minimum-RTT sample. The process never changes the operating-system clock and never adds credentials to the public time request.
- See `docs/adr/ADR-018-replay-cpu-budget-focus-cache-and-venue-clock.md` for the decision and failure boundaries.

## 10.12 Bounded active ledger persistence

- The active SQLite ledger and the immutable public-market Parquet archive can live on different volumes. Entry safety checks both volumes and fails closed when either free-byte or free-ratio threshold is breached.
- Every execution audit row remains append-only. Rejection-only audit batches do not duplicate the complete recovery payload because they do not mutate an order, pending entry, position, protection, fill or account risk state.
- A recovery snapshot is written after a state-mutating audit. Strategy-account history writes only the shadow accounts named by those mutations instead of all strategy/profile accounts.
- The in-memory `CandleBuilder` continues to provide every supported chart interval. SQLite persists canonical 1-second candles and the 180-second replay focus interval only; the other chart intervals are deterministic derivatives and are not duplicated permanently.
- On macOS, an external APFS project defaults its active ledger to the same mounted APFS volume under `05_RUNTIME/ROBOM_FlowScalper/active-ledger`. The Python runtime, bytecode cache and service logs remain in Application Support. An explicit `ROBOM_ACTIVE_LEDGER_DIR` or `ROBOM_DB_PATH` still overrides the default.
- Existing ledgers are never silently deleted or rewritten. A migration must stop the service, copy and checksum the closed SQLite files, run `PRAGMA quick_check` and foreign-key checks, retain a recoverable pre-migration copy, then restart and verify the recovered Run.
- See `docs/adr/ADR-024-bounded-active-ledger-and-volume-safety.md`.

## 10.13 늦은 공개 체결의 저장과 실행 분리

- 500ms보다 늦게 도착한 공개 aggregate trade도 원본성 있는 시장 사건이므로 immutable archive에는 보존한다.
- 같은 이벤트를 현재 candle·체결흐름·전략 피처에 뒤늦게 적용하지 않는다. 해당 종목은 신선한 trade가 도착할 때까지 전략입력 `data_healthy=false`를 유지한다.
- replay는 저장 당시의 stale 표식과 reason flag를 보존해 LIVE와 동일한 유효성 경계를 재현한다.
- See `docs/adr/ADR-026-executable-book-trade-lag-and-strategy-visibility.md`.

## 10.14 Research manifest and chronological intraday reports

- Every research output binds the exact code commit, configuration hash, fixed seed, dataset Run IDs, event counts, time ranges and per-Run SHA-256 checksums before recording the final result checksum.
- Train, Validation and OOS Run IDs are fixed before execution. Horizon-specific maximum holding time is used as purge and embargo around chronological boundaries.
- Partial Run or maximum-event diagnostics are labeled `PARTIAL_DIAGNOSTIC_NOT_EVIDENCE`; only the complete preregistered archive may be considered for OOS assessment.
- The intraday report retains all 180 preregistered hypotheses, including no-signal rows. The 60 mechanical mirrors are baselines, while 120 ORIGINAL and separate reverse hypotheses count toward multiple-testing correction.
- JSON is the machine-readable source. HTML is a human-readable projection of the same result. A hash or deterministic replay PASS proves reproducibility, not profitability.
- Research outputs never modify current Registry settings, PAPER accounts or immutable execution ledgers.
