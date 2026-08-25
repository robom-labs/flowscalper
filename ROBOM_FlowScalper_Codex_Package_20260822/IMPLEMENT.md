# IMPLEMENT.md — Autonomous Implementation Runbook

## Operating rule

Follow `PLANS.md` milestone by milestone. Do not stop after producing an architecture proposal. Implement, validate, repair, document, and continue until the current Wave exit gate is satisfied.

## At the start of every Wave

1. Re-read applicable sections of `AGENTS.md` and `docs/`.
2. Update the Wave plan with concrete files, interfaces and tests.
3. Verify official endpoint details that may have changed.
4. Identify independent work that may be delegated.
5. State the validation commands that will prove completion.

## During implementation

- Prefer a thin vertical slice before broad stubs.
- Keep exchange protocol handling behind adapters.
- Use an injectable clock, latency model and RNG seed.
- Preserve raw venue timestamps and local receive timestamps.
- Treat every numeric market-data input as untrusted.
- Use Decimal or integer ticks/steps for order and PnL calculations where precision matters.
- Never use float equality for prices or quantities.
- Make state transitions explicit and persisted.
- Make every trade decision explainable through reason codes.

## Blocker handling

If a public endpoint cannot be reached from the Codex environment:

- implement against official schemas and recorded fixtures;
- run offline tests;
- create a network smoke command for the user's machine;
- mark network validation NOT_RUN rather than blocking the entire project.

If Binance public endpoints are inaccessible on the user's network:

- allow Bybit public linear as a separately identified venue;
- create a new Run for the venue;
- never silently switch an open paper position between venues.

If the frontend build tool is unavailable:

- continue backend and fixture validation;
- install the documented stable toolchain when permitted;
- do not replace the UI with an untested mock and declare completion.

## At the end of every Wave

1. Run unit, integration and relevant e2e tests.
2. Run lint and type checks.
3. Review for safety-invariant violations.
4. Update docs and configuration examples.
5. Update `PLANS.md` progress.
6. Commit the Wave.
7. Continue to the next Wave if the gate passes.

## Final evidence

Create or update `FINAL_UPGRADE_EVIDENCE.md` containing:

- system summary;
- exact startup commands;
- test and build commands with actual results;
- network test status;
- screenshot paths;
- sample completed paper trade ID;
- proof of fee/slippage accounting;
- proof of restart recovery;
- proof that real orders are impossible;
- known limitations;
- Git commit and clean-tree status.

## Strategy League backend Wave

1. Keep the existing `PaperExecutionEngine`, `CandidatePlanner`, `RiskManager`,
   persistence and replay path. Do not create a second execution engine or database.
2. Evaluate Registry A-F from the same symbol snapshot and history prefix. E/F
   confirmation must use evaluator state and real event timestamps.
3. Offer ACTIVE candidates to the one-position main benchmark. Offer ACTIVE and
   SHADOW candidates independently to each strategy's BASE/STRESS three-symbol account.
4. Apply account-local sizing, cost profile, pending/open risk, notional, cooldown and
   loss locks. Apply system market-data, storage and recovery locks to every account.
5. Persist recovery schema v2 and keep a tested v1 single-position migration reader.
6. Expose `league_accounts` and `league_positions` through the existing dashboard output.
7. Validate with the four commands in `docs/19_STRATEGY_LEAGUE_SPEC_KO.md`. This Wave
   does not run or claim frontend, browser, network, soak or release validation.

## Strategy League UI and asynchronous control Wave

1. Preserve the A-F and 12-account backend and expose long Run mutations as bounded
   `ControlOperation` background tasks with `202`, deduplication, conflict, cancel and retry.
2. Use the WebSocket dashboard operation as the UI source of truth. Never show fixture data
   as LIVE or a failed connection as completed.
3. Keep the first screen beginner-readable. Put account detail in drawers and market detail
   in the advanced terminal without changing the underlying Strategy League entry rules.
4. Keep scanner order and dimensions stable. Use the installed Lightweight Charts 5.2.1
   instance once per selection and update ordinary candles and indicators incrementally.
5. Validate backend tests, frontend unit tests, static checks, build, desktop/tablet/mobile
   Playwright, a separate real local-browser review, safety scan and repository hygiene.
6. Record unresolved external checks as `NOT_RUN` or `BLOCKED`; do not infer soak, network,
   Release, GitHub push or Actions results from local tests.

## Compact market and position focus Wave

