# 02. Architecture

## 2.1 Logical overview

```text
Public Venue REST/WebSocket
          │
          ▼
Venue Adapter + Connection Supervisor
          │
          ▼
Normalized Event Bus
          │
          ├── Symbol Metadata / Universe Manager
          ├── Order Book Builders
          ├── Trade Tape / Candle Builder
          └── Data Health Monitor
          │
          ▼
Feature Engine
          │
          ▼
Regime Classifier
          │
          ▼
Strategy Detectors
          │
          ▼
Candidate Ranker + Pre-trade Gate
          │
          ▼
Paper Execution Engine
          │
          ▼
Position Manager + Risk Manager
          │
          ▼
Persistence / Replay / Analytics
          │
          ▼
FastAPI WebSocket/API
          │
          ▼
React Dashboard
```

## 2.2 Recommended repository structure

```text
robom-flowscalper/
├─ backend/
│  ├─ app/
│  │  ├─ api/
│  │  ├─ adapters/
│  │  │  ├─ binance_usdm/
│  │  │  ├─ bybit_linear/
│  │  │  └─ fixture/
│  │  ├─ domain/
│  │  ├─ events/
│  │  ├─ market_data/
│  │  ├─ orderbook/
│  │  ├─ universe/
│  │  ├─ features/
│  │  ├─ regime/
│  │  ├─ strategies/
│  │  ├─ costing/
│  │  ├─ execution/
│  │  ├─ positions/
│  │  ├─ risk/
│  │  ├─ storage/
│  │  ├─ replay/
│  │  ├─ analytics/
│  │  ├─ reporting/
│  │  └─ main.py
│  └─ tests/
├─ frontend/
│  ├─ src/
│  └─ tests/
├─ config/
├─ data/
├─ docs/
├─ scripts/
├─ Makefile
├─ pyproject.toml
└─ README.md
```

## 2.3 Process model

Start with one Python process and one frontend static bundle.

Recommended asynchronous tasks:

- one connection supervisor per venue/channel family;
- one normalized event dispatcher;
- one order-book worker per deep symbol or sharded worker pool;
- one feature worker pool;
- one strategy/risk decision loop;
- one persistence writer with bounded queues;
- one API broadcast loop.

CPU-heavy feature aggregation may use a process pool only after profiling. Do not prematurely introduce distributed infrastructure.

## 2.4 Event model

Every normalized event must include:

- venue;
- run_id;
- symbol;
- instrument type;
- venue event timestamp;
- venue transaction timestamp when available;
- local receive monotonic timestamp;
- sequence/update identifiers;
- payload version;
- data-source quality flags.

Use immutable domain events. Persist state transitions and decision snapshots.

## 2.5 Precision model

- Store prices and quantities as Decimal or integer units derived from tick/step sizes.
- Market-data feature calculations may use float64 internally where appropriate.
- Convert back through explicit instrument precision rules before execution simulation.
- Never infer precision from displayed decimal places alone.

## 2.6 Clock and determinism

Provide an injectable clock abstraction:

- `SystemClock` for live paper;
- `ReplayClock` for deterministic replay;
- `TestClock` for unit tests.

Use monotonic time for timeouts/latency and UTC timestamps for persistence/display.

## 2.7 Backpressure

Queues must be bounded. Define behavior for overload:

- never block venue heartbeat handling;
- drop only explicitly droppable UI updates, not order-book sequence events;
- if critical market events cannot be processed within limits, mark symbol `DEGRADED` and prohibit entry;
- record dropped-event metrics;
- automatically reduce deep-scan symbol count if sustained processing lag exceeds the threshold.

## 2.8 Configuration

Configuration layers:

1. committed safe defaults;
2. local user YAML;
3. environment variables for paths/ports only;
4. immutable Run snapshot.

No secret configuration is needed in v0.1.

## 2.9 Runtime modes

```text
FIXTURE_OFFLINE
LIVE_SHADOW_PAPER
REPLAY
```

