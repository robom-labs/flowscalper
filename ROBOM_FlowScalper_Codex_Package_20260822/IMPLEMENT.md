# IMPLEMENT.md — Autonomous Implementation Runbook

## Operating rule

Follow `PLANS.md` milestone by milestone. Do not stop after producing an architecture proposal. Implement, validate, repair, document, and continue until the current Wave exit gate is satisfied.

## Strategy-profitability operating objective

1. Treat the user's long-term money-making objective as a research-selection objective, not as permission to add real trading. Keep this program credential-free, public-data PAPER-only and structurally unable to place an exchange order.
2. Repair runtime, ledger, replay and UI truthfulness first because unstable or mixed evidence cannot qualify a strategy.
3. For each strategy iteration, record Run, strategy version, account/profile, BASE/STRESS cost basis and every active history/replay filter before interpreting results.
4. Predeclare the hypothesis, parameters, chronological split, fee/slippage model and promotion gates. Evaluate the unchanged candidate once across train, validation, purged OOS and independent future data where available.
5. Evaluate win rate together with its confidence interval, cost-adjusted expectancy, Profit Factor, payoff, maximum drawdown, downside risk, holding time, symbol/regime concentration, bootstrap lower bound, DSR and PBO. Never choose a strategy from win rate alone.
6. Preserve the existing minimum gates in ADR-038 and `docs/20_RESEARCH_FOUNDATIONS_AND_ADAPTATION.md`. Below 30 current-version natural `LIVE_PUBLIC` samples, keep ranking and profitability `NOT_PROVEN`; do not call a backtest or replay result a live profit proof.
7. Promote only a preregistered candidate that passes every BASE/STRESS, OOS, robustness and natural-sample gate. Keep failures `SHADOW`, `RETIRED` or unregistered and move to the next predefined hypothesis instead of tuning the failed sample repeatedly.
8. Record entry, TP1, TP2, SL and actual-close timestamps and durations independently of promotion. Exercise one concrete PAPER trade in replay and confirm the same milestones, costs and outcome on the actual chart.
9. Do not hide an empty champion state. If no strategy qualifies, report why each candidate failed, retain actual orders at 0 and continue collecting independent PAPER evidence.
10. A future real-money system is a separate product phase requiring explicit user approval, credential/security design, legal and venue review, capital/risk limits, kill switches and new acceptance evidence. It is not implemented by this runbook.

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

## Atomic persistence ledger commit Wave

1. Preserve WAL, `synchronous=FULL`, checksum, immutable tables and storage-pressure fail-closed behavior.
2. Write and fsync every checksum-addressed Parquet group in the existing isolated process before touching its SQLite manifest.
3. Commit archive manifests, per-symbol event statistics and the matching candle rows in one `BEGIN IMMEDIATE` transaction per persistence batch.
4. Roll back the complete SQLite batch on a manifest or candle checksum conflict. Restore both in-memory buffers and lock new PAPER entries on any worker failure.
5. Replace the obsolete separate manifest and candle duration diagnostics with one beginner-readable atomic-ledger commit duration.
6. Prove one begin and one commit, atomic rollback and worker buffer recovery with tests before restarting the real service.
7. Restart only with no open PAPER position, click start in the actual browser and observe multiple natural FULL flushes together with lag, event gaps, reconnects, drops and faults.
8. Inspect all A~J monitor rows, LONG and SHORT controls, evaluated paths, natural open PAPER plans and holding times without lowering any strategy threshold.
9. Re-run backend, frontend, static, production build, safety, security, repository hygiene and desktop, tablet and mobile browser validation. Keep profitability, six-hour, 24-hour and Release ZIP status separate.

## Separated passive WAL checkpoint Wave

1. Continue the same actual Run beyond the first short atomic-commit sample and correct earlier evidence if the latency result is not sustained.
2. Compare the WAL size and page count with SQLite's documented 1,000-page automatic checkpoint boundary before changing durability settings.
3. Keep WAL and `synchronous=FULL`, disable commit-thread auto-checkpoint and run PASSIVE checkpoints in a separate process every eight persistence flushes.
4. Retry a partial checkpoint without blocking readers or writers. Fail closed only if checkpoint failure or incompleteness lets the WAL reach the bounded 64MiB safety threshold.
5. Expose beginner-readable auto-checkpoint, attempt, partial, duration, frame and error diagnostics and remove no existing incident measurements.
6. Exercise real checkpoint cycles together with event gaps, critical incidents, reconnects, sequence gaps, drops and persistence faults. Do not call the Wave complete if FULL commits still correlate with market stalls.
7. Preserve all strategy, cost, TP, SL, risk and PAPER-only boundaries and keep profitability and multi-hour soak claims separate.

## Out-of-process durable market persistence Wave

1. Treat residual slow FULL commits after WAL separation as a new measured problem rather than rewriting the earlier checkpoint result.
2. Move one complete market batch's Parquet serialization, compression, fsync and atomic SQLite manifest, statistics and candle commit into the same background I/O process.
3. Use an independent SQLite connection with WAL, `synchronous=FULL`, foreign keys, auto-checkpoint disabled and a bounded writer wait. Keep one `BEGIN IMMEDIATE` and one COMMIT.
4. Restore both in-memory batches and fail closed on any process, Parquet or SQLite error. Preserve checksum conflicts and closed-Run rejection.
5. Prove the separate connection is visible from the live ledger and inject a process fault to verify rollback, buffer restoration and zero silent drop.
6. Restart with no open PAPER position, click start once and verify a fresh 1,000 USDT Run with trade, PnL and fees at zero.
7. Observe at least 160,000 actual public-market events, including intentionally occurring slow FULL commits and checkpoints. Require the market path to continue without hiding any safety incident.
8. Inspect an actual chart position and plan with strategy, direction, account scope, entry, TP1, TP2, SL, quantity and maximum loss. Inspect all A~J rows and strict wait reasons.
9. Re-run full backend, frontend, static, production build, PAPER safety, security, repository hygiene and desktop, tablet and mobile browser validation, then push the exact evidence to GitHub main.

## Cost-adjusted strategy retirement and supervisor headroom Wave

1. Read current-version independent `LIVE_PUBLIC` BASE/STRESS trades before changing any strategy and report wins, losses, expectancy, Profit Factor, costs and sample size together.
2. Re-evaluate the affected hypothesis on chronological stored-public-market train and later holdout groups. Use only prefix features, actual reconstructed bid·ask and conservative BASE/STRESS costs.
3. Pre-register only a small strict and cost-aware revision set. Do not lower thresholds, search a large parameter grid or promote a no-signal candidate after seeing holdout results.
4. Demote or retire a strategy whose directional movement does not survive costs. Preserve immutable old trades, independent accounts, LONG/SHORT controls and the ability to re-enable it deliberately.
5. Keep only evidence-supported strategies eligible for shared main PAPER. A silent SHADOW or OFF strategy must not be presented as broken, and no short sample may be presented as profitable.
6. Apply every raw depth delta to the local order book before rate limiting completed snapshots. Preserve the first and last sequence identifiers while bounding downstream depth and trade delivery to a rate the consumer can sustain.
7. Re-run strategy, supervisor, storage/replay, frontend, browser, static, build, PAPER safety, security and repository-hygiene checks.
8. Restart only after confirming zero open main and League PAPER positions. Start a fresh 1,000 USDT Run once and observe queue, drop, executable-book/trade/display p95, safety lock, reconnect, gap, persistence fault, CPU and memory continuously.
9. Exercise the actual browser strategy list and a natural open PAPER position without manufacturing a signal. Record TP/SL, cost and account scope, and keep profitability plus 6-hour/24-hour soak as `NOT_PROVEN` or `NOT_RUN` until the exact gate is completed.

