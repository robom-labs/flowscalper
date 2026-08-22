# 08. Risk Management

## 8.1 Default paper account

```text
initial_equity: 1,000 USDT
risk_per_trade: 0.10%
initial risk budget: approximately 1 USDT
max_open_positions: 1
max_daily_trades: 12
daily_loss_limit: 5 USDT
weekly_loss_limit: 15 USDT
maximum_drawdown_lock: 3%
```

These are conservative research defaults and must be configurable per new Run.

## 8.2 Exposure

Default BASE profile:

- simulated leverage: 1x;
- maximum gross notional: 100% of equity, with a recommended safer default of 50%;
- actual quantity still constrained by risk budget and book depth.

Optional comparison profile:

- simulated leverage: 2x;
- same dollar risk budget;
- separate results and clear labeling;
- no impact on BASE decisions.

Leverage must not multiply the planned loss. The position-sizing formula keeps the dollar risk fixed.

## 8.3 Hard prohibitions

- averaging down;
- martingale;
- pyramiding;
- adding to losing positions;
- widening stop;
- automatically increasing risk after wins;
- trading to meet a quota;
- simultaneous correlated positions in v0.1.

## 8.4 Portfolio gates

Reject a candidate when:

- another position is open;
- daily/weekly/drawdown lock is active;
- data health is degraded;
- system processing lag is critical;
- the symbol/strategy is cooling down;
- planned risk exceeds budget;
- depth exposure cap is exceeded;
- cost-adjusted reward criteria fail;
- the Run is paused or faulted.

## 8.5 Loss pauses

Suggested initial behavior:

- same symbol and strategy: 2 consecutive losses → 2-hour pause;
- total portfolio: 3 consecutive losses → 60-minute pause;
- daily loss limit → no new entries until the next configured UTC/local trading day boundary or manual new Run;
- weekly loss limit → Run locked for review;
- drawdown lock → Run cannot resume without creating/confirming a new experiment decision.

## 8.6 Correlation

Only one position is allowed initially. Still record cross-symbol market context:

- BTC and ETH shock state;
- market-wide breadth;
- candidate correlation to recent benchmark movement.

Do not use a market-wide drop as a reason to enter multiple similar shorts simultaneously.

## 8.7 Cost gates

Initial configurable minimums:

- net reward/risk ≥ 1.20;
- total expected cost ≤ 30% of gross target;
- p95 expected slippage ≤ 15% of stop distance;
- expected executable depth supports size;
- target not immediately blocked by stronger opposing liquidity.

These are initial research gates and should be recorded with every Run.

## 8.8 Risk-control UI

The user may:

- pause/resume paper entries;
- close the current paper position at a simulated executable price;
- start a new Run with a different safe configuration;
- exclude symbols;
- inspect locks.

Risk-critical configuration changes create a new Run or require restart/confirmation. Do not mutate historical assumptions inside an active Run.

## 8.9 Auditability

Every rejection or lock must have:

- reason code;
- timestamp;
- relevant measured values;
- configured threshold;
- source component.