There is no functioning LIVE_TRADING mode. Any enum or API request attempting it must fail safely.

## 2.10 API design

Read-only/control APIs may include:

- system status;
- venue health;
- universe;
- candidates;
- current paper position;
- runs;
- trades;
- replay control;
- paper pause/resume;
- emergency simulated close;
- new Run/reset;
- configuration preview.

No real order, credential, transfer or withdrawal endpoint may exist.

## 2.11 Phase 03 market and focus services

- `MarketExplorerService` owns read-only Binance USD-M and Upbit KRW catalogs plus historical candles. It is isolated from the PAPER execution venue adapter.
- `PersistentPublicSupervisor` keeps wide and deep streams; market catalog browsing never subscribes every symbol to depth.
- `PaperRuntime.focus_positions()` is the typed dashboard source for main and BASE/STRESS positions after actual fills.
- `ReplayFocusSessionBuilder` reads stored public events and bounded candles, then exposes timestamp-ordered frames. It does not create a second execution database.
- The React shell has five navigation groups. `MarketPage`, `PositionFocusWorkspace` and `ReplayPage` share `PriceChart` and indicator functions.

## 2.12 Strategy Governor boundary

- `StrategyRegistry` is the single in-memory owner of lifecycle, mode, directions, CAS revision and manual lock.
- `StrategyGovernor` only evaluates supplied immutable research and operational evidence. It never rewrites strategy source or lowers signal thresholds.
- Multi-strategy champion replacement validates every target before mutation. `ACTIVE` is unique in the normal default, while `SHADOW` and `CHALLENGER` keep their independent BASE/STRESS PAPER accounts.
- SQLite `strategy_settings` rows and `AUTO_GOVERNOR_TRANSITION` incidents retain actor, reason, evidence period and metrics. Rollback creates a new revision.
- Missing OOS, robustness, multiple-testing or natural-sample evidence is fail-closed as `NOT_PROVEN`; a successful unit test is not substituted for that evidence.

## 2.13 Canonical candle and intraday research boundary

- `backend/app/market_data/timeframes.py` is the single timeframe registry for API, dashboard labels, market history and research. Public chart intervals are 1m, 3m, 5m, 15m, 30m, 1h and 4h; internal research may additionally aggregate 1s, 5s, 15s and 30s.
- `CandleBuilder` owns event-ID deduplication, symbol-local ordering and complete-boundary emission. The in-progress candle is never exposed as a completed research observation.
- `backend/app/intraday/` consumes completed candles and provides research-only multi-timeframe features, horizon-specific immutable plans and ORIGINAL/MIRROR/REVERSE candidate variants.
- The production `StrategyRegistry` remains the sole runtime strategy source. An intraday research result cannot register, promote or alter a runtime strategy.
- Registry strategy count and BASE/STRESS account count are derived from the backend registry payload. Removed strategies retain immutable accounts and trades; newly approved IDs require an explicit migration.
- See ADR-039 and `docs/20_RESEARCH_FOUNDATIONS_AND_ADAPTATION.md`.

## 2.14 Process resource truth boundary

- `ProcessResourceSampler` exposes current resident memory and lifetime peak resident memory as separate values. A peak counter must never be labeled as current usage.
- macOS reads current RSS from `proc_pidinfo(PROC_PIDTASKINFO)`, Linux reads `/proc/self/statm`, and Windows reads the current working set. Platform-native failure falls back to the peak value with the explicit source label `PEAK_MAX_RSS_FALLBACK`.
- `process_memory_mb` and soak `memory_growth_mb` mean current RSS. `process_memory_peak_mb` and `peak_memory_growth_mb` are diagnostic high-water marks.
- The advanced Korean system view names both meanings explicitly. A smaller current RSS after garbage collection or buffer release is valid even while the lifetime peak remains unchanged.
- This telemetry change does not alter strategy thresholds, PAPER plans, fills, positions, safety locks, Registry state or ledger records. See ADR-048.
