# PLANS.md — Execution Plan and Progress Source of Truth

This file is the source of truth for long-horizon implementation. Codex must continuously update status, decisions, validation evidence, and remaining work.

## Global definition of done

The application runs locally without credentials, connects to a supported venue's real public market data, dynamically scans dozens of eligible USDT perpetual symbols, operates a 1,000 USDT paper account, simulates realistic fills from executable order-book depth, displays a polished Korean dashboard, persists/replays trades, and contains no usable real-order path.

## Wave 00 — Contract, architecture, scaffold

Deliverables:

- Read and reconcile all package documents.
- Create `docs/adr/` and initial ADRs.
- Create backend/frontend repository structure.
- Define typed domain models and interfaces.
- Implement runtime mode invariant and real-trading blocker.
- Create fixture market-data generator.
- Create baseline CI/local commands.
- Create first dashboard shell showing unmistakable PAPER state.

Exit gate:

- Repository installs.
- Unit-test skeleton passes.
- A test proves live order invocation is impossible.
- Fixture mode boots end-to-end.

## Wave 01 — Market data and symbol universe

Deliverables:

- Binance public REST metadata and ticker discovery.
- Binance current WebSocket endpoint split and stream routing.
- Connection sharding, ping/pong, 24-hour rotation, backoff and health.
- Local order-book reconstruction with gap detection/resync.
- Bybit public linear fallback adapter.
- Dynamic universe ranking and wide/deep scanner rotation.
- Locally built subminute candles.

Exit gate:

- Deterministic snapshot/delta tests pass.
- Recorded fixtures demonstrate gap recovery.
- Network smoke test can list eligible symbols when network is available.
- Venue mixing is prevented.

## Wave 02 — Feature engine and regime classifier

Deliverables:

- Mid, spread, depth, imbalance, microprice.
- OFI and aggressive trade imbalance at multiple windows.
- Refill/cancel metrics and price-response efficiency.
- Realized volatility, compression, efficiency ratio, micro-VWAP.
- TREND_UP, TREND_DOWN, RANGE, SHOCK, DEGRADED classification.
- Candidate ranking infrastructure.

Exit gate:

- Features are deterministic from fixtures.
- No NaN/inf propagation.
- Stale/degraded data cannot produce a trade candidate.

## Wave 03 — Strategy A and Strategy B

Deliverables:

- Liquidity sweep/absorption/range re-entry strategy.
- Compression/breakout/pullback/reacceleration strategy.
- Configurable rolling-percentile and robust-z-score thresholds.
- Candidate explanation and rejection reason codes.
- Cold-start `CALIBRATING` behavior.

Exit gate:

- Positive and negative fixture scenarios pass.
- Same input always produces same candidate decision.
- No candidate without structural stop and viable target.

## Wave 04 — Cost, risk and paper execution

Deliverables:

- Conservative configurable fee models.
- Entry/exit latency models.
- Marketable-limit IOC paper fills across depth.
- Partial fill and cancellation.
- TP/SL simulation using executable sides.
- Ambiguous event pessimism.
- 1,000 USDT paper portfolios: BASE and STRESS.
- Position sizing and loss limits.
- State machine and reconciliation.

Exit gate:

- Candidate-to-closed-trade flow passes integration tests.
- Fees/spread/slippage reconcile exactly.
- No unprotected simulated position state.
- Risk locks operate.

## Wave 05 — Adaptive position management

Deliverables:

- Position health model.
- Edge-decay exit.
- Profit-protection exit.
- No fixed 120-second exit.
- Emergency stale policy.
- Cooldowns and repeated-loss pauses.

Exit gate:

- Holding beyond 120 seconds is allowed while edge remains valid.
- Early exit occurs when entry thesis is invalidated.
- Initial stop never widens.

## Wave 06 — Dashboard and user workflow

Deliverables:

- Polished Korean dark dashboard.
- Scanner, chart, current trade, logs.
- History, replay, performance, risk, system pages.
- Run reset preserving history.
- Real-time server-to-browser updates.
- Responsive and accessible layout.

Exit gate:

- UI e2e tests pass.
- PAPER/LIVE data distinctions are always visible.
- Entry/TP/SL appear on the chart.
- Rejected signals are explainable.

## Wave 07 — Persistence, replay and analytics

Deliverables:

- SQLite transactional state.
- Parquet market/feature storage.
- DuckDB reports.
- Retention and disk-pressure safety.
- Event-driven replay.
- MAE/MFE, costs, drawdown, strategy/venue/regime metrics.

Exit gate:

- Restart recovery tests pass.
- A completed trade can be replayed deterministically.
- Run results remain immutable after reset.

## Wave 08 — Packaging, hardening and evidence

Deliverables:

