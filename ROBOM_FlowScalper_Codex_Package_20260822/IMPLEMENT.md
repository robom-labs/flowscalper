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
