# 03. Market Data and Venues

## 3.1 Venue policy

Primary venue: `BINANCE_USDM_PUBLIC`.

Fallback venue: `BYBIT_LINEAR_PUBLIC`.

A Run is permanently associated with one venue. Do not combine signals, candles, fills or PnL from different venues inside one Run.

## 3.2 Authentication

Only public market-data endpoints are used. Default startup requires no account and no API key.

Do not call:

- account endpoints;
- order endpoints;
- user data streams;
- wallet endpoints;
- transfer/withdrawal endpoints.

## 3.3 Binance requirements

Use the current official USDⓈ-M documentation, including the 2026 WebSocket base URL split/migration.

Implement configurable endpoint families rather than assuming every stream belongs to one path. The official mapping currently separates high-frequency public book data and regular market data.

Required information:

- exchange/instrument metadata;
- active contracts;
- filters, tick size and step size;
- 24-hour ticker/turnover;
- book ticker;
- aggregate/recent trades;
- partial or diff depth;
- optional mark price for display/risk diagnostics.

Connection requirements:

- support combined and live subscribe/unsubscribe methods;
- shard streams conservatively even when the official maximum is higher;
- rotate before the documented connection lifetime;
- implement ping/pong and exponential backoff with jitter;
- avoid reconnect storms;
- resubscribe idempotently;
- record sequence gaps and resync counts.

### Local order book

For deep-scan symbols, prefer a sequence-valid local book:

1. connect and buffer depth events;
2. obtain a REST snapshot;
3. discard obsolete updates;
4. apply the first bridging event;
5. require continuity with previous update IDs;
6. on any gap, mark stale and rebuild from a new snapshot.

Never keep trading from a book with unknown continuity.

## 3.4 Bybit fallback requirements

Use the official V5 public linear endpoint.

- Public topics require no authentication.
- Process initial snapshot then delta updates.
- A newly received snapshot resets the local book.
- Use public trade, ticker and orderbook topics.
- Instrument metadata is paginated and must be fully traversed.

Bybit data and Binance data must remain separate.

## 3.5 Wide and deep scanning

### Wide scan

Default 50 eligible symbols.

Use relatively light streams/metrics:

- best bid/ask;
- recent trades;
- short return/volume activity;
- 24-hour turnover;
- data freshness;
- spread.

### Deep scan

Default 10 symbols selected with hysteresis.

Use:

- top 10–20 depth or reconstructed diff depth;
- OFI;
- multi-level imbalance;
- refill/cancel metrics;
- microprice;
- price-response efficiency.

Avoid rapid subscription thrashing. A symbol should remain deep-scanned for a configurable minimum dwell period unless its data becomes invalid.

## 3.6 Candles

Build from live public trades:

- 1 second;
- 5 seconds;
- 15 seconds;
- 1 minute.

Persist both trade-time and receive-time based diagnostics. Do not synthesize missing trades as zero-price movement without a data-quality flag.

## 3.7 Data freshness

Suggested initial safety thresholds, configurable and adaptive:

- best bid/ask stale: > 1,000 ms;
- deep book stale: > 1,000 ms;
- trade tape stale: based on symbol activity, with an absolute ceiling;
- system processing lag warning: > 250 ms sustained;
- entry lock: > 500 ms critical lag or venue-specific threshold.

Low-activity symbols should be excluded instead of treating infrequent trades as a technical failure.

## 3.8 Venue failover

When no position is open:

- the application may offer to start a new Run on the fallback venue after repeated primary failure;
- automatic failover must be explicit in the UI and event log.

When a paper position is open:

- never switch price source;
- pause strategy entries;
- attempt same-venue reconnect;
- on recovery, process the first fresh executable quote conservatively;
- if a safety exit is required, use the Run's configured data-gap policy and clearly mark it.

## 3.9 Network diagnostics

Provide a diagnostics page and CLI command that reports:

- DNS resolution;
- REST metadata response;
- WebSocket handshake;
- first book/ticker/trade event;
- average/p95 event delay;
- universe symbol count;
- endpoint used;
- failure reason without exposing private data.
