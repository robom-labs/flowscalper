# 13. Acceptance Criteria

Codex must produce an acceptance matrix and evidence for every item.

## A. Startup and access

- [ ] Fresh setup instructions exist for Windows and macOS.
- [ ] No exchange/OpenAI/TradingView account is requested.
- [ ] Fixture mode starts offline.
- [ ] Live public-data mode starts without credentials when the venue is reachable.
- [ ] Backend serves the built frontend locally and opens a browser.

## B. Honest runtime state

- [ ] LIVE is shown only after verified real events.
- [ ] PAPER is permanently visible.
- [ ] Offline fixture cannot be mistaken for live data.
- [ ] Venue and Run ID are visible.
- [ ] Starting equity is 1,000 USDT by default.

## C. Market data

- [ ] Active USDT perpetual metadata is dynamically discovered.
- [ ] Dozens of eligible symbols can be wide-scanned.
- [ ] Deep books are sequence-valid or marked stale.
- [ ] Gap/reconnect/resync counters are visible.
- [ ] No venue mixing inside a Run.

## D. Strategy and planning

- [ ] Strategy A long/short implemented.
- [ ] Strategy B long/short implemented.
- [ ] Candidate explanations and rejection reason codes exist.
- [ ] Stop and target are calculated before entry.
- [ ] No fake probability appears during cold start.
- [ ] Cost and risk gates can reject an otherwise valid setup.

## E. Paper execution

- [ ] Entry consumes asks for long and bids for short.
- [ ] Exit consumes bids for long and asks for short.
- [ ] Latency is applied.
- [ ] Multi-level and partial fills work.
- [ ] TP/SL are created for the filled quantity.
- [ ] Fees and slippage reduce equity.
- [ ] Ambiguous TP/SL ordering is pessimistic.
- [ ] Actual simulated fill differs from planned price when the book requires it.

## F. Position management

- [ ] No fixed 120-second forced exit.
- [ ] Position may remain open past 120 seconds with healthy thesis.
- [ ] Edge decay can close before TP/SL.
- [ ] Initial stop never widens.
- [ ] Emergency stale policy works.
- [ ] Trade completion reconciles quantity and contingent paper orders.

## G. Risk

- [ ] Risk-based quantity calculation works with tick/step rounding.
- [ ] Max one concurrent position.
- [ ] Daily, weekly and drawdown locks work.
- [ ] Cooldowns work.
- [ ] No averaging down, martingale or pyramiding.
- [ ] Changing critical assumptions starts a new Run.

## H. UI

- [ ] Professional Korean dark dashboard.
- [ ] Scanner, chart, current trade and event log work.
- [ ] Entry/TP/SL lines are visible.
- [ ] History, replay, performance, risk and system pages exist.
- [ ] Gross/net PnL, fees, slippage and drawdown are visible.
- [ ] BASE and STRESS results are distinguishable.

## I. Persistence and recovery

- [ ] SQLite state persists.
- [ ] Market/replay data is stored with retention.
- [ ] A completed trade replays deterministically.
- [ ] Restart recovery works in tested lifecycle states.
- [ ] Reset creates a new Run and preserves old history.
- [ ] Disk pressure pauses entry safely.

## J. Safety and quality

- [ ] No functioning real-order/private API path exists.
- [ ] No secret input fields exist.
- [ ] Localhost-only by default.
- [ ] Unit/integration/e2e tests pass.
- [ ] Lint, typecheck and production build pass.
- [ ] Dependency/license notices exist.
- [ ] `FINAL_UPGRADE_EVIDENCE.md` exists with actual results.
- [ ] Git working tree is clean.

## K. Phase 02 asynchronous control and Strategy League UI

- [ ] Start LIVE, demo and new Run submit immediately as `202 ControlOperation`.
- [ ] Duplicate, conflict, ordered stage, cancel, retryable and blocked outcomes are tested.
- [ ] Cancellation leaves no unregistered supervisor and never lies about LIVE state.
- [ ] Nine Strategy rows and eighteen independent BASE/STRESS accounts are connected.
- [ ] ACTIVE, SHADOW and OFF use beginner-readable meanings without `기록만 하기`.
- [ ] Home separates nine BASE-account totals from the Shared Capital Benchmark.
- [ ] League positions default to BASE and expose no real buy/sell action.
- [ ] Scanner order, row size and chart dimensions remain stable while data and drawers change.
- [ ] MA, EMA, VWAP, Bollinger, RSI and MACD are selectable without changing strategy rules.
- [ ] Same-selection data uses incremental update; selection changes use bounded full setData.
- [ ] Crosshair, current-to-realtime and fullscreen return work in a real browser.
- [ ] Desktop, tablet and mobile have 48px controls, no root overflow and no runtime errors.
- [ ] Core and Browser GitHub Actions pass on the final GitHub main commit.

## L. Phase 03 market, position focus and trade replay

- [ ] Five compact navigation groups with market as default and no old user-facing League/advanced-terminal copy.
- [ ] Binance active USDT perpetual full catalog and Upbit KRW observation-only full catalog work without authentication.
- [ ] Default 3-minute candles count 200 with MA10, MA20 and volume; RSI/MACD add and remove real panes without resizing the chart.
- [ ] Wide 50+ and deep 20 remain bounded; rotation protects pin/open/pending symbols and appends snapshots.
- [ ] Strategy×symbol report withholds rank below 30 samples and includes expectancy, PF, costs, drawdown and sample status.
- [ ] Actual fills, not candidates or pending entries, trigger focus. BASE priority, focus lock, selector and 15-second closed review work.
- [ ] Focus shows actual entry, initial/current stop, TP1/TP2, quantities, planned loss, fee/slippage, net PnL, equity, stage and data health.
- [ ] Trade replay uses stored public events, hides future markers, bounds frames and preserves ordered 0.5x–80x playback.
- [ ] Desktop focus chart is at least 960px wide and root scroll is zero; tablet/mobile sheets do not change chart width.
- [ ] Actual order, private API, API Key, secret, wallet and manual buy/sell controls remain zero.

## M. Phase 03 latency and mobile truth hardening

- [ ] Active Run Parquet writes are Run-partitioned and replay remains compatible with stored earlier partitions.
- [ ] Trade coalescing preserves direction, quantity, notional and VWAP without lowering a strategy or fill threshold.
- [ ] Integrated public-market lag stays below the 1,500ms entry-lock threshold or fails closed; queue/drop/gap/fault evidence is recorded.
- [ ] DEMO never inherits LIVE lag or universe counts and cannot be mistaken for LIVE on phone, tablet or desktop.
- [ ] READY start controls and LIVE PAPER observation state remain visible at phone width.
- [ ] Completed trade replay exposes entry and exit ledger transitions even when the market-event post-roll ends early.
- [ ] Actual browser control evidence distinguishes deterministic DEMO replay from a naturally observed LIVE PAPER fill.
