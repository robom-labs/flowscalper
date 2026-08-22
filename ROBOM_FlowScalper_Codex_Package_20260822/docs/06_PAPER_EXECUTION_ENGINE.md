# 06. Paper Execution Engine

## 6.1 Purpose

Simulate realistic execution against live public order-book data without sending any real order. The simulator must be intentionally conservative so that displayed performance is not inflated.

## 6.2 Paper order types

Initial supported intents:

- `MARKETABLE_LIMIT_IOC_ENTRY`
- `REDUCE_ONLY_TAKE_PROFIT`
- `REDUCE_ONLY_STOP_EXIT`
- `REDUCE_ONLY_EDGE_DECAY_EXIT`
- `REDUCE_ONLY_EMERGENCY_EXIT`

These are internal simulation types. They do not map to private exchange endpoints in v0.1.

## 6.3 Entry workflow

1. Strategy emits a fully specified candidate.
2. Risk manager validates account/run limits.
3. Paper execution captures the order-book version and local timestamp.
4. Apply configured decision-to-arrival latency.
5. Retrieve the first sequence-valid book at or after arrival.
6. Calculate a maximum acceptable price.
7. Consume executable depth level by level.
8. Stop when desired quantity is filled or price limit is reached.
9. Cancel unfilled remainder as IOC.
10. Recalculate weighted average fill, fees, slippage and actual risk.
11. Reject/flatten if the partial fill makes protection or minimum-size rules invalid.
12. Create simulated TP and SL for the filled quantity.

## 6.4 Price-side rules

Long entry consumes asks.

Short entry consumes bids.

Long exit consumes bids.

Short exit consumes asks.

Never use mid or last as an executable fill when a valid book is available.

## 6.5 Latency profiles

Default paper portfolios may share signals but use different execution assumptions.

### BASE

- decision-to-arrival latency: 250 ms;
- cancel latency: 150 ms;
- conservative configured taker fee;
- measured book-depth consumption.

### STRESS

- decision-to-arrival latency: 500 ms or 1,000 ms;
- cancel latency: 300 ms;
- fee/slippage multiplier: 2x;
- pessimistic ambiguity.

Latency values are assumptions until measured and must be labeled as such.

## 6.6 Marketable-limit price cap

Initial cap:

```text
max_price_deviation = min(
    configured_tick_limit,
    initial_stop_distance × configured_fraction,
    venue/symbol p95 observed short-horizon slippage cap
)
```

If no observed slippage distribution exists, use a conservative configuration and label it `ASSUMED`.

## 6.7 Partial fills

Support:

- zero fill;
- partial fill;
- full fill.

For a partial fill:

- cancel remainder;
- recalculate position-level risk;
- create TP/SL for filled quantity only;
- reject tiny dust quantities that cannot be represented under instrument steps;
- record `PARTIAL_FILL` and fill ratio.

## 6.8 Fee model

No account-specific fee is available without authentication. Use a clearly labeled conservative default that is configurable per Run.

Suggested example defaults:

```text
entry_fee_bps: 6.0
exit_fee_bps: 6.0
additional_safety_bps: 1.0
```

These values are assumptions, not a claim about a venue's current user fee. The UI must show the configured values.

Calculate fees on actual filled notional. Include any configured contract multiplier.

## 6.9 Slippage decomposition

Record separately:

- signal-to-arrival move;
- depth-walk impact;
- trigger-to-exit move;
- model safety buffer;
- total entry slippage;
- total exit slippage.

## 6.10 Take-profit simulation

For a long position:

- a TP becomes executable only when best bid reaches the target;
- fill against bids, respecting available quantity and latency assumptions;
- support partial TP fill only if the initial product design retains it; v0.1 should prefer all-or-cancel/full-position exit behavior to reduce complexity.

For a short, use asks symmetrically.

The target displayed on the chart is the trigger/limit plan; actual paper fill may differ and must be recorded.

## 6.11 Stop simulation

For a long:

- trigger when best bid reaches or crosses stop;
- apply stop-processing latency;
- consume current bids as a marketable exit;
- record gap/slippage.

For a short, use asks symmetrically.

If the book is missing at trigger time, use the first valid same-venue book after recovery and mark `DATA_GAP_STOP_FILL`. Do not use another venue.

## 6.12 Event ordering

Use venue sequence, transaction time and receive order where reliable.

When TP and SL ordering cannot be determined:

- choose the worse outcome;
- record `AMBIGUOUS_ORDERING_PESSIMISTIC`;
- expose the count in analytics.

## 6.13 Position sizing

For linear contracts:

```text
risk_budget = equity × risk_per_trade
loss_per_unit = abs(entry - stop) + entry_fee_per_unit + stop_fee_per_unit + p95_exit_slippage_per_unit
raw_qty = risk_budget / loss_per_unit
qty = floor_to_step(min(raw_qty, exposure_cap_qty, depth_cap_qty))
```

Reject when:

- quantity is below minimum;
- planned loss exceeds budget after rounding;
- required notional exceeds exposure cap;
- order size exceeds configured percentage of executable depth;
- TP does not satisfy net reward criteria after actual expected fill.

## 6.14 State machine

Required states:

```text
SCANNING
CANDIDATE
ARMED
ENTRY_PENDING
PARTIALLY_FILLED
PROTECTION_PENDING
PROTECTED
MANAGING
EXIT_PENDING
RECONCILING
CLOSED
COOLDOWN
PAUSED
FAULTED
```

State transitions must be idempotent, persisted and covered by tests.

## 6.15 Accounting

Update paper equity only from finalized fills and fees. Maintain:

- cash/equity;
- realized PnL;
- unrealized PnL;
- gross PnL;
- fees;
- spread/depth slippage;
- peak equity;
- drawdown;
- risk consumed.

No funding charge is expected for most very short trades, but the data model should support funding if a paper position crosses a funding timestamp.