1. Keep the existing Registry, PAPER execution, risk, storage and replay engines. Add one read-only full-market explorer around public Binance and Upbit endpoints.
2. Use the five-group compact shell and fixed market rail/chart workspace. Default to 3-minute candles, MA10/MA20 and volume overlay.
3. Maintain wide 50 and deep 12 with dwell, protected symbols and bounded rotation; append every selected universe snapshot.
4. Publish strategy×symbol analytics only from completed ledger trades and withhold ranking below 30 samples.
5. Normalize main and BASE/STRESS positions into `focus_positions`. Auto focus only a newly observed actual `trade_id` fill and persist the user's focus lock.
6. Build trade-centered replay from stored public events. Bound frames at 50,000, preserve state transitions, hide future markers and use timestamp-based 0.5x–80x playback.
7. Run local static/unit/E2E checks, separate actual Chrome review, public network smoke and genuine 30-minute soak. Leave 6h, 24h and Release as `NOT_RUN` when not executed.

## Explicit start status and automatic safety recovery Wave

1. Expose a beginner-readable operation contract that separates market observation from new PAPER entry activity.
2. Show READY, asynchronous connection progress, RUNNING, manual pause, automatic safety wait, hard safety block and reconnect as distinct Korean states.
3. Never offer a misleading resume button while a recoverable safety lock is active. Keep the supervisor observing and automatically reopen PAPER entry only after every existing safety gate clears.
4. Cache the exact top 20 prices of each full local order book and fall back to a full recomputation whenever a cached price is removed.
5. Validate exact order-book equivalence, pause-cause contracts, frontend one-click behavior, actual browser controls and a measured public-market observation window.
6. Keep 6-hour and 24-hour claims as `NOT_RUN` unless their full wall-clock duration is actually completed.

## Strategy entry integrity and protection simulation Wave

1. Replay a stored public-market Run before changing thresholds and identify the exact gate that rejects every qualified signal.
2. Keep minimum final net reward-risk, fees, slippage and strategy signal thresholds unchanged. Align strategy plan geometry with the final REVERSION/TREND split-exit cost calculation.
3. Replace A-D fixed confirmation and pullback values with real event timestamp and same-symbol history-prefix calculations. Reject the first aligned update and reset immediately when alignment breaks.
4. Run every Registry strategy through LONG/SHORT and both TP1→TP2 and initial-stop outcomes. Require protection creation immediately after PAPER fill and exact accounting.
5. Replay the same stored public-market events twice after the fix and require identical checksum and counts. Count unique candidate IDs across main and League account audit duplication.
6. Restart the actual localhost service, click start once in the browser, observe CONNECTING→RUNNING and record lag plus permanent PAPER safety state.
7. Fix any adjacent user-visible state contradiction discovered during the browser pass, add a regression test and rebuild the served frontend.
8. Record profitability as unproven until sufficient completed samples exist. Keep six-hour, 24-hour and Release status as `NOT_RUN` unless actually executed.

## Position churn and independent statistics Wave

1. Inspect actual immutable main trades and management audits before changing any exit rule. Do not impose a fixed normal holding deadline.
2. Keep initial SL/TP and data/system safety exits active immediately. Apply the 10-second grace only to ordinary edge-decay exits.
3. Require at least two simultaneous adverse health reasons and 3,000ms event-time persistence for ordinary edge-decay and profit-protection exits.
4. Keep A/B ACTIVE, C/D/E/F SHADOW and LONG/SHORT enabled for all six by default. Do not promote experimental strategies to the shared benchmark merely to call them on.
5. Calculate strategy/profile and strategy/symbol statistics only from independent League trades. Keep shared benchmark trades in their separate history and equity curve.
6. Preserve exact Decimal and ledger payload values. Apply adaptive precision only to frontend rendering, including millisecond-based holding-time display.
7. Re-run all A-F LONG/SHORT TP/SL simulations, full backend/frontend/static/build/E2E safety checks and actual browser controls.
8. Start a new immutable PAPER Run after deploying the policy so later samples do not mix old and new exit policy within one Run. Do not delete old trades.
9. Record natural post-change fills and profitability as `NOT_OBSERVED` or `NOT_PROVEN` until actual evidence exists.

## Bounded active ledger and volume safety Wave

1. Inspect the active SQLite file, WAL, filesystem free space, table row counts and `dbstat` sizes before changing persistence.
2. Keep every execution audit, order, fill, trade, immutable plan and checksum. Do not treat rejection-only audits as portfolio mutations.
3. Persist complete recovery state only after an audit that changes pending entry, position, protection, fill or risk state. Persist only affected shadow-account history rows.
4. Keep all chart intervals in memory but persist only canonical 1-second and replay-focus 180-second candles in SQLite.
5. Check archive and ledger filesystems independently with the same fail-closed storage thresholds and expose both capacities in diagnostics.
6. Before moving a live ledger, stop the service and verify a closed copy with SHA-256, `PRAGMA quick_check`, foreign keys and invariant row counts. Keep the prior file recoverable.
7. Restart the actual service, verify the recovered Run and open PAPER state, exercise the browser, and measure ledger growth and public-data lag after multiple flushes.
8. Re-run static checks, backend/frontend tests, production build, browser E2E, security and repository hygiene. Keep 6-hour and 24-hour status `NOT_RUN` unless fully observed.