## Execution chronology and full runtime strategy screening Wave

1. Persist candidate selection, entry request, entry fill, protection request, management decision and exit fill at their actual decision, book or fill event-time. Do not reuse the immutable signal time for later lifecycle events.
2. Prove persisted audit chronology with deterministic entry, TP1, TP2 and management-exit tests, then compare one natural `LIVE_PUBLIC` trade's audit delta with its immutable ledger holding time.
3. Run the actual A~J `StrategyRegistry` and `StrategySignalEvaluator` on chronological stored public-market train and later holdout groups. Use prefix-only features, reconstructed top-of-book ask/bid, a fixed disclosed horizon and unchanged BASE/STRESS costs.
4. Treat the fixed-horizon result as a screening test, not full exit-policy profitability proof. Report gross and cost-adjusted sample size, wins, expectancy and Profit Factor together.
5. Move a repeatedly failing strategy to OFF without deleting its source, immutable trades, independent accounts, LONG/SHORT controls or deliberate user reactivation path. Do not lower thresholds to manufacture a replacement.
6. Start a fresh 1,000 USDT PAPER Run from the exact implementation commit, verify 7 observed and 3 OFF strategies, and distinguish explicit browser configuration POSTs from automatic runtime changes.
7. Let already-open PAPER positions finish through their existing TP/SL and management path. Turning a strategy OFF prevents new evaluation but does not erase or force-close existing research records.
8. Re-run backend, frontend, static, build, safety, security, repository hygiene and desktop, tablet and mobile browser tests. Preserve actual orders, private API, credentials, secrets and wallets at zero.
9. Record profitability as `NOT_PROVEN` and multi-hour soak as `NOT_RUN` until their exact sample and duration gates are completed.

## Later natural-sample retirement and nonblocking analytics Wave

1. Compare each new current-revision natural `LIVE_PUBLIC` BASE/STRESS trade with the earlier chronological train and holdout screen. Do not rank fewer than 30 samples or claim profitability.
2. Retire a hypothesis from default evaluation when every available cost-adjusted screen remains negative and continuing automatic exposure lacks evidence. Preserve source, immutable trades, 20 independent accounts, LONG/SHORT controls and deliberate reactivation.
3. Keep B as the only shared-capital ACTIVE strategy. Do not promote another strategy merely to keep the active count high.
4. Serve LIVE strategy and strategy-symbol analytics from the startup checksum-verified current-version cache merged by trade ID with current-process completed PAPER trades. Keep Replay and non-LIVE ledger reads unchanged.
5. Test that LIVE analytics cannot rescan the active ledger, that cached and persisted metrics match, and that current/prior strategy-version sample counts remain separated.
6. Bound any full 13-Run research rerun. If it exceeds the declared runtime budget, stop it and record `NOT_COMPLETED` rather than treating partial output as evidence.
7. Restart only with zero main and League positions, create a Fresh 1,000 USDT Run from the implementation commit and verify 6 monitored, 4 OFF, real orders 0 and auth false in the actual browser.
8. Measure repeated strategy and strategy-symbol API latency during active persistence, inspect browser console and run desktop, tablet and mobile Playwright.
9. Record any resource-contention critical incident with exact duration. A recovered incident is not a zero-incident result.
10. Before a READY service creates a new Run, preserve-finalize superseded flat Run rows. If the latest checksum-verified recovery snapshot contains any PAPER pending entry or position, block the new Run rather than orphaning exposure.

## Full audit, control truth and preregistered intraday research Wave

1. Treat repeated start as an idempotent request and reserve explicit new-Run creation for a separate confirmed operation. Persist idempotency key, expected revision, actor, reason and audit result.
2. Preserve user pause intent independently from automatic data or storage locks. Recovery may clear an automatic lock only after fresh valid input; it must not override a manual pause.
3. Use a single timeframe registry through backend history, dashboard and React controls. Implement every public interval from 1m through 4h or expose none of it.
4. Make history and analytics scope explicit for main/League, Run, BASE/STRESS, strategy version and sample type. Do not mix current Run equity with all-Run research metrics.
5. Add reproducible research manifests, chronological splits, walk-forward evidence, purge·embargo, multi-horizon outcomes, PBO, DSR and deterministic bootstrap. A partial result is diagnostic only.
6. Keep Strategy Governor lifecycle, champion/challenger, technical/performance quarantine, manual lock, CAS, rollback and audit separate from execution mode. Never mutate strategy source or thresholds at runtime.
7. Build canonical completed candles and a research-only multi-timeframe engine. Compare ORIGINAL, paired MECHANICAL_MIRROR and separately conditioned HYPOTHESIS_REVERSE using actual bid/ask and unchanged BASE/STRESS costs.
8. Count no-signal preregistered hypotheses in multiple-testing correction. Do not select or report only rows that traded.
9. Derive strategy and independent BASE/STRESS account totals from Registry data. Preserve historical accounts and trades when a strategy retires.
10. Run the existing A~J screening and the new full archive intraday study from the implementation commit. A research candidate cannot enter Registry without a new strategy ID and later SHADOW approval.
11. Re-run backend, frontend, lint, typecheck, build, E2E, public network, security and repository hygiene. Verify the actual browser at desktop, tablet and phone sizes.
12. Deploy only after every main and League PAPER position is flat. Verify the active service after atomic replacement and preserve actual orders, auth, private API, API keys, secrets, wallets and runtime AI at zero.
13. Complete a fresh 30-minute observation after deployment. Mark 6-hour and 24-hour checks `NOT_RUN` unless their full wall-clock duration is actually observed.
14. Publish each runtime strategy's horizon, expected holding range, signal half-life, required inputs, exit model, maximum safety hold and cost-model version without changing its entry threshold.
15. Shield the final persistence worker during ASGI shutdown. The macOS launcher may recover only the latest non-finalized LIVE/DEMO PAPER intent from a read-only ledger query; all missing or invalid state falls back to READY.

## Observable replay and bounded timeline Wave

1. Reproduce history and replay visibility from the actual 8870 browser before changing storage or strategy settings.
2. Compare visible zero rows with the current-version main and independent Strategy League ledger scopes. Preserve immutable prior-version rows.
3. Replace synchronous full replay requests with an explicit background operation, ordered transitions, idempotency, conflict, timeout, cancellation and shutdown cleanup.
4. Keep the isolated `nice(19)` child and 5% cooperative CPU budget. Cancellation must terminate its child work and must not pause LIVE public observation.
5. Load Run metadata and recent candles before historical replay results. Reattach to an active operation after refresh.
6. Bound interactive event and candle reads while leaving the full strategy replay input unchanged. Merge active SQLite and archive rows by the canonical event key.
7. Exercise current history, a small completed replay and a cancellable large replay in the actual desktop, tablet and phone browser. Check console output.
8. Re-run backend, frontend, fixture, Playwright, lint, typecheck, build, PAPER safety, security and repository hygiene.
9. Record current natural sample counts, costs and holding times without lowering entry, exit, cost or governor gates. Keep profitability `NOT_PROVEN` below its preregistered evidence gate.
10. Keep the active-ledger full check, six-hour soak, 24-hour soak and Release ZIP as `NOT_RUN` unless each exact operation is completed.

