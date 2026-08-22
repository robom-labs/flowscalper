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
- [ ] `FINAL_EVIDENCE.md` exists with actual results.
- [ ] Git working tree is clean.