## Bounded LIVE processing and dashboard statistics Wave

1. Keep the 500ms per-symbol strategy contract and every strict signal, cost, TP, SL and risk gate unchanged.
2. Use the product requirement's 8–12 machine-health range and subscribe to 12 deep symbols while preserving the 50-symbol wide scan, protected positions and bounded rotation.
3. Recompute current-version strategy statistics only when a completed independent PAPER trade changes; reuse the immutable report between dashboard frames.
4. Project only the most recent 512 in-memory events into the live dashboard while retaining the full bounded 10,000-event runtime window for execution and diagnostics.
5. Measure actual dashboard response time, event lag, queue, reconnect, drop, gap, persistence fault and browser frame stability after restarting the served build.

## Executable market lag and strategy visibility Wave

1. Keep actual bid·ask order-book latency as the global PAPER entry-safety measurement and expose public trade and wide-scanner latency separately.
2. Archive a late aggregate trade with its stale reason but do not let it rewrite the current candle or strategy feature. Keep that symbol unhealthy until a fresh trade arrives.
3. Preserve every A~J signal, cost, risk, TP, SL and PAPER execution threshold. Do not manufacture a natural signal to prove the display.
4. Show all open PAPER positions in the market workspace and overlay the selected symbol's direction, strategy, profile, entry, TP1 and SL on its chart.
5. Show each strategy's current monitor state, last beginner-readable wait reasons and evaluated path count. Distinguish a normal strict-condition wait from safety wait, fault and OFF.
6. Test the supervisor and runtime stale-trade boundary, frontend position and strategy states, production build and desktop·tablet·mobile Chromium flows.
7. Restart the actual localhost service, click start once, inspect live positions and every strategy row, sample lag and health continuously, and record the exact evidence without claiming profitability or a 6-hour·24-hour soak.

## Runtime incident observability and closed-position clarity Wave

1. Keep bid·ask execution latency, public trade latency and wide scanner latency separate. Add incident start, recovery, duration and event-receive-gap diagnostics without changing the 1,500ms fail-closed threshold.
2. Timestamp every persistence flush maximum and every flush taking at least 2,000ms. Keep the persistence worker isolated and diagnose correlation before attributing a market stall to storage.
3. When a focused PAPER position closes inside the same LIVE Run, replace the entry notice with a short closed-review notice and then clear it. Never leave a closed trade looking open on the chart.
4. Exercise A~J in the actual browser and API. Treat a strict-condition rejection with evaluated paths and no account fault as normal waiting, not a broken strategy.
5. Inspect actual immutable shadow trades for holding time and exit reason. Preserve immediate STOP/TP and system safety exits; require the existing 10-second grace only for ordinary EDGE_DECAY.
6. Research each new microstructure hypothesis with stored `LIVE_PUBLIC` data, an earlier train group and later holdout group, conservative bid·ask and BASE/STRESS costs. Reject a candidate whose net result does not survive costs.
7. Replay a stored public-market Run through the same Registry, candidate, execution and accounting path. Compare checksum and counts only against results produced by the same strategy implementation revision.
8. Record short-run performance as an observation only. Keep strategy profitability, six-hour, 24-hour and Release ZIP as `NOT_PROVEN` or `NOT_RUN` unless the exact gate was completed.

## Nonblocking READY startup and PAPER account clarity Wave

1. Measure storage initialization, SQLite open, recovery lookup, runtime construction, portfolio construction and historical trade statistics separately before changing startup order.
2. Keep recovery lookup and checksum validation synchronous. Move only READY dashboard history statistics to a background worker and expose loading, ready, duration and completion time.
3. Read historical main and shadow trades through the existing query-only SQLite WAL connection so the background cache cannot hold the writer lock needed by a new Run or PAPER persistence.
4. Record Parquet, manifest and candle timing for the slowest persistence flush together with batch sizes. Timestamp the largest event receive gap and compare time, not just magnitude, before claiming causality.
5. Exercise a real service restart and click start immediately in the actual browser. Verify READY response, CONNECTING feedback, RUNNING persistence, cache completion and zero unplanned reconnect, sequence gap and persistence fault.
6. When shared main PAPER and independent strategy PAPER use the same symbol, strategy and BASE profile, label the account scope in the position list, selector, chart banner and plan rail.
7. Re-run backend, frontend, static, build, safety, security, repository hygiene and desktop, tablet and mobile browser checks. Record the broader non-gate test typing audit separately if it is not part of the repository mypy contract.
8. Keep strategy thresholds, costs, TP, SL, risk and the real-order/private-API/credential boundary unchanged. Do not treat a short runtime sample as a six-hour or 24-hour soak.
