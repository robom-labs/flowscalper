# PLANS.md — Execution Plan and Progress Source of Truth

> v0.2 업그레이드의 세부 실행계획과 현재 관찰 결과는 `UPGRADE_EXEC_PLAN.md`가 우선한다. 아래 0.1 Wave 기록은 완료된 기준선으로 보존한다.

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
- 2026-08-22: 최종 fixture 증거는 계획 가격과 실제 체결 가격을 분리한 진입·청산 주문/체결, 수수료·슬리피지 합계, 단조 증가 상태 전이를 동일 Run 원장에 보존한다.
- 2026-08-22: 거래의 `config_hash`는 예시 식별자가 아니라 소속 Run의 정규 설정 JSON SHA-256과 같아야 하며 테스트로 고정한다.
- 2026-08-22: ADR-002에 따라 v0.2 기본 상태를 READY로 바꾸고, fixture는 DEMO 전용 Run으로 격리하며, A/B/C/D Registry와 지속 공개 WebSocket supervisor를 공통 런타임에 연결한다.
- 2026-08-22: ADR-003에 따라 모든 적격 신호를 불변 CandidatePlan으로 고정한 뒤 지연된 실행가능 호가에서 main과 전략별 BASE·STRESS shadow를 동일하게 체결한다.
- 2026-08-22: ADR-004에 따라 공개시장 이벤트를 Run 범위 불변 원장에 배치 저장하고 동일 A/B/C/D·PAPER 런타임으로 checksum 리플레이하며, 전략별 기대값·PF·비용·낙폭·표본상태를 함께 계산한다.
- 2026-08-22: ADR-005에 따라 v0.2는 기존 SVG 관찰 차트를 Lightweight Charts 실제 candle·bid·ask·microprice로 교체하고, 일곱 한국어 화면을 같은 backend 원장·ReplayEngine·Strategy Registry에 연결한다.
- 2026-08-22: ADR-006에 따라 main·8개 shadow 실행계좌와 전략 설정을 checksum 검증 snapshot에서 복구하고, 공개지연 p95 1,500ms 초과·저장 실패·디스크 압박·복구 불일치를 UI로 우회할 수 없는 PAPER 신규진입 잠금으로 처리한다.
- 2026-08-22: ADR-007에 따라 wide 1초·deep 250ms·trade 수신을 분리하고, SQLite batch를 event loop 밖에서 저장하며, 대시보드 snapshot·차트 인스턴스·KST 표시를 각각 한 번의 안정적인 수명주기로 운영한다.
- 2026-08-22: ADR-007 보강에 따라 LIVE 대시보드의 SQLite 반복 조회를 제거하고 Run 시작 cache와 현재 메모리 거래를 결합해 WAL checkpoint 중에도 화면 snapshot이 멈추지 않게 한다.

## v0.2 upgrade progress

