# 12. Test Plan

## 12.1 Test pyramid

- unit tests for calculations and state transitions;
- property tests for precision/risk invariants;
- integration tests for adapters, event flow and persistence;
- deterministic replay tests;
- frontend component tests;
- Playwright end-to-end tests;
- optional network smoke tests separated from deterministic CI.

## 12.2 Market-data tests

Required cases:

- metadata pagination;
- active/inactive contract filtering;
- WebSocket subscribe/unsubscribe;
- snapshot then delta;
- out-of-order delta;
- duplicate delta;
- sequence gap;
- snapshot reset;
- stale ticker/book/trade;
- ping/pong and planned reconnect;
- host wall-clock step after venue calibration;
- planned rotation entry lock before provider prepare and automatic unlock after fresh depth;
- unplanned reconnect metadata/time recalibration, failed-prepare backoff and recovered error clearing;
- bounded multi-socket close and repeated actual public rotation;
- rate-limit/backoff behavior;
- venue endpoint migration/configuration;
- provider failover only through a new Run.

## 12.3 Order-book tests

- sorted bid/ask invariants;
- crossed-book rejection;
- level add/update/remove;
- checksum or update-ID continuity where applicable;
- multiple levels and price precision;
- resync after gap;
- bounded memory.

## 12.4 Feature tests

- mid/spread;
- weighted imbalance;
- microprice;
- OFI;
- trade imbalance;
- refill/cancel metrics;
- realized volatility;
- compression;
- micro-VWAP;
- robust z-score/percentile;
- warmup behavior;
- no NaN/inf propagation.

## 12.5 Strategy tests

For both long and short:

- valid positive setup;
- continuation rather than absorption rejection;
- fleeting wall rejection;
- spread rejection;
- shock rejection;
- stale-data rejection;
- no structural stop rejection;
- inadequate net target rejection;
- deterministic reason codes.

## 12.6 Execution tests

- zero/full/partial fill;
- multi-level weighted fill;
- price-cap cancellation;
- latency changes fill;
- long/short correct book side;
- TP executable-side rule;
- SL trigger and gap slippage;
- ambiguous TP/SL pessimistic result;
- fee and slippage accounting;
- quantity rounding;
- dust rejection;
- depth-cap rejection.

## 12.7 Position-management tests

- stay open beyond 120 seconds while thesis valid;
- early edge-decay exit;
- initial stop never widens;
- profit protection;
- stale emergency;
- data gap and same-venue recovery;
- trade cannot close twice;
- all contingent simulated orders finalized.

## 12.8 Risk tests

- risk budget exactness;
- max one position;
- daily/weekly/drawdown locks;
- consecutive-loss cooldowns;
- no martingale/pyramiding;
- active Run configuration immutability;
- BASE and STRESS accounting separation.

## 12.9 Recovery tests

- crash after candidate;
- crash after partial fill;
- crash after protection creation;
- crash during exit;
- database reopen and reconciliation;
- corrupted snapshot safety pause;
- disk-full entry lock.
- lifespan cancellation while a persistence worker is still flushing;
- macOS service mode selection for missing, open LIVE PAPER and finalized Run ledgers;
- actual LaunchAgent restart recovery without a browser click, followed by fresh-book unlock.

## 12.10 Real-trading prohibition tests

- forbidden private endpoint strings absent from runtime adapters;
- no credential settings/schema/UI fields;
- attempting a real mode raises;
- network mock asserts only public routes are called;
- UI contains no live-order control;
- package scan contains no withdrawal/transfer implementation.

## 12.11 Frontend tests

- permanent PAPER banner;
- data-source state honesty;
- scanner updates;
- chart renders entry/TP/SL;
- current position fields;
- rejection explanation;
- Run reset preservation confirmation;
- pause and paper close;
- responsive desktop widths;
- accessibility smoke checks.

## 12.12 Network smoke tests

Run separately and do not fail offline CI by default:

- DNS and REST metadata;
- public WebSocket handshake;
- receive book/ticker/trade;
- discover at least one eligible symbol;
- measure p50/p95 event latency;
- no credential headers/parameters.

Results: PASS, FAIL, or NOT_RUN with reason.

## 12.13 Performance tests

Initial targets on a reasonable desktop:

- 50 wide symbols and 10 deep symbols with no critical-lag interval left entry-enabled;
- UI update throttled independently from strategy event rate;
- changing from an old Run to READY or a fresh Run clears the old PAPER-entry toast and focused-position review;
- bounded queue memory;
- feature processing p95 within configured budget;
- automatic capacity reduction when overloaded.

Executable soak commands:

- `uv run python scripts/soak_live.py --duration-seconds 1800 --output evidence/WAVE07_SOAK_30M.json`;
- `scripts/soak_6h.command`;
- `scripts/soak_24h.command`.