## Cost-aware hourly trend and truthful trade-focus Wave

1. Reproduce the empty trade-focus screen from an actual immutable `LIVE_PUBLIC` trade before changing strategy thresholds or ledger scope.
2. Build the complete focus session from the immutable PAPER ledger and checksum-verified public market archive before attempting any optional cache write.
3. Treat only SQLite `locked` and `busy` during `replay_focus_cache` persistence as a best-effort cache miss. Propagate every other integrity, schema, checksum and serialization failure.
4. Display an explicit focus failure and retry action instead of rendering a blank chart that looks like missing trade data.
5. Audit current-version natural BASE/STRESS samples, exit reasons and holding times. Retire repeated cost-adjusted failures without deleting source, accounts or immutable history and without lowering entry criteria.
6. Research slower public-market hypotheses on completed candles with fixed BASE/STRESS costs, chronological partitions, bootstrap, DSR and PBO. Preserve failed candidates and no-signal rows.
7. Add a new strategy ID only as SHADOW when its hypothesis and runtime contract are explicit. Do not describe a positive diagnostic slice as profitability.
8. For the hourly strategy use completed 1h candles, new-bar freshness, actual bid/ask, fixed TP1·TP2·SL, quantity, cost, maximum loss and descriptor-specific maximum holding time.
9. Upgrade recovery only through strict additive Registry extension. Preload persisted order/trade IDs and fail closed on partial profile loss or conflicting state.
10. Exercise actual history, trade focus, entry and actual exit cursors, strategy rows, retired controls and K details in the browser. Capture screenshots from the real 8870 service.
11. Run backend, frontend, E2E, lint, typecheck, build, PAPER safety, security, repository hygiene and active-ledger integrity checks. Record exact PASS, NOT_PROVEN and NOT_RUN boundaries.
12. Keep actual orders, private API, credentials, secrets, wallets and runtime AI at zero. Push the reviewed implementation and evidence to GitHub main and confirm Actions before completion.

## Strategy survival, outcome timing and visible history Wave

1. Start from the immutable current Run history. Separate missing rows caused by Run, account and strategy-version filters from genuinely absent trades before changing any strategy rule.
2. Aggregate current natural `LIVE_PUBLIC` BASE/STRESS samples by strategy, exit reason, cost, holding time and account. Do not rank or promote sparse samples and do not convert a high gross-win rate into a cost-adjusted claim.
3. Freeze every research threshold before downloading or opening the evaluation period. Preserve dataset, config and result hashes for rejected candidates.
4. Require BASE and STRESS expectancy, Profit Factor, chronological OOS, bootstrap lower bound, DSR/PBO, robustness and natural sample gates before a strategy can become a shared-account champion.
5. Keep zero default ACTIVE strategies when none passes. Preserve B/C/F/G/I/J as independent SHADOW research accounts and A/D/E/H/K as immutable-history RETIRED accounts.
6. Run the Strategy Governor every 15 minutes, but count only a newly completed natural sample as another degradation evaluation. Allow automatic quarantine or demotion, never evidence-free promotion.
7. Persist first TP1, first TP2 and actual STOP timestamps and elapsed durations through the PAPER trade model, restart recovery, schema, history API and strategy analytics. Never relabel EDGE_DECAY or other exits as STOP.
8. Show all accounts and prior versions by default in history, while keeping performance analytics current-version-only. Persisted ledger rows win when recovered in-memory rows share the same trade ID.
9. Bound focused replay idle gaps so a real Play click visibly advances within one second at the default speed, without changing source timestamps, frame order or reconciliation.
10. Exercise the actual browser history filters, detail drawer, trade replay Play, pause/complete, first, next and end controls. Check console warnings and capture screenshots.
11. Run backend, frontend, fixture, Playwright, lint, typecheck, build, PAPER safety, security and repository hygiene. Record the short LIVE observation separately from six-hour and 24-hour soak evidence.
12. Keep actual orders, private API, credentials, secrets, wallets and profitability claims at zero. Push implementation and final evidence to GitHub main and confirm Actions before completion.

## Current and peak process-memory truth Wave

1. Compare the dashboard process-memory number with the operating-system current RSS before changing telemetry. Record the exact Run, process and observation time.
2. Never use a lifetime peak counter as current usage. Expose current resident memory and lifetime peak resident memory as separate values with explicit source labels.
3. Use platform-native current RSS on macOS, Linux and Windows. If the native measurement fails, label the peak fallback explicitly instead of silently presenting it as current.
4. Calculate soak memory growth from current RSS. Preserve peak growth only as a separate diagnostic high-water mark.
5. Keep strategy thresholds, PAPER entry and exit rules, Registry state, ledger rows, actual-order safety and current Run state unchanged.
6. Add backend regression tests for current-versus-peak semantics and frontend tests for the beginner-readable Korean advanced diagnostics labels.
7. Preserve the running service until a useful long-run boundary is recorded when it is safe to do so. Restart only when all main and Strategy League PAPER positions are flat.
8. After restart, compare API current RSS with the operating-system RSS, verify peak is not lower than current, and exercise the actual advanced diagnostics screen in the browser.
9. Re-run backend, frontend, fixture, Playwright, lint, typecheck, build, PAPER safety, security and repository hygiene. Record six-hour and 24-hour stability separately and never infer them from a short sample.
10. Update machine-readable evidence and `FINAL_UPGRADE_EVIDENCE.md`, push the reviewed implementation to GitHub main and confirm Actions before calling the Wave complete.

## Closed cross-device large-ledger integrity Wave

1. Never run full `quick_check` or `foreign_key_check` on the active writer after a reproduced queue, drop or lag incident. Preserve every failed attempt with its exact status.
2. Bound the Online Backup API by total duration and no-progress duration. Treat completion under continuous external writes as unproven and remove partial copies after abort.
3. Start maintenance only from LIVE·PAPER·RUNNING with zero main and Strategy League positions, safe storage, bounded queue and lag, and actual orders·auth false.
4. Give LaunchAgent at least 60 seconds to finish the shielded persistence worker. Require source process handles 0, WAL busy 0 and WAL size 0 before snapshot creation.
5. Use macOS `clonefile(2)` directly on the closed same-device ledger and do not allow a normal-copy fallback that would silently extend downtime.
6. Restart the service immediately after clone creation. Require the same Run, a new process, LIVE·PAPER·RUNNING, zero positions and actual orders·auth false before long work continues.
7. Transfer the closed clone in bounded chunks to an explicitly different device. Require exact byte count and matching source·target SHA-256 before reading the verification copy.
8. Run full `quick_check` and `foreign_key_check` only on the read-only immutable different-device copy. Monitor LIVE independently through transfer, hashing and checks.
9. Fail closed on queue, executable lag, event stall, unplanned reconnect, gap, resync, drop, persistence fault, buffer drop, critical incident, position or PAPER-safety violation. Allow only a bounded planned-rotation transition and at most two consecutive probe errors.
10. Remove successful temporary copies, re-run backend, frontend, fixture, Playwright, lint, typecheck, build, PAPER safety, security and repository hygiene, and preserve 6-hour·24-hour·profitability·Release gates separately.
11. Update ADR, acceptance criteria, machine-readable evidence and `FINAL_UPGRADE_EVIDENCE.md`, then push the reviewed implementation to GitHub main and confirm Actions.

## Non-invasive running-service soak and mobile touch-target Wave

