# 01. Product Requirements

## 1.1 Product name

**ROBOM FlowScalper**

## 1.2 Product purpose

A local research and paper-trading application that consumes real public cryptocurrency perpetual-futures market data, scans a dynamically selected multi-symbol universe, detects cost-aware microstructure setups, simulates realistic entry and exit execution, and presents every decision through a polished Korean dashboard.

The product is not a broker, exchange, wallet, investment adviser, or guaranteed-profit system.

## 1.3 Primary user outcome

The user launches the application without creating an account or entering credentials and immediately sees:

- whether real market data is connected;
- which venue is used;
- which symbols are monitored;
- current candidate setups and rejection reasons;
- a 1,000 USDT paper account;
- current paper position, entry, TP, SL and planned maximum loss;
- a live chart and event log;
- historical paper performance and replay.

## 1.4 Default mode

```text
Mode: LIVE_SHADOW_PAPER
Market data: real public venue data
Execution: internal simulation only
Initial equity: 1,000 USDT
Credentials: none
Real orders: impossible
```

## 1.5 Functional requirements

### FR-001 Credential-free startup

The application must not request a user ID, password, API key, secret, wallet address, or OpenAI key in the default product path.

### FR-002 Honest data-source state

The UI must distinguish:

- `LIVE / BINANCE_USDM`
- `LIVE / BYBIT_LINEAR`
- `RECONNECTING`
- `STALE`
- `FIXTURE / OFFLINE`
- `DISCONNECTED`

Fixture data must never be labeled LIVE.

### FR-003 Dynamic multi-symbol universe

The application must discover eligible contracts from public venue metadata and rank them by liquidity, cost and data quality. Default targets are 50 wide-scan symbols and 8–12 deep-scan symbols, subject to machine and connection health.

### FR-004 Paper portfolio

Create an immutable experiment Run with:

- starting equity;
- venue;
- strategy/config version;
- cost model;
- latency model;
- leverage scenario;
- start/end timestamps.

Resetting the paper balance creates a new Run and preserves prior data.

### FR-005 Strategy candidates

Implement two initial strategies:

1. Liquidity sweep / absorption / range re-entry reversal.
2. Volatility compression / breakout / pullback / reacceleration continuation.

### FR-006 Cost-aware pre-trade plan

A trade cannot enter unless the engine has computed and validated:

- entry side and price range;
- structural stop;
- selected take-profit;
- expected fees, spread and slippage;
- net reward and net risk;
- position size;
- maximum planned loss;
- reason codes and invalidation conditions.

### FR-007 Realistic paper fills

The paper engine must consume the executable side of the order book after latency and support partial fills, no-fill, price limits and conservative ambiguous ordering.

### FR-008 Adaptive holding

No fixed 120-second forced exit. The position may remain open while the entry thesis and remaining edge are healthy. The engine may exit before TP/SL when the thesis decays. Emergency stale handling must exist.

### FR-009 Clear dashboard

The dashboard must show real market data, paper state, entry/TP/SL, risk, fees, slippage, current rationale, rejection rationale, system health and replay.

### FR-010 Local persistence and replay

Every closed trade must be replayable from preserved market and decision events to the extent allowed by the configured retention policy.

## 1.6 Non-functional requirements

- Localhost-first security.
- Deterministic tests.
- Typed interfaces.
- Graceful reconnect and recovery.
- No silent data mixing between venues.
- No use of future information.
- No blocking calls on the market-data event loop.
- Backpressure and disk-pressure controls.
- Explainable reason codes.
- Korean-first UI, English technical logs optional.

## 1.7 Explicit exclusions

Version 0.1 does not include:

- real trading;
- exchange account connection;
- money deposit or withdrawal;
- copy trading;
- social trading;
- cloud-hosted multi-user accounts;
- GPT runtime decisions;
- TradingView alerts;
- guaranteed-profit claims;
- parameter auto-optimization directly deployed to a real account.

## 1.8 Success indicators

Product success for v0.1 means technical and research validity, not profit:

- uninterrupted public-data ingestion with recoverable gaps;
- realistic paper execution;
- no unprotected paper position state;
- no real-order path;
- exact fee/slippage reconciliation;
- honest calibration and uncertainty display;
- usable dashboard and replay;
- comprehensive automated tests.
