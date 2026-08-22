# 12. Test Plan

## 12.1 Test pyramid

- unit tests for calculations and state transitions;
- property tests for precision/risk invariants;
- integration tests for adapters, event flow and persistence;
- deterministic replay tests;
- frontend component tests;
- Playwright end-to-end tests;
- optional network smoke tests separated from deterministic CI.

## 12.2 Market-data tests

Required cases:

- metadata pagination;
- active/inactive contract filtering;
- WebSocket subscribe/unsubscribe;
- snapshot then delta;
- out-of-order delta;
- duplicate delta;
- sequence gap;
- snapshot reset;
- stale ticker/book/trade;
- ping/pong and planned reconnect;
- rate-limit/backoff behavior;
- venue endpoint migration/configuration;
- provider failover only through a new Run.

## 12.3 Order-book tests

- sorted bid/ask invariants;
- crossed-book rejection;
- level add/update/remove;
- checksum or update-ID continuity where applicable;
- multiple levels and price precision;
- resync after gap;
- bounded memory.

## 12.4 Feature tests

- mid/spread;
- weighted imbalance;
- microprice;
- OFI;
- trade imbalance;
- refill/cancel metrics;
- realized volatility;
- compression;
- micro-VWAP;
- robust z-score/percentile;
- warmup behavior;
- no NaN/inf propagation.

## 12.5 Strategy tests

For both long and short:

- valid positive setup;
- continuation rather than absorption rejection;
- fleeting wall rejection;
- spread rejection;
- shock rejection;
- stale-data rejection;
- no structural stop rejection;
- inadequate net target rejection;
- deterministic reason codes.

## 12.6 Execution tests

- zero/full/partial fill;
- multi-level weighted fill;
- price-cap cancellation;
- latency changes fill;
- long/short correct book side;
- TP executable-side rule;
- SL trigger and gap slippage;
- ambiguous TP/SL pessimistic result;
- fee and slippage accounting;
- quantity rounding;
- dust rejection;
- depth-cap rejection.

## 12.7 Position-management tests

- stay open beyond 120 seconds while thesis valid;
- early edge-decay exit;
- initial stop never widens;
- profit protection;
- stale emergency;
- data gap and same-venue recovery;
- trade cannot close twice;
- all contingent simulated orders finalized.

## 12.8 Risk tests

- risk budget exactness;
- max one position;
- daily/weekly/drawdown locks;
- consecutive-loss cooldowns;
- no martingale/pyramiding;
- active Run configuration immutability;
- BASE and STRESS accounting separation.

## 12.9 Recovery tests

- crash after candidate;
- crash after partial fill;
- crash after protection creation;
- crash during exit;
- database reopen and reconciliation;
- corrupted snapshot safety pause;
- disk-full entry lock.

## 12.10 Real-trading prohibition tests

- forbidden private endpoint strings absent from runtime adapters;
- no credential settings/schema/UI fields;
- attempting a real mode raises;
- network mock asserts only public routes are called;
- UI contains no live-order control;
- package scan contains no withdrawal/transfer implementation.

## 12.11 Frontend tests

- permanent PAPER banner;
- data-source state honesty;
- scanner updates;
- chart renders entry/TP/SL;
- current position fields;
- rejection explanation;
- Run reset preservation confirmation;
- pause and paper close;
- responsive desktop widths;
- accessibility smoke checks.

## 12.12 Network smoke tests

Run separately and do not fail offline CI by default:

- DNS and REST metadata;
- public WebSocket handshake;
- receive book/ticker/trade;
- discover at least one eligible symbol;
- measure p50/p95 event latency;
- no credential headers/parameters.

Results: PASS, FAIL, or NOT_RUN with reason.

## 12.13 Performance tests

Initial targets on a reasonable desktop:

- 50 wide symbols and 10 deep symbols without sustained critical lag;
- UI update throttled independently from strategy event rate;
- bounded queue memory;
- feature processing p95 within configured budget;
- automatic capacity reduction when overloaded.
