# 08. Risk Management

## 8.1 Default paper account

```text
initial_equity: 1,000 USDT
risk_per_trade: 0.10%
initial risk budget: approximately 1 USDT
max_open_positions: 1
max_daily_trades: disabled for continuous PAPER research
daily_loss_limit: disabled for continuous PAPER research
weekly_loss_limit: disabled for continuous PAPER research
consecutive_loss_cooldown: disabled for continuous PAPER research
maximum_drawdown_lock: 3%
selected_margin_leverage: 10x default, selectable up to 100x
```

The disabled period quotas prevent a credential-free PAPER experiment from silently stopping because
of elapsed calendar periods. They do not relax position sizing, book depth, drawdown, persistence or
market-data safety.

## 8.2 Exposure

Default PAPER profile:

- selected margin leverage: 10x;
- user-selectable margin leverage: 1x, 2x, 3x, 5x, 10x, 20x, 25x, 50x, 75x or 100x;
- maximum aggregate gross notional follows the selected margin leverage;
- actual quantity still constrained by risk budget and book depth.

The selected value is margin leverage, not an instruction to force every order to the maximum
notional. `margin_used = actual entry notional / selected leverage`. Fees, slippage and PnL use the
actual filled notional. The position-sizing formula keeps the dollar risk fixed, and BASE/STRESS keep
their separate cost assumptions. Open positions preserve their entry-time leverage when the setting
changes; only new entries use the new value.

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
- drawdown lock is active;
- data health is degraded;
- system processing lag is critical;
- planned risk exceeds budget;
- depth exposure cap is exceeded;
- cost-adjusted reward criteria fail;
- the Run is in deployment maintenance, recovery or faulted state.

## 8.5 Continuous PAPER research and safety waits

- Daily trade count, daily loss, weekly loss and consecutive-loss cooldown do not lock new entries.
- Daily and weekly counters remain observable for research but are not entry gates.
- Drawdown remains a separate capital-integrity gate.
- Data lag, invalid books, persistence faults, recovery revalidation and insufficient executable depth
  remain fail-closed. These waits protect the validity of the simulated fill and resume automatically
  when their recovery contract passes.
- A routine user pause is not exposed in the normal dashboard. An ordinary legacy pause is cleared on
  the next normal process restart. A deployment-maintenance pause remains explicit and must be released
  by the deployment workflow.

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

- close the current paper position at a simulated executable price;
- start a new Run with a different safe configuration;
- select 1x through 100x PAPER margin leverage for new entries;
- exclude symbols;
- inspect locks.

Leverage changes use revision-checked configuration, persist globally and apply only to new entries.
Do not mutate the leverage or cost record of an existing position or completed trade.

## 8.9 Auditability

Every rejection or lock must have:

- reason code;
- timestamp;
- relevant measured values;
- configured threshold;
- source component.