1. Observe the installed 8870 PAPER service through its existing dashboard only. Do not create another public-market connection, Run, runtime, replay process or SQLite writer.
2. Expose cumulative event, strategy-evaluation and qualified-signal counters so a quiet strategy can be distinguished from a stalled evaluation path without lowering any entry threshold.
3. Derive the expected independent account shape from the current Registry. Require exactly one BASE and one STRESS account per strategy and reject missing, duplicate or unknown pairs.
4. Keep event, strategy-evaluation, qualified-signal, reconnect, persistence and WAL counters monotonic within the same Run and process. Treat a restart, reset or unaudited strategy transition as a failure.
5. Require every sampled PAPER position to retain initial/current stop, TP1 and maximum planned loss. Keep actual orders, auth, private API, API keys, secrets and wallets at zero.
6. Allow planned rotation or critical lag only when PAPER entry fails closed and the final state recovers to RUNNING·LIVE·PAPER. Keep wide scanner lag observational and separate from executable bid/ask and trade lag.
7. Require bounded queue, event stalls, persistence flush, WAL checkpoint, current-RSS growth and probe errors. Record every independent check and exact failure in machine-readable JSON.
8. Provide exact wall-clock 30-minute, 6-hour and 24-hour targets. Never use an independent `PaperRuntime` soak or a shorter observation as proof of the installed service duration.
9. Keep tablet and mobile summary, primary navigation and secondary navigation controls at least 48×48px and verify root horizontal overflow is zero in the actual browser and Playwright.
10. Run backend, frontend, fixture, lint, typecheck, production build, PAPER safety, security, repository hygiene, network smoke and actual browser checks. Preserve profitability as `NOT_PROVEN` and 6-hour·24-hour as `NOT_RUN` until their independent gates are genuinely met.

## Normalized operation-transition audit Wave

1. Query only the incident table needed for reproduction. Do not run a full integrity check against the active multi-gigabyte writer.
2. Compare PAPER entry-intent, control-operation and replay-operation incident payloads for direct previous/new state, actor, cause, revision and reversibility fields.
3. Preserve each existing incident ID, category, full operation snapshot and history while adding a normalized append-only transition contract to new control and replay rows.
4. Record the first transition as `NONE` to revision 1 and every later transition from the exact previous revision. Mark terminal states as not reversible.
5. Keep irrelevant strategy and account fields explicitly null, attach the current Run only when it exists and attach replay source Run and symbol when applicable.
6. Do not migrate or rewrite historical incident rows. Do not change PAPER strategy thresholds, entry, exit, cost, account, ledger trade or Governor behavior.
7. Re-run targeted transition, control, replay, recovery and storage tests plus the complete backend, frontend, static, build, PAPER safety, security, repository hygiene and three-viewport browser suite.
8. Do not call the Wave deployed until the installed service loads the implementation and an actual new transition row satisfies the normalized contract. Keep the already-running 6-hour and 24-hour observations scoped to their baseline commit.

## Policy-retirement lock and strategy-transition audit Wave

1. Read only strategy-setting revisions needed to reproduce a rollback-policy gap. Do not alter the active PAPER service to prove that a protected control can fail.
2. Reproduce the same current RETIRED and prior SHADOW revision in an isolated runtime and require the pre-fix rollback request to demonstrate the bypass.
3. Separate a cost-adjusted policy-retirement lock from the generic OFF·RETIRED display. Keep ordinary user OFF reversible through the explicit revisioned control.
4. Block policy-retired rollback in the backend, not only in the browser. Preserve every historical revision, account and immutable trade.
5. Normalize strategy setting transitions in API history, checksum-verified strategy-settings rows and incidents with previous/new state, actor, cause, Run, strategy, revisions and reversibility.
6. Persist USER_UI configure, rollback, AUTO_GOVERNOR and recovery policy migration as strategy-specific transition events. Preserve MIGRATION in the original setting while presenting RECOVERY as the audit actor.
7. Add missing occurrence time and cause code to PAPER entry-intent transitions without changing pause or automatic-safety semantics.
8. Verify policy-locked controls, ordinary OFF reactivation and beginner-readable history in frontend unit tests and desktop, tablet and mobile Playwright.
9. Re-run complete backend, frontend, static, production build, fixture, PAPER safety, security and repository hygiene suites. Do not change strategy or cost thresholds.
10. Keep deployment, actual post-deploy ledger rows and actual 8870 browser verification `NOT_RUN` until the baseline long observers can finish without a process restart.

## Normalized startup-recovery transition audit Wave

1. Query only `PAPER_RESTART_RECOVERY` rows needed to establish the audit gap. Do not run a full integrity check against the active multi-gigabyte writer.
2. Preserve the existing lifecycle, recovery and open-position payload while adding a normalized append-only transition contract to every new startup-recovery incident.
3. Distinguish LIVE revalidation, READY-mode deferral, fail-closed recovery and DEMO fixture recovery with separate states, cause codes and beginner-readable Korean descriptions.
4. Never trust an invalid snapshot payload after checksum failure. Associate the failure only with the latest open Run identity obtained through an independent read-only lookup.
5. Emit an ERROR recovery incident for checksum, schema and restore failures. Keep the service in READY fail-closed state and mark the transition non-reversible.
6. Expose the latest startup recovery state, cause, timestamp and Run as flat runtime diagnostics. Show a concise beginner card and preserve raw values in the collapsible advanced diagnostics.
7. Do not rewrite historical recovery rows or migrate the storage schema. Do not change strategy thresholds, signals, costs, TP, SL, execution, Governor, risk budgets or PAPER safety.
8. Re-run targeted recovery paths, related recovery, storage, control and replay tests, complete backend and frontend suites, lint, typecheck, production build, PAPER safety, security, repository hygiene and desktop, tablet and mobile Playwright.
9. Preserve intermediate browser failures as fixed test evidence. Do not overwrite baseline screenshots when the Wave only verifies behavior.
10. Keep deployment, an actual post-deploy recovery row, actual 8870 browser verification, GitHub main and Actions `NOT_RUN` until the baseline six-hour observer can finish without a process restart.

## Normalized PAPER lifecycle-transition audit Wave

1. Query only current-Run `execution_audit` event counts and fixture `transitions` needed to establish the lifecycle audit gap. Do not run a full integrity check against the active multi-gigabyte writer.
2. Normalize only state-changing PAPER candidate, entry, protection and exit events. Keep risk rejection, duplicate-symbol rejection and user-entry-pause rows as diagnostics rather than manufacturing state changes.
3. Scope the transition cursor by account and symbol so League multi-position accounts retain independent order. Generate deterministic IDs from Run, account, symbol and response revision.
4. Record automatic PAPER lifecycle activity as `AUTO_SAFETY` and a user-requested main exit as `USER_UI`. Mark immutable entry and exit fills non-reversible.
5. Persist revision cursors, current states and the last transition in recovery snapshot schema v4. For schema v1 through v3, derive only pending or protected state from recovered accounts and begin a new cursor without inventing historical revisions.
6. Reject a schema v4 snapshot when account, symbol, revision, state or last-transition evidence disagrees. Do not partially restore a corrupt cursor.
7. Give new offline fixture rows the same normalized lifecycle contract and remember the final CLOSED transition before saving the fixture snapshot. Do not rewrite existing ledger rows.
8. Flatten the latest PAPER lifecycle transition into runtime diagnostics. Add a beginner-readable Korean summary card while retaining the raw contract in collapsible advanced diagnostics.
9. Re-run candidate lifecycle, fixture, recovery and replay tests, complete backend and frontend suites, lint, typecheck, production build, PAPER safety, security, repository hygiene and desktop, tablet and mobile Playwright without overwriting baseline screenshots.
10. Do not change strategy thresholds, costs, TP, SL, fill pricing, Governor, risk budgets, account topology or actual-order safety. Keep installed-service deployment, actual new lifecycle rows, actual 8870 verification, GitHub main and Actions `NOT_RUN` until the baseline long observers finish and every PAPER account is flat.

