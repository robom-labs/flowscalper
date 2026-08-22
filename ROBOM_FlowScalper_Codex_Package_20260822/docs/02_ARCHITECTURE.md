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
