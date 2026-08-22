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

## Source of truth

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

## Completion rule

Do not state DONE until `FINAL_EVIDENCE.md` exists and the acceptance matrix is populated with PASS, NOT_RUN, or BLOCKED plus evidence. Never convert NOT_RUN into PASS.
