# AGENTS.md — ROBOM FlowScalper Repository Rules

## Mission

Build a production-quality **real-market-data / paper-execution-only** crypto scalping research application. Runtime must not call OpenAI, Codex, TradingView, private exchange APIs, or any real order endpoint.

## Non-negotiable invariants

1. `REAL_TRADING` is structurally disabled. Do not add a functioning live order path.
2. No API key, password, login, wallet, withdrawal, transfer, or private account endpoint is required or accepted.
3. A live market-data failure must never be displayed as a successful LIVE connection.
4. No invented probability, win rate, or expected value. Use `CALIBRATING` until data and validation are sufficient.
5. Paper fills must consume executable bid/ask depth after latency; do not fill at last price by default.
6. Initial stop may never move in an adverse direction.
7. No averaging down, martingale, pyramiding, or automatic risk escalation.
8. No fixed 120-second forced exit. Exit by TP, SL, edge decay, data safety, or emergency stale policy.
9. Keep data from different venues and experiment Runs separate.
10. Never claim profitability or safety guarantees.
11. Keep the Shared Capital Benchmark at one position and each Strategy League account at three different symbols maximum.
12. Strategy League accounts are `strategy_id:BASE` and `strategy_id:STRESS`; capital, risk, fees, cooldowns, orders and PnL must never cross accounts.
13. A/B default to `ACTIVE`; C~I default to `SHADOW`. Every strategy and profile remains PAPER-only.

## Source of truth

- Current version: `VERSION`
- User-visible version history: `CHANGELOG.md`
- Product and technical contract: `docs/`
- Execution roadmap: `PLANS.md`
- Runbook: `IMPLEMENT.md`
- Configuration examples: `config/`
- Event contracts: `schemas/`

When documents conflict, apply this priority:

1. Safety invariants in this file
2. `docs/13_ACCEPTANCE_CRITERIA.md`
3. `docs/01_PRODUCT_REQUIREMENTS.md`
4. Other detailed docs
5. Example configuration

Record necessary design changes in an ADR rather than silently changing the contract.

## Upgrade and repository hygiene

- Keep exactly one current implementation on `main`. Do not add `old`, `legacy`, `backup`, `copy`, version-suffixed source directories, or parallel old/new UI implementations.
- When a feature or UI is replaced, find and remove its obsolete entry points, state, copy, styles and tests in the same change unless an explicit migration window is required.
- Preserve past source with Git history and immutable version tags. Preserve distributable ZIPs, checksums and release evidence as GitHub Release assets.
- Keep only a concise user-visible summary in `CHANGELOG.md`. Do not copy completed implementation plans or full old source trees into the current tree.
- Keep runtime SQLite, Parquet, logs, caches and build outputs out of Git. Move inactive legacy runtime data to a verified migration archive instead of deleting or reading it as current state.
- Follow `docs/18_VERSIONING_AND_UPGRADE_POLICY_KO.md` and ADR-009. Run `make repo-hygiene` before every commit and release.

## Working discipline

- Continue from planning into implementation unless an external permission boundary prevents it.
- Use official exchange documentation as the primary source.
- Prefer direct `httpx`/`websockets` adapters over unofficial trading SDKs.
- Keep adapters isolated from strategies and paper execution.
- Use typed domain models and deterministic clocks in tests.
- Run relevant tests after each meaningful change.
- Fix failures before declaring a milestone complete.
- Keep `PLANS.md` progress and decisions current.
- Commit each completed Wave with a meaningful message.

## Required commands

Codex may adapt exact commands after scaffolding, but the completed repository must expose equivalent commands:

```text
make setup
make dev
make test
make lint
make typecheck
make build
make e2e
make network-smoke
make repo-hygiene
```

Windows and macOS helper scripts are also required.

## Review requirements

Every order lifecycle change must include tests for:

- duplicate events
- partial fills
- stale data
- disconnection/reconnection
- pessimistic ambiguous ordering
- state recovery
- absence of real-order calls

Every strategy change must include:

- deterministic fixture tests
- cost-aware rejection tests
- no-lookahead tests
- parameter/config documentation

Strategy League changes must additionally test account isolation, three-symbol limits,
BASE/STRESS cost separation, real event-time confirmation and recovery compatibility.

Control and dashboard changes must additionally keep long-running Run changes behind an
asynchronous operation contract. Submission returns `202`, duplicate actions reuse the
same operation, conflicting actions return `409`, cancellation cleans unregistered
supervisors, and retry/blocked errors remain explicit Korean UI states.

The default UI uses `league_accounts` and `league_positions`, keeps the beginner home
separate from the advanced terminal, and never calls SHADOW “record only”. Scanner rows
must not resize during snapshots or drawer use. A normal update to the same chart selection
must use incremental series updates instead of rebuilding the chart or repeating full
`setData` calls.

Phase 03 uses five user-facing navigation groups with `시장` as the default. Binance
USD-M full catalog is PAPER eligible, while Upbit KRW is observation-only and must never
feed a PAPER position. The chart defaults to 3-minute, 200 historical candles, MA10/MA20
and volume overlay. Actual fills, not candidates or pending entries, are the only automatic
trigger for `PositionFocusWorkspace`. Replay markers must never reveal future events, and
0.5x through 80x must preserve the same ordered keyframes and final checksum.

## Completion rule

Do not state DONE until `FINAL_UPGRADE_EVIDENCE.md` exists and the acceptance matrix is populated with PASS, NOT_RUN, or BLOCKED plus evidence. Never convert NOT_RUN into PASS.