| Wave | Status | Validation | Blockers | Next action |
|---|---|---|---|---|
| Upgrade 00 | COMPLETE | 패키지 전체 완독, ZIP 안전검사 PASS, 0.1 기준선 test/lint/typecheck/build/security/network PASS | 기준선 E2E는 기존 8765 사용자 프로세스와 포트 충돌 | 완료 |
| Upgrade 01 | COMPLETE | 백엔드 60 PASS, 프런트 2 PASS, lint/typecheck/build/security PASS, fresh LIVE 1,000 USDT·손익·비용·거래 0과 DEMO 성과 격리 PASS | 없음 | 지속 supervisor와 캔들 구현 |
| Upgrade 02 | COMPLETE | 백엔드 63 PASS, frontend test/lint/typecheck/build PASS, 실제 Binance 50 wide·10 deep·5초 지속 수신 29,351 events, book 18,348·depth 287·trade 765, 10종목 1초봉 생성, reconnect 0·gap 0·drop 0·lag P95 91ms | 없음 | Strategy Registry와 shadow 계좌 구현 |
| Upgrade 03 | COMPLETE | 백엔드 71 PASS, ruff/mypy PASS, 실제 Binance LIVE에서 A/B/C/D 2,296회 평가·latest 80 경로 전부 보수적 REJECTED·가짜 TP 확률 0, 전략별 BASE/STRESS shadow 계좌 8개 격리 PASS | 없음 | 불변 계획·체결·포지션 연결 |
| Upgrade 04 | COMPLETE | 백엔드 75 PASS, frontend 2 PASS, ruff/mypy/ESLint/TypeScript/build/security PASS. 불변 plan·250/500ms 지연·실제 ask/bid·부분 진입·TP1/TP2·main 1개·shadow 격리·120초 초과 유지·edge decay·실시간 순손익 PASS. 실제 Binance 61,937 events, 평가 5,360회, 자연 적격신호·거래 0, reconnect/gap/drop 0, auth·실제주문 false | 없음 | 원장·리플레이·분석 연결 |
| Upgrade 05 | COMPLETE | 백엔드 81 PASS, frontend 2 PASS, lint/typecheck/build/security PASS. schema v3 migration·시장 이벤트 checksum·캔들·후보·main/shadow 실제 원장·HTTP replay/analytics PASS. 실제 Binance 50종목 21,620 events·53 candles 저장, 두 replay 21,620건·3,224 전략평가·적격/거래 0·checksum 일치, auth/실제주문 false | 없음 | 한국어 UI와 실제 차트 구현 |
| Upgrade 06 | COMPLETE | 백엔드 82 PASS, frontend 2 PASS, ruff/mypy/ESLint/TypeScript/build/security PASS, Playwright 데스크톱·태블릿·모바일 3 PASS·console/page error 0·48px controls·root overflow 0. 실제 candle·bid·ask·microprice와 entry·TP1·TP2·SL·체결 marker, A/B/C/D 제어, 거래원장, backend replay, 전략별 성과 화면 및 디자인 비교 PASS | 없음 | 복구·soak·보안 검증 |
| Upgrade 07 | COMPLETE | 백엔드 전체 92 PASS, frontend 2 PASS, targeted 복구·운영안전 11 PASS, ruff/mypy/ESLint/TypeScript/build/security/E2E 3 PASS. 실제 Binance 30분 3,120,256 events, reconnect 39, gap/resync/drop 0, queue max 2, memory +132.922MB, 임계 지연 표본 171개 fail-open 0, 종료 supervisor lock·runtime pause 유지 | 6시간·24시간 soak는 NOT_RUN | 최종 증거와 릴리스 |
| Upgrade 08 | COMPLETE | macOS root launcher READY 1,000 USDT·성과 0 실제 부팅 PASS. 릴리스 234 entries·10,934,450 bytes, `unzip -t` PASS, 내부 checksum 233개 전수 PASS, 새 압축해제본 frozen 설치 후 backend 92·frontend 2 PASS, One Touch 복사본 SHA-256 일치 | Windows 실기기 실행 NOT_RUN | 완료 |
| Upgrade 09 | COMPLETE | backend 96 PASS, frontend 3 PASS, lint/typecheck/build/security PASS. 실제 Binance wide 50·deep 10에서 625.957초·129,849 events·604 candles, 38회 UI API HTTP 200·최대 120.584ms, 최종 실행 경로 p95 71ms, queue/gap/drop/reconnect/fault 0, KST 차이 5ms·차트 높이·재생성 최적화 | 현재 in-app browser 보안 정책 확인 불가로 수정 후 screenshot 재캡처 BLOCKED | 완료 |

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
| 08 | COMPLETE | 9398f00 | Backend 59 PASS including fixture order/fill/accounting chronology and Run config-hash binding; macOS setup PASS; fixture restart recovery PASS; final live app Binance 524 crypto eligible, 50 wide/1 deep, verified LIVE p95 6ms; final network smoke 527 exchange-eligible, raw-first-event p95 8231.569ms; Playwright 3 PASS; security/audits PASS; release/evidence generated | Windows execution NOT_RUN on macOS; sustained 50 wide/10 deep NOT_RUN | 완료 |