The 30-minute run is the automated acceptance smoke. The 6-hour and 24-hour commands use the same assertions and must be reported `NOT_RUN` rather than inferred when wall-clock execution is unavailable.

The public-event lag threshold remains 1,500ms. Because an exchange or network can exceed it independently of local queue health, the soak must preserve the maximum and count of critical samples, prove zero fail-open samples, and finish either below the threshold or with both supervisor and runtime entry-locked. Queue overflow, dropped events, unbounded memory, or an unlocked critical-lag sample still fails the run.

## 12.14 Phase 02 control and UI regression

- Backend tests cover immediate `202`, same-action deduplication, different-action conflict,
  bounded history, ordered stages, retryable/blocked failures, cancellation cleanup, current
  and missing operations, dashboard/WebSocket output, the 18 League accounts, extended
  positions, split risk contracts and PAPER/auth invariants.
- Frontend tests cover bootstrap failure, HTTP timeout and typed error bodies, duplicate
  protection, cancel/retry, malformed WebSocket recovery, ten strategies and 20 account
  pairing, beginner copy, scanner stability, indicators without input mutation and chart
  instance/series update behavior.
- Deterministic Playwright covers the 45 accepted interactions across desktop, tablet and
  mobile. It records console errors, page errors and failed requests, requires 48px controls,
  verifies per-page overflow, scanner/chart size invariance, current-to-realtime and
  fullscreen return, and creates only new `phase02-*` screenshots.
- A separate local-browser pass repeats the user-visible home, operation, League drawer,
  positions, terminal indicators, fullscreen and responsive checks against port 8870.
- Public network smoke, 30-minute, 6-hour and 24-hour soaks and a Release ZIP remain separate
  and must be `NOT_RUN` unless executed during the same evidence pass.

## 12.15 Phase 03 validation

- Backend tests cover public role separation, candle validation/deduplication, deep rotation protection, append-only universe snapshots, 30-sample analytics, FocusPosition PAPER contract and future-marker-free deterministic replay.
- Frontend tests cover default MA10/MA20, chart instance/update behavior and timestamp-based 80x ReplayClock ordering.
- Playwright covers 1408×900, 820×1180 and 390×844 market screens, catalog switching, 3-minute defaults, indicator pane add/remove, strategy-symbol navigation, focus dimensions, mobile/tablet detail sheets and replay 80x state.
- `make network-smoke` writes `evidence/PHASE03_PUBLIC_MARKET_SMOKE.json` and must validate Binance/Upbit catalog counts, two Binance symbols, KRW-BTC candles, public WebSocket events and auth false.
- `make soak-30m` writes `evidence/PHASE03_SOAK_30M.json`. It must complete actual 1,800 seconds and observe at least one bounded rotation with drop/gap/fail-open all zero.

## 12.16 Phase 03 latency and actual-control regression

- Verify coalesced trades preserve side, quantity, notional and VWAP, including mixed-side timestamp ordering.
- Verify strategy history statistics are computed once per snapshot and shared by all strategy-direction evaluations.
- Verify market Parquet files are separated by Run and both old and new Run replay reads remain exact.
- Verify a persistence worker flushes threshold batches in a separate process while the event-loop heartbeat continues, and flushes a final sub-threshold batch on shutdown.
- Verify the 10,000 recent-event window and 2,000 plan-rejection window replace one oldest row per append instead of deleting a large prefix in the market event loop.
- Correlate actual Parquet flush duration with executable-path lag across multiple flushes and at least one planned 15-minute WebSocket rotation; a short unit test alone is insufficient.
- Verify performance summaries label current-Run account equity separately from current-strategy-version trade statistics on desktop, tablet and mobile.
- Verify DEMO clears LIVE-only lag, universe and selection state, and permanently renders `샘플 PAPER · LIVE 아님 · 실제 주문 0` at phone width.
- Verify a completed trade focus session contains PRE_ENTRY, OPEN and CLOSED, with no future marker and an exit ledger transition when no market event exists after exit.
- In the actual in-app browser, click navigation, strategy modes/directions, record filters, replay controls, analytics filters, safety controls, market search/source/symbol, all intervals, all indicators, fullscreen, drawer, focus sheets and responsive states. Write PASS/FAIL per control to `evidence/PHASE03_ACTUAL_UI_SIMULATION.json`.
- The integrated post-fix public run evidence is `evidence/PHASE03_INTEGRATED_LIVE_POSTFIX_180S.json`. Six-hour and 24-hour results remain `NOT_RUN` unless actually completed.

## 12.17 Phase 05 strategy entry and protection regression