- Windows setup/run scripts.
- macOS setup/run scripts.
- Localhost-only production server.
- Frontend static bundle served by backend.
- Offline fixture demo.
- Network diagnostics.
- Security scan and third-party notices.
- Full documentation.
- `FINAL_EVIDENCE.md`.

Exit gate:

- Fresh-environment instructions are complete.
- All automated validation passes or is honestly marked.
- No secrets and no live-order path exist.
- Git working tree clean.

## Decision log

Codex must append concise dated entries here or link ADRs when a material choice is made.

- 2026-08-22: ADR-001에 따라 FastAPI 단일 프로세스와 정적 React 번들, 세 가지 PAPER 전용 런타임 모드, 거래소별 Run 격리를 채택했다.
- 2026-08-22: Binance 2026 WebSocket `/public`·`/market` 분리와 Bybit V5 public linear snapshot/delta 계약을 공식 문서에서 재확인했다.
- 2026-08-22: Binance 24시간 통계에는 최우선 호가가 없음을 실제 응답에서 확인해 `/ticker/24hr`와 공개 `/ticker/bookTicker`를 심볼별로 결합한다.
- 2026-08-22: Wave 06 대시보드는 초기 snapshot 1회와 WebSocket 갱신을 공유하고, 차트는 별도 무거운 의존성 없이 메모이제이션된 SVG로 구성했다.
- 2026-08-22: Wave 07은 SQLite WAL 불변 원장을 사용하고, 시계열은 venue/date/symbol/hour/event_type Parquet으로 분리하며, DuckDB는 Parquet·거래 집계와 내보내기에만 사용한다.
- 2026-08-22: LIVE는 REST 메타데이터와 sequence-valid 공개 WebSocket 이벤트 후에만 표시하며, 연결 실패·임계 초과 지연은 UI 재개로 풀 수 없는 PAPER 진입 잠금으로 처리한다.
- 2026-08-22: v0.1 LIVE 부트스트랩은 50 wide book-ticker와 1 sequence-valid deep book만 검증하고, 50 wide/10 deep 지속 성능은 알려진 제한으로 남긴다.

## Progress log

Codex must maintain a table with Wave, status, last commit, validation result, blockers, and next action.

| Wave | Status | Last commit | Validation result | Blockers | Next action |
|---|---|---|---|---|---|
| 00 | COMPLETE | 88f9624 | Backend 5 PASS; Ruff PASS; mypy PASS; ESLint PASS; TypeScript PASS; Vitest 1 PASS; Vite build PASS; fixture API/static boot PASS | 없음 | 완료 |
| 01 | COMPLETE | 29f94f7 | Backend 16 PASS; recorded gap/resync PASS; Ruff/mypy/frontend/build/e2e PASS; network smoke PASS, Binance 527 eligible, REST + WS 2 events, credentials false | 없음 | 완료 |
| 02 | COMPLETE | d0ef16f | Backend 21 PASS; deterministic/finite feature and stale candidate gate PASS; Ruff/mypy/frontend/build/e2e PASS | 없음 | 완료 |
| 03 | COMPLETE | ee1cfb2 | Backend 29 PASS; Strategy A/B long/short, positive/negative, cost/no-lookahead/determinism PASS; Ruff/mypy/frontend/build/e2e PASS | 없음 | 완료 |
| 04 | COMPLETE | 1c237f1 | Backend 37 PASS; latency/IOC partial/full/multilevel/protection/fee/slippage/ambiguity/risk lock/end-to-end accounting PASS; Ruff/mypy/frontend/build/e2e PASS | 없음 | 완료 |
| 05 | COMPLETE | 207eac3 | Backend 44 PASS; >120s hold, persistent edge decay, profit protection, stop non-widening, same-venue stale/emergency, cooldown PASS; Ruff/mypy/frontend/build/e2e PASS | 없음 | 완료 |
| 06 | COMPLETE | 25cc2fa | Backend 45 PASS; Vitest 2 PASS; Playwright desktop/tablet/mobile 3 PASS; console error 0; permanent PAPER/FIXTURE, chart lines, rejected reason, 48px controls PASS | 없음 | 완료 |
| 07 | COMPLETE | de12d0e | Backend 55 PASS including 9 storage/replay tests; four lifecycle restart states, corrupt snapshot fail-closed, immutable Run/trade, Parquet retention/protection, DuckDB metrics, disk-pressure lock, deterministic replay/export PASS; frontend/build/e2e PASS | 없음 | 완료 |
| 08 | COMPLETE | Wave 08 commit | Backend 59 PASS; macOS setup PASS; fixture restart recovery PASS; live app Binance 696 eligible, 50 wide/1 deep, verified LIVE 41ms and later 8ms; network smoke 527 eligible, raw-first-event p95 9809.824ms; Playwright 3 PASS; security/audits PASS; release/evidence generated | Windows execution NOT_RUN on macOS; sustained 50 wide/10 deep NOT_RUN | 완료 |
