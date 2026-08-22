# 15. Failure and Recovery

## 15.1 Failure principle

Fail closed: uncertainty blocks new entries. Do not continue trading from an invalid book, stale data, corrupted state or unknown venue connection.

## 15.2 Failure matrix

### Public WebSocket disconnect

- mark connection reconnecting;
- lock new entries for affected venue/symbols;
- keep last state for display as stale;
- reconnect with backoff/jitter;
- resubscribe and rebuild books;
- do not mark healthy until fresh sequence-valid data arrives.

### Sequence gap

- mark symbol stale immediately;
- remove from candidate ranking;
- rebuild local book from a new snapshot;
- increment incident counter.

### REST metadata failure

- retain previously validated metadata only for display/reconnect grace;
- do not activate unknown/new symbols;
- retry conservatively.

### Processing overload

- protect connection heartbeat and state integrity;
- reduce deep-scan symbols;
- throttle UI broadcasts;
- if lag remains critical, pause entries.

### Persistence failure

- stop new entries;
- attempt safe flush/recovery;
- display critical incident;
- do not create paper trades that cannot be audited.

### Disk pressure

- warn early;
- enforce retention;
- protect trade/replay windows;
- lock entries before disk full.

### Browser/UI disconnect

- backend continues paper management;
- UI reconnects and receives current snapshot;
- no trade decision depends on browser availability.

## 15.3 Restart recovery

At startup:

1. load latest non-finalized Run;
2. replay persisted state transitions;
3. inspect any open paper position/order state;
4. verify same venue connection and instrument metadata;
5. reconcile against saved market state and new fresh quote;
6. resume or conservatively close according to recovery policy;
7. record recovery incident.

Because there are no real orders, recovery concerns the internal paper state only. Still test all lifecycle states.

## 15.4 Data gap with open paper position

- preserve planned TP/SL;
- do not switch venue;
- upon first fresh quote, determine whether either boundary would conservatively have been crossed during the unknown interval;
- if unknowable, apply a pessimistic configured gap policy and flag the trade;
- exclude or separately report gap-affected trades in research metrics.

## 15.5 Clock issues

- use monotonic clock for latency and timers;
- detect UTC wall-clock jumps;
- record venue/local timestamp skew;
- critical skew locks entries.

## 15.6 Circuit breaker

Global PAUSED/FAULTED state must trigger on:

- real-order invariant violation;
- persistent corrupt state;
- critical processing lag;
- repeated data gaps;
- daily/weekly/drawdown limit;
- storage failure;
- unsupported schema/protocol change.

Recovery requires satisfying a deterministic health check, not merely a UI toggle.

## 15.7 v0.2 implemented recovery contract

- SQLite snapshot schema 1 stores the complete main and eight strategy/profile shadow execution accounts, immutable plans, fills, protection, remaining TP quantities, pending exits, risk state and completed PAPER trades.
- Recovery accepts only a checksum-valid snapshot whose Run, venue, Strategy Registry account set, cost profiles and quantity invariants match the active Run.
- Latest append-only completed trades override an older open-position snapshot from a crash window and rebuild realized equity, peak equity, drawdown and trade counts.
- A recovered LIVE Run starts paused with `ENTRY_LOCK_RECOVERY_REVALIDATION`. Its position or pending-entry symbol is pinned into both wide and deep subscriptions, and the lock clears only after that exact symbol receives a fresh sequence-valid book on the same venue.
- An active recovered lifecycle never fails over to another venue. If the original venue or recovered symbol is unavailable, the Run and PAPER state remain preserved and entry-locked.
- A corrupt checksum or invalid account state starts the UI in READY fail-closed state with no new Run or fixture trade.
- The storage guard checks the ledger volume at most once per second. Below 2GiB or 5% free by default, LIVE entries remain locked while existing PAPER position management stays independent of the browser.
- A persistence write error faults the main risk state, keeps retry buffers bounded and cannot be cleared with the UI resume control.
- CPU, process memory, thread count, uptime and disk figures on the System diagnostics screen come from the local process and filesystem rather than fixture constants.
- Rolling public-event lag p95 above 1,500ms sets `CRITICAL_MARKET_LAG_ENTRY_LOCK` in both supervisor telemetry and the PAPER runtime. A fresh sequence-valid depth can clear the health flag after p95 recovery, but an automatically paused runtime still requires an explicit safe resume.