## Runtime strategy research-contract implementation Wave

1. Read the approved objective, current Registry descriptors, strategy API shape, Korean detail drawer, strategy catalog and source catalog before editing.
2. Add a frozen per-strategy research contract instead of duplicating untyped strings across API and frontend code.
3. Include strategy version, required public-market inputs, minimum warmup, entry hypothesis, falsification conditions, edge-decay policy, shared and independent PAPER risk budget, target universe, point-in-time leakage guards and primary Source IDs.
4. Reference one contract explicitly from every runtime descriptor and flatten those fields into the existing API row. Keep current lifecycle and change reason from the mutable revisioned strategy setting.
5. Verify each Source ID against `docs/20_RESEARCH_FOUNDATIONS_AND_ADAPTATION.md`. Do not convert a cited source or test result into a profitability claim.
6. Add the fields to the frontend contract and fixtures, display them in the existing beginner-readable Korean detail drawer and verify the label and value separately.
7. Correct stale current-state text in the strategy catalog from the actual Registry. Do not delete or rewrite historical evidence, accounts or immutable trades.
8. Write a failing backend descriptor test and a failing frontend detail test before the fix, then run targeted, related and full suites after the fix.
9. Run backend pytest, frontend Vitest, Ruff, project mypy, ESLint, TypeScript, production build, fixture, PAPER safety, security, repository hygiene and desktop, tablet and mobile Playwright. Preserve intermediate selector failures and require the final source to pass.
10. Do not deploy or restart the installed service while baseline six-hour and 24-hour observers are running. After they finish and all PAPER accounts are flat, deploy once and verify the actual 8870 API and browser before GitHub synchronization.

## LIVE-priority replay auto-abort Wave

1. Run a genuinely large stored-market replay against the installed service while sampling LIVE event progress, executable and trade lag, queue, reconnect, gap, resync, drop, persistence and entry-lock state. Preserve failures instead of reducing the input.
2. If LIVE safety is breached, cancel the worker immediately and label the attempt `FAIL` or `CANCELLED`; do not report an incomplete checksum as a successful replay.
3. Capture a replay-free comparison window before assigning causality. A provider timeout without queue growth is not proof that replay CPU alone caused the incident.
4. Take a lightweight LIVE PAPER baseline before starting the worker and poll it once per second. Fail closed on Run or market changes, actual orders, auth, storage lock, open PAPER positions, runtime error, queue over 64, executable p95 over 500ms, critical lag, event stall or new reconnect, gap, resync, drop and persistence faults.
5. Allow only a reconnect-counted planned rotation with a 15-second entry-lock grace. Never reinterpret an unplanned reconnect as a planned rotation.
6. Keep the existing `nice(19)`, 5% cooperative CPU budget and cancellable process. Await worker termination before finalizing the operation state.
7. Mark automatic safety termination `FAILED_RETRYABLE` with `REPLAY_ABORTED_LIVE_SAFETY` and include exact cause codes in the Korean message and append-only transition audit.
8. Do not persist a LIVE replay result from the worker. Persist it in the parent only after the final safety sample passes so a cancelled or unsafe result cannot appear as checksum evidence.
9. Add deterministic guard, planned-rotation, event-stall, critical-lag, probe-error, child-cancellation, HTTP-operation and non-persistence regression tests. Re-run complete backend, frontend, fixture, static, build, PAPER safety, security and repository-hygiene checks.
10. Deploy only through the immutable release path after the baseline observation boundary and all PAPER accounts are flat. Retry the same full scope under the installed watchdog and verify the actual browser, ledger transition and LIVE metrics before GitHub synchronization.
11. Preserve six-hour and 24-hour post-deploy observers as independent gates. Keep profitability `NOT_PROVEN` and do not change any strategy threshold, cost, TP, SL, fill, Governor or risk budget.

## Storage commit priority and all-symbol rotation warmup Wave

1. Preserve the replay-free 30-minute FAIL. Do not dismiss the 22.636-second flush or the 8.027-second planned-rotation critical incident because event progress and queue remained healthy.
2. Separate observed facts from causality. The cumulative slowest archive/ledger split and code path identify a strong hypothesis, while only post-deploy observation can prove the remedy.
3. Keep Parquet serialization, compression and file fsync under Darwin background policy. Temporarily leave background only for the SQLite connection, `BEGIN IMMEDIATE`, immutable metadata/candle inserts and `synchronous=FULL` commit.
4. Restore background policy in `finally` after both success and failure. Preserve WAL, FULL durability, checksum, atomic rollback and buffer restoration.
5. Replace the single Binance depth warmup boolean with a set of every selected deep symbol. Apply stale deltas to sequence state but emit no executable depth until every symbol has produced fresh depth within 1,500ms.
6. Keep entry locked while the set is incomplete. Do not shorten warmup or lower lag limits to avoid aborting replay.
7. Remove the runtime/replay circular import so isolated test collection and installed startup do not depend on prior import order.
8. Add failing-first tests for all-symbol warmup, process-priority release/reapply and direct runtime import. Run related and full backend, frontend, fixture, static, build, PAPER safety, security and repository-hygiene checks.
9. Do not deploy while the baseline six-hour observer is running. Preserve its known failures, then deploy once from an immutable clean commit while all PAPER accounts are flat.
10. After deployment verify actual flush component timings and at least one planned rotation before retrying the same 485,283-event replay. Start new six-hour and 24-hour observers only after this runtime gate.

## Maintenance-coordinated immutable release handoff Wave

1. Do not stop the baseline service while its loaded LaunchAgent still points at a mutable worktree whose current runner now requires a release manifest.
2. Add a fail-closed `--prepare-only` installer path that accepts no other arguments and preserves default installer behavior when the option is absent.
3. From a clean commit, stage and atomically activate the immutable `current` release and write its LaunchAgent plist without booting out, bootstrapping or kickstarting the loaded baseline job.
4. Keep the currently running dashboard scoped to its old release identity until a process restart proves otherwise. A changed `current` pointer alone is not deployment evidence.
5. Require zero main and Strategy League pending entries and positions before handoff. Keep real orders and auth false.
6. Run closed-ledger maintenance against the prepared plist so it gracefully unloads the old job, checkpoints WAL to zero, creates a same-device APFS clone and then bootstraps the new immutable job exactly once.
7. Require same-Run recovery, a new process, exact release commit, physical release backend root, matching frontend hash, LIVE·PAPER·RUNNING and zero positions before cross-device work continues.
8. Transfer the closed clone to a different physical device, require exact bytes and SHA-256, and run full immutable quick-check and foreign-key validation while independently monitoring the new LIVE service.
9. Preserve the baseline six-hour FAIL and the operator-aborted contaminated 24-hour run. Start fresh post-deploy six-hour and 24-hour observers only after installed flush and planned-rotation gates pass.
10. Do not change strategy thresholds, costs, TP, SL, fills, Governor, risk budgets or ledger precision. Keep actual orders, private API, API keys, secrets, wallets and runtime AI decisions at zero.

