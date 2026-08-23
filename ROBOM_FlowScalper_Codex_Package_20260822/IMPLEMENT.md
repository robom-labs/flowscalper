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
3. Maintain deep 20 with dwell, protected symbols and bounded rotation; append every selected universe snapshot.
4. Publish strategy×symbol analytics only from completed ledger trades and withhold ranking below 30 samples.
5. Normalize main and BASE/STRESS positions into `focus_positions`. Auto focus only a newly observed actual `trade_id` fill and persist the user's focus lock.
6. Build trade-centered replay from stored public events. Bound frames at 50,000, preserve state transitions, hide future markers and use timestamp-based 0.5x–80x playback.
7. Run local static/unit/E2E checks, separate actual Chrome review, public network smoke and genuine 30-minute soak. Leave 6h, 24h and Release as `NOT_RUN` when not executed.