- Parameterize all ten Registry strategies and LONG/SHORT through the runtime plan geometry and final executable-book cost gate. Require net reward-risk at least 1.20 without lowering fee or slippage assumptions.
- Parameterize all ten strategies, both directions and both TP1→TP2 and initial-stop outcomes. Require protection orders immediately after fill, exact remaining quantity, stop non-widening and fee/slippage reconciliation.
- Verify A/C REVERSION and B/D/E/F/G/H/I TREND exit styles use their documented minimum structural distances and split exits.
- Verify G/H/I symmetric signal gates, actual event-time persistence, reset, history-prefix robust z, top10 fair-price/depth-normalized OFI and three-second trailing-return calculations.
- Verify B/D pullback metrics are symmetric, use event time, require price reacceleration and ignore future history. Verify every temporal confirmation resets when alignment breaks.
- Replay the same stored public-market Run twice and require identical checksum, evaluated/qualified/candidate/trade counts and PAPER/auth invariants.
- Count replay candidates by unique candidate ID across main and League BASE/STRESS audit duplication.
- In the actual browser, require a single start click to show CONNECTING then RUNNING, a numeric lag P95 and permanent `PAPER · 실제 주문 0`.
- A short replay or deterministic test proves code-path integrity, not profitability. Six-hour and 24-hour soaks remain `NOT_RUN` unless their full duration is observed.

## 12.18 Strategy Governor regression

- Verify lifecycle-mode consistency, CAS conflicts, user manual lock and recovery of every saved revision.
- Verify rollback creates a new revision and remains available after process restart.
- Verify one poor evaluation and fewer than 30 samples cannot performance-quarantine a strategy.
- Verify full and recent OOS degradation require two consecutive evaluations, while data leakage, ledger contamination and abnormal PAPER loops quarantine immediately.
- Verify missing DSR/PBO/OOS lower bound/robustness blocks promotion.
- Verify champion demotion and challenger promotion are prevalidated and applied atomically, with at most one champion replacement per evaluation.
- Verify `AUTO_GOVERNOR` setting evidence and incident audit are checksum-protected in SQLite.
- Verify the browser shows lifecycle, last evaluation, exact reason, remaining sample/time, manual lock, change history and rollback confirmation.

## 12.19 Canonical candle and multi-timeframe research regression

- Parameterize every canonical aggregation boundary used by 1m, 3m, 5m, 15m, 30m, 1h and 4h. Verify OHLCV, quote volume, trade count and taker buy/sell fields.
- Feed duplicate event IDs and out-of-order symbol events. Require deterministic ignore counters and unchanged completed candles.
- Verify feature snapshots use completed candles at or before `as_of_ts_ms` only. Reject a future higher-timeframe candle and preserve exact session VWAP ordering.
- Verify ORIGINAL and MECHANICAL_MIRROR have identical signal timestamps and information-set IDs, opposite sides, symmetric stop/TP geometry and paired admission.
- Verify HYPOTHESIS_REVERSE uses separately coded conditions rather than an unconditional side flip.
- Verify LONG uses ask entry and bid exit, SHORT uses bid entry and ask exit, stop is conservative under same-event ambiguity, and TP1/TP2 remain one completed outcome.
- Require the preregistered grid to contain 180 keys and the selection correction to count 120 promotable hypotheses even if some have zero signals.
- Run Train·Validation·OOS on the complete preregistered archive. Verify dataset, config, code and result hashes, horizon-specific purge·embargo, PBO, DSR, bootstrap and no-trade baseline.
- Re-run the same full research command and require identical deterministic content except generation/completion timestamps. If not run twice, report determinism as `NOT_RUN` rather than PASS.

## 12.20 Dynamic registry and history scope regression

- Add a synthetic strategy to the API fixture and require strategy/account totals and row order to follow the payload without changing production constants.
- Verify main, Strategy League and combined history across current/all Run, current/all strategy version, BASE/STRESS and LIVE_PUBLIC/OFFLINE sample type.
- Verify every public timeframe is accepted end-to-end and unsupported values fail explicitly.
- Verify replay discovery keeps an event-only Run with no completed trade.

## 12.21 Current and peak process-memory regression

- Require the current-memory source to be `CURRENT_RSS_LIBPROC`, `CURRENT_RSS_PROCFS` or `CURRENT_WORKING_SET` on a supported platform.
- Require the separately reported peak RSS to be greater than or equal to current RSS and to use an explicit `PEAK_` source.
- Mock a lower current RSS and a higher peak RSS. Verify the current field cannot silently reuse or relabel the peak counter.
- Verify the advanced Korean system view displays both labels and values independently.
- Verify soak `memory_growth_mb` is calculated from current RSS while `peak_memory_growth_mb` remains a separate high-water diagnostic.
- Compare the restarted service current RSS with the operating-system process RSS. Record tolerance and source in machine-readable evidence.
- A unit test or short sample does not prove six-hour or 24-hour memory stability. Those gates remain `NOT_RUN` until their exact wall-clock duration completes.