## Immutable replay-input scope Wave

1. Treat an open LIVE Run as an append-only source whose event count can increase while a long replay is running. A matching Run ID and symbol alone do not prove matching input.
2. Capture the selected timeline event count when the user requests strategy verification and submit it as an explicit positive `event_limit`.
3. Resolve and validate the effective count before starting the asynchronous operation. Reject a requested count larger than the currently persisted scope instead of silently widening or shortening it.
4. Apply the effective count in the isolated process and the in-process replay path. Require the loader to return exactly that many checksum-verified ordered events.
5. Keep the operation `total_events` field as the exact frozen input count, not an approximate progress label.
6. Expose a SHA-256 over the normalized source-event stream separately from the end-to-end checksum that also binds strategy version, configuration and decision path.
7. Label older stored results without an input-only checksum honestly. Do not relabel the existing end-to-end checksum as an input checksum.
8. Preserve the previous cancelled 485,283-event operation as FAIL/CANCELLED. A new 485,283-event attempt may prove its own fixed input checksum but cannot retroactively prove the cancelled attempt's unfinished checksum.
9. Add failing-first backend fixed-scope and frontend request-contract tests, then run complete backend, frontend, static, build, PAPER safety, security, repository hygiene and three-viewport browser validation.
10. Do not alter strategies, thresholds, costs, TP, SL, fills, Governor, risk, account topology or PAPER safety. Deploy only after the baseline six-hour boundary and the maintenance-coordinated immutable handoff.

## Consumer lock-leak and overload-recovery Wave

1. Preserve the baseline queue-full incident and its increasing drop count until the six-hour observer finishes. Do not restart the installed service early to make the metrics look healthy.
2. Treat event progress from the provider as insufficient proof of market processing. Compare consumer delivery, strategy evaluation and persistence completion independently.
3. Release the process `RLock` when SQLite `BEGIN IMMEDIATE` raises before a transaction context has been entered. Re-raise the original exception and keep FULL durability unchanged.
4. Isolate one sink exception to one explicitly counted delivery drop. Keep the consumer task alive, lock new PAPER entry and expose the exact failure count and last failure time.
5. Treat a full bounded queue as an active safety incident. Keep the lock until consecutive successful deliveries and a low-water queue depth prove recovery.
6. Expose supervisor and consumer running, delivery, failure, drop, recovery and queue overload start/recovery metrics through the existing diagnostics contract.
7. Refresh supervisor safety while building the dashboard. If either task is not running, do not report active market observation or PAPER entry. Show task termination before any overlapping storage lock and offer same-Run start recovery instead.
8. Make same-Run recovery literal. `자동 관찰 시작` must replace only the stopped supervisor while preserving Run ID, PAPER accounts, ledger and replay scope; only the separate `새 Run` action may archive and create a Run.
9. Add failing-first tests for transaction-lock release, consumer task survival, producer/supervisor liveness, truthful stopped-task UI and same-Run control recovery. Run related and full backend, frontend, fixture, static, build, PAPER safety, security, repository hygiene and three-viewport browser checks.
10. Deploy only after the contaminated baseline six-hour observer is written and the 24-hour observer is operator-aborted with its partial evidence preserved. Verify the exact new release through the coordinated ledger handoff.
11. Do not alter strategy thresholds, costs, TP, SL, fills, Governor, risk budgets, account topology or PAPER-only safety. Require fresh 30-minute, six-hour and 24-hour observations after deployment.

## Failed-runtime coordinated maintenance Wave

1. Do not restart a failed baseline once merely to make the maintenance precondition look healthy and then restart it again for deployment.
2. Permit an explicit failed-runtime recovery mode only when the existing violations are limited to `ENTRY_LOCKED` and `QUEUE_LIMIT_EXCEEDED` with zero open positions, zero real orders, no authentication and PAPER execution.
3. Reject every additional runtime violation before shutdown. Do not use this mode to bypass an open position, runtime error, storage block, critical lag, Run mismatch or non-PAPER state.
4. Record the requested and applied override, initial violations and fixed recovery reason in machine-readable evidence.
5. Require the recovered immutable release to pass the normal strict runtime checks without the override.
6. Keep the single graceful bootout, closed WAL checkpoint, APFS clone, cross-device SHA-256, full quick-check, foreign-key check and same-Run recovery contract unchanged.

## Closed transfer before LIVE restart Wave

1. Do not read a multi-gigabyte APFS clone from the source device while the recovered LIVE service performs FULL SQLite commits on that same device.
2. Keep the service stopped through closed WAL checkpoint, APFS clone, cross-device transfer and source/verification SHA-256 comparison.
3. Remove the source-side clone only after exact transfer verification, then start the prepared immutable release and require same-Run recovery.
4. Run full immutable quick-check and foreign-key validation only on the different-device copy while strictly monitoring the recovered LIVE service.
5. If transfer fails, restore the prepared LaunchAgent from `finally`. If post-restart integrity verification causes a safety violation, abort without weakening the safety threshold.

## Immediate control feedback and focus-replay isolation Wave

1. Reproduce pause and resume with a deferred HTTP response and require the visible control to change immediately, stay disabled and return to the server-authored state after completion.
2. Keep `Idempotency-Key`, expected revision, actor and reason payload unchanged. The pending label is acknowledgement of the request only and never overrides the server state.
3. Add an exact `(run_id, trade_id, profile)` ledger read for main and checksum-verified shadow PAPER trades.
4. Limit the focus BASE·STRESS comparison to the same Run, strategy, symbol and side. Make tests fail if focus falls back to broad `list_trades` or `list_shadow_trades` scans.
5. Route LIVE focus construction through `to_process.run_sync` and the existing replay process lock with immutable ledger and archive paths. Keep non-LIVE fixture behavior in process.
6. Run targeted focus/process tests, the full backend and frontend suites, Ruff, mypy, ESLint, TypeScript, production build, PAPER build safety, security and repository hygiene.
7. Write ADR-066 and `evidence/WAVE66_CONTROL_AND_FOCUS_REPLAY_QA.json`. Preserve the raw contaminated maintenance evidence separately and do not convert an aborted quick-check into integrity PASS.
8. Commit and stage a clean immutable release. Run the maintenance handoff alone, with no browser, replay, tests, build, source scan or other local I/O until it completes.
9. After clean integrity and same-Run recovery, reload the actual 8870 browser, click pause and resume, verify the immediate labels, navigate every primary page and replay an uncached completed PAPER trade while measuring latency and LIVE safety.
10. Continue with a clean 5-minute comparison, 30-minute soak, fixed 485,283-event replay and only then new six-hour and 24-hour observers. Keep every unfilled duration `NOT_RUN` and profitability `NOT_PROVEN`.

## Planned-rotation safety-waiting grace Wave

1. Preserve the uncontaminated actual maintenance failure and classify transfer, hash and same-Run recovery separately from the incomplete quick-check.
2. Reproduce `planned_rotations +1`, `SAFETY_WAITING` and `entry_locked=true` inside the existing 15-second transition grace.
3. Allow that exact state only when the existing planned/reconnect count contract already allows the transition. Do not create a broader operation-state exemption.
4. Keep `MANUALLY_PAUSED` and every other non-RUNNING state fail-closed even if planned rotation counters change.
5. Keep the existing grace duration, queue, lag, critical incident, reconnect, gap, resync, drop, persistence, Run, process, position, actual-order and auth checks unchanged.
6. Add direct guard and threaded monitor regressions, then run ledger and service targets, complete backend, Ruff, mypy, PAPER build safety, security and repository hygiene.
7. Commit and stage the fixed immutable release, then rerun the same full maintenance command without browser actions, replay, tests, build or source scans.
8. Keep integrity `NOT_RUN` until the real quick-check and foreign-key results are returned. Do not infer database corruption from a monitor-contract abort.

