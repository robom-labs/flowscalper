# PLANS.md — Execution Plan and Progress Source of Truth

> 현재 제품 상태는 아래 Upgrade progress와 `FINAL_UPGRADE_EVIDENCE.md`를 우선한다. 완료된 초기 Wave는 현재 기능이 만들어진 순서와 수용 gate를 설명하는 구현 이력이며, 버전별 사용자 요약은 `CHANGELOG.md`를 사용한다.

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
- `FINAL_UPGRADE_EVIDENCE.md`.

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
- 2026-08-23: ADR-008에 따라 대용량 원장 replay 목록을 schema v4 O(1) 통계와 worker thread로 분리하고, 로그인 후 자동 복구 LaunchAgent와 비전문가용 고정 scanner·차트·이동평균선 홈을 채택했다.
- 2026-08-23: ADR-008 보강에 따라 지연 분위수 재정렬을 256표본 주기로 제한하되 단일 임계 지연은 즉시 fail-closed로 잠그고, 120초 피처는 동일 결과의 단일 순회 계산과 종목별 500ms 재평가로 바꿨다. deep 250ms 수신과 모든 호가의 PAPER 포지션 관리는 유지한다. 자동 서비스는 내장 실행환경 복사본과 소형 거래 상태·manifest용 SQLite, 외장 `data/market-parquet-v6` 고빈도 archive를 결합해 시작 지연·내장 용량 압박·외장 SQLite checkpoint·과거 1.3GB 원장 재스캔을 함께 피한다.
- 2026-08-23: schema v6 hybrid 저장은 공개시장 이벤트를 상위 10호가·1,000건 단위 ZSTD Parquet으로 외장에 기록하고 row·batch checksum과 root 경로를 검증한다. 5,000건 batch는 p95 5,978ms로 실패해 폐기했고, 1,000건 batch는 4분 이상 LIVE에서 pause·drop·gap·reconnect·fault 0으로 통과했다.
- 2026-08-23: ADR-009에 따라 `main`은 현재 실행 소스 한 벌만 유지하고, 과거 source는 Git history·tag, 배포물은 Release, 사용자용 변화는 짧은 `CHANGELOG.md`로 보존한다. 운영 구형 데이터는 삭제하지 않고 프로젝트 밖 migration archive로 이동하며 repository hygiene 검사를 CI와 release gate에 추가한다.
- 2026-08-23: 2차 UI는 장시간 Run 변경을 `202 ControlOperation`으로 분리하고, 초보자 홈·Strategy League·진행 거래를 고급 터미널과 분리한다. 스캐너는 고정 행·순서를, 차트는 선택 변경 외 `update`를 사용하며 보조지표는 전략 threshold와 분리한다.
- 2026-08-23: ADR-010에 따라 3차 UI는 5개 compact 메뉴와 시장 기본화면, Binance 전체 PAPER catalog, Upbit KRW 관찰 catalog, deep 20 안전회전, 전략×종목 성과와 실제 fill 기반 공용 포지션 집중·거래 단위 replay를 채택한다.
- 2026-08-24: ADR-011에 따라 Run별 Parquet partition, 250ms 방향별 체결 VWAP 병합, snapshot 통계 공유, 2,000건 비동기 저장과 종료 잔여 flush를 채택한다. 완료 거래 replay에는 저장 PAPER 원장 진입·종료 전환을 포함하고 DEMO는 LIVE 지연 telemetry를 상속하지 않는다.
- 2026-08-24: ADR-012에 따라 시작·연결·작동·사용자 일시정지·자동 안전 대기를 한 값으로 축약하지 않고, 시장 관찰과 새 PAPER 진입 상태를 분리한다. 자동 회복 가능한 잠금은 안전조건 정상화 뒤 자동 복귀하고, 주문장 전체 1,000단계는 보존하면서 상위 20단계 가격을 정확히 캐시한다.
- 2026-08-24: ADR-013에 따라 전략 신호와 최종 실행가능 비용 게이트의 가격구조를 일치시키고, A~D의 고정 통과 시간을 실제 event-time·history-prefix 확인으로 교체한다. 수익성 기준이나 신호 임계값은 낮추지 않으며 표본 부족을 그대로 표시한다.

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
| Upgrade 10 | COMPLETE | schema v6 hybrid 원장·LaunchAgent 자동복구·쉬운 홈·고정 scanner·실제 거래량·선택형 5/10/20/60선 구현. backend 105 PASS, frontend 5 PASS, lint/typecheck/build/security PASS. `run-9b9d508c689d` 4분 이상 37,984 events 측정에서 p95 140ms·pause false·queue/drop/gap/reconnect/fault 0, 이후 77,274 events를 외장 147 Parquet 7,987,803 bytes로 보존하고 SQLite raw event 0·quick check·replay PASS | in-app browser admin policy 확인 불가로 수정 후 DOM·screenshot 재캡처 BLOCKED | 완료 |
| Upgrade 11 | COMPLETE | ControlOperation 202·중복·충돌·취소·재시도, 6전략·12계좌 쉬운 UI, 고정 scanner·drawer, 증분 chart·MA/EMA/VWAP/볼린저/RSI/MACD를 구현했다. backend 150, frontend 24, Playwright 3 PASS, 실제 8870 browser desktop/tablet/mobile, GitHub Core·Browser Actions PASS를 완료했다. | network·30분·6시간·24시간 soak·Release ZIP NOT_RUN | 완료 |
| Upgrade 12 | COMPLETE | compact 시장·전체 Binance/Upbit catalog·3분봉 200·MA10/20·deep 20 회전·전략별 종목성과·FocusPosition·거래 중심 0.5~80x replay를 구현했다. backend 157·frontend 27·E2E 3, 실제 network Binance 696/Upbit 285·양쪽 candle 200 PASS, 30분 811,154 events·rotation 1·drop/gap 0 PASS, actual Chrome·GitHub Actions PASS다. | 자연 공개시장 PAPER fill NOT_OBSERVED, 6시간·24시간·Release ZIP NOT_RUN | 완료 |
| Upgrade 13 | COMPLETE | LIVE 병목 profiling 후 Run별 archive·체결 병합·통계 공유·호가 계산·저장 batch를 최적화했다. backend 162·frontend 29·E2E 3 PASS, 실제 180초 p95 최대 458ms·queue 최대 2·fault/drop/reconnect 0, 실제 브라우저 50개 조작 실패 0, DEMO/LIVE 모바일 진실표시와 완료 거래 종료 replay PASS다. | 자연 공개시장 PAPER fill NOT_OBSERVED, 6시간·24시간·Release ZIP NOT_RUN | 완료 |
| Upgrade 14 | COMPLETE | 시작 결과 상태패널·수동/자동 pause 분리·자동복귀 표시와 주문장 상위 20단계 캐시를 구현했다. backend 164·frontend 31·E2E 3과 정적·보안검사를 통과했고, 실제 8870에서 시작 한 번으로 READY→연결 중→작동 중, 일시정지→재시작, 12분 연속 RUNNING을 확인했다. | 6시간·24시간·Release ZIP NOT_RUN | 완료 |
| Upgrade 15 | COMPLETE | 비용후 실행가능 계획, A~D event-time 지속성, A~F 양방향 TP/SL 24시나리오, 고유 replay 후보 집계와 거래상세 상태 정리를 구현했다. backend 204·frontend 32·E2E 3과 전체 정적·보안검사를 통과했고, 공개시장 15,045 events replay 2회의 checksum·평가 41,628·적격 8·고유후보 5·shadow 종료 7이 일치했다. 실제 8870은 시작 한 번으로 RUNNING·p95 65ms를 표시했다. | 전략 수익성 표본 부족, 6시간·24시간·Release ZIP NOT_RUN | 완료 |

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
| 12 | COMPLETE | bfd19a4 | Backend·frontend·Playwright·actual Chrome·public network·30분 soak·GitHub Actions PASS | 자연 공개시장 PAPER fill NOT_OBSERVED; 6h·24h·Release NOT_RUN | 완료 |
| 13 | COMPLETE | a11cb0b | Backend 162, frontend 29, Playwright 3, security 106 source, actual browser 50 controls, public network와 180초 integrated LIVE PASS. GitHub Actions 32650393541의 validate·browser·증거 upload PASS | 자연 공개시장 PAPER fill NOT_OBSERVED; 6h·24h·Release NOT_RUN | 완료 |
| 14 | COMPLETE | f3f2151 | Backend 164·frontend 31·Playwright 3, lint·typecheck·build·security 107 source PASS. 실제 browser 시작·연결·작동·일시정지·재시작과 746초 LIVE RUNNING, p95 최대 1,144ms·queue 최대 2·drop/reconnect/gap/fault 0 PASS. | 6h·24h·Release ZIP NOT_RUN | 완료 |
| 15 | COMPLETE | 2a40186 | Backend 204·frontend 32·Playwright 3, lint·typecheck·build·security 107 source PASS. A~F × LONG/SHORT × TP/STOP 24시나리오와 실제 공개시장 replay 2회 checksum·집계 일치, 실제 browser READY→CONNECTING→RUNNING·p95 65ms, GitHub Actions 32674493842 PASS. | 수익성 표본 부족, 6h·24h·Release ZIP NOT_RUN | 완료 |
