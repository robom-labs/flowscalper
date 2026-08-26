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
- [x] Changing Run clears stale PAPER-entry notices and the previous focused-position state.

## H. UI

- [ ] Professional Korean dark dashboard.
- [ ] Scanner, chart, current trade and event log work.
- [ ] Entry/TP/SL lines are visible.
- [ ] History, replay, performance, risk and system pages exist.
- [ ] Gross/net PnL, fees, slippage and drawdown are visible.
- [ ] BASE and STRESS results are distinguishable.
- [ ] 선택 종목의 열린 PAPER 포지션은 차트 위에 방향, 전략, 비용 프로필, entry, TP1과 SL을 표시한다.
- [ ] 모든 열린 PAPER 포지션 목록에서 종목을 선택할 수 있고 자연 종료 뒤 목록과 차트 표시가 제거된다.
- [ ] 전략 A~K는 현재 감시상태, 최근 조건 대기 이유와 평가경로 수를 표시해 정상 대기와 기술 오류를 구분한다. 퇴역 전략은 보존 상태와 재활성화 잠금을 표시한다.

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
- [ ] Ten Strategy rows and twenty independent BASE/STRESS accounts are connected.
- [ ] ACTIVE, SHADOW and OFF use beginner-readable meanings without `기록만 하기`.
- [ ] Home separates ten BASE-account totals from the Shared Capital Benchmark.
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
- [ ] Wide 50+ and deep 12 remain bounded; rotation protects pin/open/pending symbols and appends snapshots.
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
- [ ] Host wall-clock correction does not create false venue lag; planned rotation locks before prepare and automatically recovers only after fresh valid depth.
- [ ] DEMO never inherits LIVE lag or universe counts and cannot be mistaken for LIVE on phone, tablet or desktop.
- [ ] READY start controls and LIVE PAPER observation state remain visible at phone width.
- [ ] Completed trade replay exposes entry and exit ledger transitions even when the market-event post-roll ends early.
- [ ] Actual browser control evidence distinguishes deterministic DEMO replay from a naturally observed LIVE PAPER fill.
- [ ] 실행호가 p95, 체결 p95와 wide scanner p95를 분리하고 wide scanner를 진입판정 지연으로 표시하지 않는다.
- [ ] 500ms보다 늦은 aggregate trade는 archive에는 보존하되 candle·feature·전략입력에는 사용하지 않으며 신선한 체결 뒤에만 해당 종목이 회복된다.

## N. Strategy Governor and lifecycle

- [ ] RESEARCH, SHADOW, CHALLENGER, ACTIVE, QUARANTINED and RETIRED are distinct from the three execution modes.
- [ ] Missing OOS lower bound, robustness, DSR/PBO or natural LIVE_PUBLIC samples blocks automatic promotion.
- [ ] Minimum-sample strategies cannot be performance-quarantined, and performance quarantine requires two degraded full/recent OOS evaluations.
- [ ] Technical corruption can quarantine immediately without closing or abandoning an existing PAPER position.
- [ ] Champion replacement is atomic and limited; manual lock wins every conflict.
- [ ] Actor, reason, evidence period, revision and rollback target survive restart in checksum-verified storage.
- [ ] Strategy UI exposes lifecycle, exact reason, remaining evidence, current champion, history and confirmed rollback.

## O. Canonical multi-timeframe research

- [ ] The one timeframe registry covers 1m, 3m, 5m, 15m, 30m, 1h and 4h through API, UI, history and tests.
- [ ] Canonical candles contain OHLCV, quote volume, trade count and taker flow; duplicates, late events and incomplete candles cannot change completed features.
- [ ] MICRO_SCALP, FAST_INTRADAY and INTRADAY_SWING use separate maximum holds and horizon-specific purge·embargo.
- [ ] ORIGINAL and MECHANICAL_MIRROR share timestamps/information sets and enter as a pair; HYPOTHESIS_REVERSE has independent conditions.
- [ ] Actual opposite-side bid/ask and unchanged BASE/STRESS costs are applied before expectancy and Profit Factor.
- [ ] All 180 preregistered keys remain in the result and all 120 promotable hypotheses count toward PBO/DSR even with zero trades.
- [ ] Dataset, code, config and final result hashes are stored in JSON; HTML derives from the same result.
- [ ] Research output cannot change Registry, mode, lifecycle, account, position, threshold or actual-order safety state.
- [ ] A candidate remains `NOT_PROVEN` unless OOS sample, BASE/STRESS, bootstrap, DSR, PBO and robustness gates all pass.

## P. Dynamic registry and evidence truth