## Replay I/O and live persistence stabilization Wave

1. Preserve every large fixed replay attempt as `PASS`, `FAIL` or `ABORTED_OPERATOR`; never turn an unfinished 485,283-event run into replay proof.
2. Freeze replay to the durable event prefix captured at request time. Limit Parquet worker threads, bound row batches and yield between checksum and evaluation chunks.
3. Keep full Run replay in the isolated low-priority process, but do not place short SQLite `synchronous=FULL` ledger commits at Darwin background priority. Use worker niceness 10 for bounded ledger progress and restore archive compression to background priority around the commit.
4. Persist live market events in 1,000-event bursts and request a PASSIVE WAL checkpoint every 4 successful flushes. Restore buffers and lock new PAPER entry on any worker or checkpoint fault.
5. Keep the public-market consumer and strategy evaluator authoritative. A replay is automatically unsuccessful if event progress, consumer delivery, queue, processing/trade lag, reconnect, gap, drop, persistence, WAL or PAPER safety crosses the registered observer gate.
6. Hide current-version strategy statistics until the validated cache is ready. Do not flash zero, prior-version or partially merged values as a current win rate.
7. Verify the user path separately with a small saved public Run. Load precise events, start the same-condition replay, wait for terminal state, compare the input checksum and click the next-event control in the actual browser.
8. Evaluate all 11 strategies against the same public input with 22 independent BASE/STRESS accounts. Retain six unproven candidates in SHADOW and five failed candidates as immutable RETIRED/OFF evidence; deletion is not an acceptable shortcut.
9. Do not rank or promote before 30 current-version `LIVE_PUBLIC` trades and cost-adjusted expectancy, Profit Factor, drawdown, BASE/STRESS, chronological OOS and robustness gates. Keep profitability `NOT_PROVEN` until those gates pass.
10. Keep natural signal thresholds, TP1, TP2, SL, fills, fees, slippage, Governor, account topology and risk budgets unchanged. Actual orders, private APIs, credentials, wallets and runtime AI order decisions remain zero.

## Trade target truth and post-release long-observation Wave

1. Audit only the current Run, current strategy version, Strategy League accounts and `LIVE_PUBLIC` samples before interpreting win rate or holding time.
2. Preserve explicit TP1 and TP2 prices independently in the history contract and display them with distinct labels. Never infer which target a legacy single `take_profit` represented.
3. Trace every hold below 10 seconds through entry, management and exit audit. Do not change the 10-second grace, two-signal confirmation or three-second persistence unless a current-version defect is reproduced.
4. Separate gross directional correctness from fees, spread and slippage. A gross-positive but net-negative row is not a winning strategy sample.
5. Keep all 11 strategies and 22 BASE/STRESS accounts on identical public-market input. Preserve six SHADOW and five RETIRED/OFF histories while profitability is unproven.
6. Require 30 current-version natural samples, cost-adjusted expectancy, Profit Factor, drawdown, BASE/STRESS, chronological OOS, walk-forward and robustness before ranking or promotion.
7. Run the complete backend, frontend, fixture, lint, typecheck, build and desktop/tablet/mobile browser regression before installing an immutable release.
8. Deploy only while every PAPER account is flat, preserve the current Run and ledger, then verify the exact release commit, LIVE public data, PAPER execution, zero real orders and zero authentication.
9. Start a new six-hour observer only after deployment. Keep its status `IN_PROGRESS` until all wall-clock time and samples have actually completed, and keep 24 hours `NOT_RUN` until separately executed.
10. Do not overlap the long observer with replay, full tests, builds or ledger maintenance. Preserve any threshold violation as FAIL rather than restarting or weakening the gate.

## Persistence backlog containment and truthful release-loading Wave

1. Preserve the interrupted Wave 94 observer as `ABORTED_OPERATOR` with every failed latency and duration gate. Never overwrite it with the later short PASS.
2. Measure event-loop delay independently from exchange timestamps. Keep processing, trade and wide-scanner latency as separate fields and fail closed on critical local delay.
3. Keep 1,000-event `synchronous=FULL` persistence batches and do not drop public-market input to hide storage pressure.
4. When backlog is at least 2,000 events and WAL is below 16MiB, defer a due PASSIVE checkpoint by one successful flush. At 16MiB or the existing 64MiB hard boundary, run the registered checkpoint safety path.
5. At 10,000 queued persistence events, pause only new PAPER entries with `PERSISTENCE_BACKLOG_ENTRY_LOCK`. Continue observation, archive recovery, position protection and exit handling, and release the lock only after backlog is at most 2,000.
6. Expose event-loop maximum delay, persistence backlog peak, backlog lock count and deferred checkpoint count in the Korean advanced diagnostics.
7. Validate every open PAPER position with planned and actual entry, quantity, initial and current stop, TP1, TP2, maximum planned loss, entry and estimated exit fee, slippage, `paper_only=true`, `real_orders=false` and `auth=false`.
8. Do not show a release mismatch before the first real dashboard response. Keep the loading state until the frontend and server commits can actually be compared, then fail closed only on a confirmed mismatch.
9. Exercise history, precise saved-event loading, next-event, play and pause, strategy analysis and advanced diagnostics in the actual browser. Keep this browser proof separate from fixture screenshots.
10. Run a clean five-minute observer after deployment without replay, builds, tests or ledger inspection. A short PASS is only a regression baseline; start a new six-hour observer afterward and keep 24 hours `NOT_RUN` until separately completed.

## Observation-window event-loop truth Wave

1. Preserve the prematurely started 210.311-second long observer as `ABORTED_OPERATOR`; its baseline queue already exceeded the registered gate and its event-loop result used a process-lifetime maximum.
2. Count local event-loop delays above the default 500ms soak threshold and expose the count, last timestamp and last value in diagnostics.
3. For the default 500ms contract, fail only when that counter advances inside the observation window. Keep the process-lifetime maximum visible for diagnostics and keep custom thresholds on the prior maximum rule.
4. Preserve a real critical executable-path lag as `FAIL` even when the new local event-loop counter does not advance. Never weaken the processing or critical-lag gate to make a retry pass.
5. Run the retry without restart, replay, build, tests or ledger inspection. Treat a later PASS only as a clean five-minute recovery window and keep the earlier FAIL visible.
6. Audit each new current-version `LIVE_PUBLIC` BASE/STRESS pair for holding time, initial stop, TP1, TP2, exit reason, gross PnL, fees, slippage and net PnL.
7. Do not classify a gross-positive but net-negative trade as a win. Keep fewer than 30 samples per cost profile unranked and profitability `NOT_PROVEN`.
8. Run the complete backend, frontend, fixture, lint, typecheck, build, PAPER safety, security, repository hygiene and desktop/tablet/mobile browser regression before GitHub synchronization.
9. Install the exact immutable source release, verify the actual browser and preserve the same Run, public-market LIVE input, PAPER execution, zero real orders and zero authentication.
10. Start a fresh six-hour observer only from a valid bounded baseline after the final documented release. Keep 24 hours and the fixed 485,283-event replay `NOT_RUN` until actually executed.

## Nonblocking storage-health and atomic execution-persistence Wave

1. Preserve the Wave 97 clean 20-minute queue and event-loop failures. Do not replace them with later passing samples.
2. Move disk usage, archive safety and ledger safety probes to one off-loop worker. Normal dashboard, replay-safety and market-event paths read only the last completed cache.
3. Fail new PAPER entry closed when the storage-health cache is older than five seconds. Continue public-market observation and protection or exit of an existing PAPER position.
4. Persist all order, fill, main trade, strategy trade, execution-audit, changed-account and recovery-snapshot rows created by one market event in one SQLite transaction.
5. Complete invariant validation, canonical JSON and checksum computation before `BEGIN IMMEDIATE`. Advance in-memory persisted IDs and audit offsets only after commit; retry the unchanged state after rollback.
6. Yield the consumer cooperatively after 10ms of uninterrupted synchronous work without changing event order, signal thresholds or the bounded-queue fail-closed contract.
7. Expose storage-health, atomic execution-persistence and consumer-yield counters in advanced diagnostics and the running-service observer.
8. Audit every natural trade in the observation window for entry, initial stop, TP1, TP2, holding time, exit reason, gross PnL, all costs and net PnL. Keep a 13.864-second `EDGE_DECAY` distinct from the historical 1–3-second defect.
9. Keep 11 strategies, 22 BASE/STRESS accounts, all historical rows, zero real orders and zero authentication unchanged. Do not lower natural-signal thresholds to create a passing runtime sample.
10. A passing five- or 20-minute retry does not prove six or 24 hours and does not prove profitability.

## Off-loop dashboard and bounded LIVE display-memory Wave

1. Preserve the first atomic-persistence five-minute failure with its 577ms event-loop delay. Trace the remaining event-loop work before applying another change.
2. Build the dashboard snapshot and JSON outside the market event loop. Serialize concurrent WebSocket broadcast, HTTP dashboard and mutation-response snapshots with one async lock.
3. Keep status and market consumption independent from the dashboard lock so a slow or disconnected screen cannot stop watchdog or strategy evaluation.
4. Limit only the LIVE display deque to 2,048 events and copy only the most recent 512 by reverse bounded iteration. Preserve every authoritative event in the persistence buffer and public-market archive.
5. Record per-event processing count, latest and maximum duration, count above 100ms, maximum event type and symbol to distinguish strategy work from dashboard work.
6. Run an uncontaminated five-minute observer and then an uncontaminated 20-minute observer. Require queue at most 64, no new event-loop delay above 500ms and no executable-path critical incident, unplanned reconnect, gap, resync, drop or persistence fault.
7. Exercise pause, resume, strategy details, history, trade details, saved replay, analysis, settings, symbol drawer and indicator controls in the actual browser. Verify desktop, tablet and mobile without horizontal overflow or console errors.
8. Keep actual browser evidence separate from OFFLINE FIXTURE Playwright screenshots and record the exact release SHA for both.
9. Never run a full SQLite integrity scan against the growing active writer. A mistakenly started scan must be terminated, marked `NOT_RUN` and followed by runtime-safety checks; only ADR-049's closed immutable-copy procedure can yield a full integrity PASS.
10. Synchronize code, raw failures, passing evidence and documentation to GitHub main, confirm Actions, then start the clean six-hour observer without replay, tests, build or ledger maintenance.

## Cost-aware ordinary exit and beginner-first results Wave

1. Freeze and audit the pre-change current-revision `LIVE_PUBLIC` cohort before changing policy. Preserve every immutable trade and separate gross movement, fees, slippage and net PnL.
2. Ordinary `EDGE_DECAY` requires a 30,000ms post-fill grace, at least two distinct adverse reasons, executable best bid for LONG or best ask for SHORT, a loss of at least `max(0.25R, planned round-trip cost R)`, and 3,000ms event-time persistence.
3. Clear ordinary edge-decay persistence when executable price recovers inside the cost band. A weak reason code by itself must never crystallize a round trip.
4. Preserve immediate initial STOP, TP1, TP2, data/system safety and explicit maximum-hold paths. Profit protection after MFE +0.8R may bypass only the grace and loss-band checks; it still requires two adverse reasons and 3,000ms persistence.
5. Increment the strategy implementation revision and restart natural BASE/STRESS evidence at zero. Do not relabel or delete earlier trades, and do not claim higher win rate or profitability from deterministic tests.
6. Make normal History, Strategy, Open Position, Performance, Risk, Strategy-Symbol and Replay views beginner-first. Show easy Korean outcome, protection and next-action information by default; retain raw ids, checksums, reason codes, revisions and research statistics inside collapsed advanced details.
7. Distinguish prior `EDGE_DECAY` rows from the new contract in visible copy. A prior row must not be explained as if it passed the current price-and-cost confirmation.
8. Verify desktop, tablet and mobile layouts, actual trade-detail/replay controls, entry/TP1/TP2/SL/exit annotations and browser console output. Screenshots supplement interaction checks but do not replace them.
9. Run the complete backend and frontend suites, lint, typecheck, build, PAPER safety, security, repository hygiene and immutable-release service observation. Keep actual orders, auth, private APIs, credentials and wallets at zero.
10. A growing active ledger that cannot complete an online snapshot is `NOT_RUN`, not corrupt and not PASS. Stop bounded retries, remove temporary copies and reserve full integrity for a closed immutable copy or an explicitly approved maintenance window.

## Executable-lag quarantine Wave

1. Preserve the first Wave 98 six-hour attempt as `ABORTED_OPERATOR`; it stopped after 1,141.869 seconds with 30 critical executable-lag events and one incident. Never reuse its initial normal samples as a six-hour PASS.
2. Diagnose the incident from the immutable market archive, not from a dashboard percentile alone. Record file checksum, exact event types, symbols, calibrated lag values and receive-time burst duration.
3. Keep the registered 1,500ms critical threshold unchanged. Mark a LIVE depth or orderbook event above that threshold `EXECUTABLE_LAG_STALE` before it enters the consumer queue.
4. Preserve the event id, sequence, venue timestamp, lag and book payload in the authoritative Run archive and ledger. Continue counting the critical incident and fail-closed entry lock.
5. Do not update the latest executable book, PAPER fills, feature history, strategy evaluation, candidate planning or position-health decisions from stale or sequence-invalid depth.
6. Keep the data-gap start until the same symbol receives fresh sequence-valid depth. Apply the existing recovery, TP/SL and emergency-stale policies only from valid same-venue data.
7. Expose quarantined event count and the latest symbol, event type, lag and venue timestamp in Korean advanced diagnostics. Keep wide ticker and trade-lag telemetry separate.
8. Add regression tests for the configurable threshold boundary, non-executable event exclusion, archive flag preservation, latest-book and feature immutability, strategy-evaluation immutability and fresh-depth recovery.
9. Run the complete backend, frontend, lint, typecheck, build, PAPER safety, security, repository hygiene and desktop/tablet/mobile actual-browser checks before GitHub synchronization.
10. Deploy an immutable release with the same Run and flat PAPER state, verify zero real orders and authentication, then run a clean five-minute baseline followed by a new uninterrupted six-hour observer. Keep 24 hours and profitability `NOT_RUN` and `NOT_PROVEN` until their actual gates are met.