- [ ] Production strategy/account totals are derived from the Registry payload and a synthetic added strategy passes UI and backend tests.
- [ ] Retired or removed strategy ledgers are preserved and never hidden by a count migration.
- [ ] Browser, network, 30m, 6h and 24h evidence are independently marked PASS, FAIL, NOT_RUN or BLOCKED.
- [ ] Profitability remains `NOT_PROVEN` unless current-version natural LIVE_PUBLIC evidence meets its preregistered gate.

## Q. Observable and cancellable stored replay

- [ ] Strategy replay POST returns 202 with an operation ID and never holds the browser request until a large Run finishes.
- [ ] Requested, preparing, processing, cancelling, completed, retryable failure, blocked failure and cancelled states are ordered and tested.
- [ ] Duplicate scope is idempotent, conflicting scope returns `REPLAY_BUSY`, timeout is explicit and cancellation terminates child processing.
- [ ] Browser refresh reattaches to an active operation and shows Run, symbol, estimated events, elapsed time, PAPER safety and a working cancel button.
- [ ] Run list and recent-candle preview render without waiting for replay result history or archive event bodies.
- [ ] Interactive timeline reads a bounded event/candle window; full strategy validation still uses all selected stored events.
- [ ] Actual desktop, tablet and mobile screens show history, preview, progress and cancellation without console errors.

## R. Revisioned PAPER entry intent and nonblocking history startup

- [x] User `ENTRY_ENABLED`·`ENTRY_PAUSED` intent is separate from automatic safety locking and survives recovery with its revision.
- [x] Pause and resume use expected-revision CAS and idempotency keys; stale or conflicting requests fail with the current state.
- [x] Intent transitions persist actor, reason, revision and timestamp as immutable audit incidents.
- [x] Automatic safety wait remains fail-closed and the actual UI cannot present a user resume action as safety recovery.
- [x] Existing-Run trade-cache preparation runs after HTTP startup and does not block the listening port.
- [x] Replay lists use a query-only read path and return the latest result per source Run while full stored results remain intact.
- [x] The actual browser shows 43 current-version trades, 79 replay Runs, a 100-event precise timeline and working play/pause controls without console errors.
- [ ] Six-hour and 24-hour post-change soaks are completed.
- [ ] The active multi-gigabyte ledger full `quick_check` is rerun without disrupting the live writer.

Wave 47에서 작동 중인 2.798GB writer와 `sqlite3 -readonly` 전수검사를 동시에 실행하자 437초 동안 결과 없이 queue가 4,096까지 포화되고 drop 9,736이 발생했다. 따라서 이 항목은 미충족이며, 같은 동시검사를 반복하지 않고 닫힌 snapshot 또는 maintenance 절차가 마련될 때까지 `NOT_COMPLETED`다.

## S. Strategy survival, outcome timing and history truth

- [x] No strategy is default ACTIVE unless formal cost-adjusted evidence passes every promotion gate; an empty shared-account champion is a valid safe state.
- [x] B/C/F/G/I/J remain independent SHADOW accounts and A/D/E/H/K remain immutable-history RETIRED accounts after restart.
- [x] The automatic Governor evaluates on a fixed interval, counts only new natural samples and cannot promote without formal OOS evidence.
- [x] New PAPER trades persist TP1, TP2 and actual STOP timestamps and elapsed durations through recovery, API, analytics and UI.
- [x] Past rows with no milestone fields show `과거 기록 없음`, not zero seconds or an inferred loss event.
- [x] History opens all PAPER accounts and prior strategy versions by default while current-version performance remains isolated.
- [x] Recovery cannot overwrite a persisted trade's strategy version when the same completed trade is present in memory.
- [x] Focused replay visibly advances across long idle gaps while preserving source timestamps, event order and final reconciliation.
- [x] Actual orders, private API, auth, API keys, secrets and wallet paths remain zero.
- [ ] Current-version natural LIVE_PUBLIC samples meet the preregistered profitability gate.
- [ ] Six-hour and 24-hour post-change soaks and a non-disruptive active-ledger full `quick_check` are completed.

## T. Current and peak process-memory truth

- [x] The backend reports current resident memory and lifetime peak resident memory as separate fields with explicit source labels.
- [x] A regression test prevents a peak RSS counter from being presented as current RSS.
- [x] The advanced Korean diagnostics view labels and displays current RSS and peak RSS separately.
- [x] Soak memory growth uses current RSS and preserves peak growth as a separate diagnostic.
- [x] The restarted actual service current RSS is compared with the operating-system process RSS in the same observation window.
- [ ] Six-hour and 24-hour post-change memory stability is measured for the implementation commit.
