# PLANS.md — Execution Plan and Progress Source of Truth

> 현재 제품 상태는 아래 Upgrade progress와 `FINAL_UPGRADE_EVIDENCE.md`를 우선한다. 완료된 초기 Wave는 현재 기능이 만들어진 순서와 수용 gate를 설명하는 구현 이력이며, 버전별 사용자 요약은 `CHANGELOG.md`를 사용한다.

This file is the source of truth for long-horizon implementation. Codex must continuously update status, decisions, validation evidence, and remaining work.

## Global definition of done

The application runs locally without credentials, connects to a supported venue's real public market data, dynamically scans dozens of eligible USDT perpetual symbols, operates a 1,000 USDT paper account, simulates realistic fills from executable order-book depth, displays a polished Korean dashboard, persists/replays trades, and contains no usable real-order path.

## Product north star and strategy-profitability gate

- The long-term user outcome is to discover strategies that may later deserve a separately approved real-money implementation. This repository remains public-market PAPER research and contains no real-order, private API, credential, wallet or deposit path.
- Do not optimize raw win rate in isolation. A strategy is useful only when current-version chronological evidence passes cost-adjusted expectancy, Profit Factor, drawdown, payoff, BASE and STRESS costs, sample sufficiency, symbol/regime concentration, independent OOS, deterministic bootstrap, DSR and PBO gates together.
- Freeze the hypothesis, parameters, market-data cutoff, fees, slippage and acceptance gates before each evaluation. Never lower a threshold or repeatedly tune the same sample merely to increase trades, win rate or dashboard rank.
- Keep fewer than 30 current-version natural `LIVE_PUBLIC` trades unranked and `NOT_PROVEN`. `SHADOW` to `CHALLENGER` still requires the existing 30-trade, 7-day and 2-regime gate; an `ACTIVE` replacement still requires the existing 100-trade, 21-day and 3-regime gate plus its stricter robustness and correlation checks.
- Failed candidates remain `SHADOW`, `RETIRED` or unregistered. Only a candidate that passes the preregistered cost and robustness contract may enter the shared PAPER benchmark. Test passage, a few wins or a high displayed win rate is not promotion evidence.
- Run comparable candidates on the same immutable public-market input and keep every weak, failed, no-trade and blocked result. Weak strategies leave the operating set as `RETIRED/OFF`; their source, BASE/STRESS accounts and audit history are never deleted merely to improve the displayed league.
- Target a useful non-ultra-scalp cadence, observed by the user as roughly two to three natural opportunities per day, only after the profitability gates pass. Frequency is a research objective, not a reason to weaken signal or safety criteria.
- Every iteration records train/validation/OOS boundaries, BASE/STRESS trade counts, win rate with uncertainty, net expectancy, Profit Factor, costs, maximum drawdown, hold duration, entry-to-TP1/TP2/SL/actual-close timing, hashes and the exact Registry decision.
- Any 1~3 second ordinary exit is traced from signal through bid/ask fill, TP/SL, edge state and close reason before exit policy changes. The beginner dashboard shows running/waiting/protected/trailing/closed state first and keeps statistical diagnostics in the detailed view.

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
- 2026-08-24: ADR-014에 따라 일반 근거약화 종료에 체결 뒤 10초 유예·복수 불리 신호·3초 지속 확인을 적용하고, 전략 성과는 독립 League 거래만 집계한다. 초기 SL/TP와 안전 종료는 즉시 유지하며 원장 정밀도는 바꾸지 않는다.
- 2026-08-25: ADR-015에 따라 전체 호가 가격과 1,200개 전략 통계 과거창을 정확한 증분 정렬 인덱스로 유지한다. 전략 임계값·Decimal 원장·PAPER 안전경계는 바꾸지 않고, native Fullscreen API가 없는 브라우저에는 앱 CSS 전체화면을 제공한다.
- 2026-08-25: ADR-016에 따라 다중호가 공정가 G와 깊이보정 OFI H를 EXPERIMENTAL·SHADOW 전용으로 추가해 A~H 8개 전략·16계좌를 비교한다. 기존 비용·보호·위험 기준은 낮추지 않으며 LIVE 중 저장 replay는 별도 저우선순위 프로세스로 격리한다.
- 2026-08-25: ADR-017에 따라 기본 전략 성과는 현재 구현 revision과 정확히 같은 `LIVE_PUBLIC` 독립계좌 거래만 집계한다. 교체 전 표본은 불변 원장에 보존하고 제외 건수를 공개하며 DEMO·REPLAY 표본은 별도 유형으로 기록한다.
- 2026-08-25: ADR-018에 따라 대형 replay의 읽기·전략·checksum 전 단계를 별도 저우선순위 process와 협력 CPU 예산으로 제한하고 거래 집중 결과를 schema v7 checksum cache에 보존한다. 공개 거래소 시각 오프셋으로 LIVE 지연을 보정하고 현재 전략버전 main 기록만 기본 표시한다.
- 2026-08-25: ADR-019에 따라 깊이보정 OFI와 실제 prefix 3초 수익률 동행을 요구하는 I를 EXPERIMENTAL·SHADOW 전용으로 추가해 A~I 9전략·18계좌를 비교한다. 저장 replay는 LIVE 우선권을 위해 5% CPU 예산과 시장 16건·checksum 128건 협력 양보를 사용한다.
- 2026-08-25: ADR-020에 따라 공개 거래소 시각을 monotonic 기준점으로 추적해 host wall-clock 보정과 실행경로 지연을 분리한다. 계획 WebSocket 교체는 provider 준비 전에 `RECONNECTING`·신규진입 잠금으로 바꾸고 bounded close 뒤 fresh depth에서만 자동 복구한다.
- 2026-08-25: ADR-021에 따라 top10 양쪽 호가의 거리 1bp당 누적 명목깊이 기울기 비대칭을 보는 J를 EXPERIMENTAL·SHADOW 전용으로 추가한다. 현재 snapshot 이전 동일종목 과거창만 사용하고 OFI·공격체결·microprice·가격반응·1초 지속·비용 게이트를 함께 요구하며 자연신호가 없다고 기준을 낮추지 않는다.
- 2026-08-25: ADR-022에 따라 같은 snapshot의 방향·청산형식이 같은 계획 입력을 최대 4개로 재사용하고, 전략 결정·독립계좌·replay 결과는 바꾸지 않는다.
- 2026-08-25: ADR-023에 따라 장시간 시장 Parquet 직렬화·압축·fsync를 별도 process로 격리하고, 최근 이벤트 10,000개와 계획거부 2,000개는 큰 prefix 삭제 없이 한 건씩 교체한다. 외부 공개 스트림 임계지연은 없애는 척하지 않고 신규 PAPER 진입 fail-closed와 자동회복을 유지한다.
- 2026-08-25: ADR-024에 따라 활성 SQLite와 archive 볼륨을 독립 검사하고 상태 비변경 감사의 snapshot 복제와 파생 candle 영구저장을 제한한다. ADR-025에 따라 deep 12·대시보드 512건·전략성과 cache를 채택한다.
- 2026-08-25: ADR-026에 따라 실행 bid·ask 호가, 공개 체결과 wide scanner 지연을 분리한다. 500ms보다 늦은 aggregate trade는 archive에 보존하되 전략입력에서는 제외하고, 차트의 현재 PAPER 정보와 A~J 전략별 감시상태·대기 이유를 표시한다.
- 2026-08-25: ADR-027에 따라 임계 지연의 시작·복구·지속시간, 이벤트 수신 공백과 2초 이상 저장 flush 시각을 진단값으로 보존한다. 유동성 재충전 실패 추세 후보 K는 12개 저장 LIVE_PUBLIC Run의 비용 포함 train·holdout이 모두 음수여서 Registry에 추가하지 않는다.
- 2026-08-25: ADR-028에 따라 안전 복구는 동기 유지하고 READY 과거 거래통계만 query-only SQLite 연결의 백그라운드 작업으로 분리한다. 부팅·저장 단계별 소요와 최대 이벤트 공백시각을 진단하고, 같은 BASE의 공동계좌와 전략 독립계좌를 현재 PAPER 목록·차트에서 구분한다.
- 2026-08-25: ADR-029에 따라 Parquet fsync 뒤 archive manifest·종목별 통계·캔들을 `synchronous=FULL` 단일 SQLite 트랜잭션으로 확정한다. 중복 checksum 충돌은 전체 롤백하고 작업자는 두 버퍼를 복원해 fail-closed하며, 전략·비용·TP·SL 기준은 바꾸지 않는다.
- 2026-08-25: ADR-030에 따라 SQLite 기본 1,000-page 자동 WAL checkpoint를 COMMIT 경로에서 끄고 8회 저장마다 별도 process의 PASSIVE checkpoint로 실행한다. 미완료는 재시도하고 WAL 64MiB 이상 실패는 fail-closed한다.
- 2026-08-25: ADR-031에 따라 Parquet 작성과 `synchronous=FULL` 원자 커밋 전체를 background I/O process의 독립 SQLite 연결로 옮긴다. 기존 WAL·FULL·checksum·원자성·버퍼복구와 모든 PAPER 안전기준은 유지한다.
- 2026-08-25: ADR-032에 따라 현재버전 비용후 성과와 시간순 `LIVE_PUBLIC` train·holdout 실패가 확인된 E/H를 기본 OFF로 두고 A는 SHADOW로 내린다. B만 ACTIVE로 유지하며 과거 거래와 20개 독립계좌는 보존한다. raw depth는 모두 복원한 뒤 완성 snapshot과 aggregate trade를 종목별 500ms로 제한해 provider queue headroom을 확보한다.
- 2026-08-25: ADR-033에 따라 후보·진입·TP·관리청산·실제 청산의 감사 시각을 각 결정·호가·체결 event-time으로 기록하고, 불변 신호시각은 후보 관련 이벤트에만 사용한다.
- 2026-08-25: ADR-034에 따라 실제 A~J 런타임 evaluator의 시간순 저장 `LIVE_PUBLIC` 비용후 선별에서 train·holdout 모두 실패한 A를 기본 OFF로 내린다. B만 ACTIVE, C/D/F/G/I/J는 SHADOW, A/E/H는 OFF를 유지하며 임계값은 낮추지 않는다.
- 2026-08-25: ADR-035에 따라 저장 train BASE 4건과 더 늦은 자연 LIVE_PUBLIC BASE 2건이 모두 비용후 손실인 D를 기본 OFF로 내린다. B만 ACTIVE, C/F/G/I/J는 SHADOW, A/D/E/H는 OFF로 유지하며 코드·과거 표본·수동 재활성화는 보존한다.
- 2026-08-25: ADR-036에 따라 LIVE 전략 성과·종목별 성과 API는 시작 때 checksum 검증한 현재-version cache와 현재 Run 메모리 거래를 사용한다. Replay와 비LIVE 분석은 불변 원장을 직접 읽고, cache와 원장의 의미 일치를 회귀검사한다.
- 2026-08-25: ADR-037에 따라 READY 서비스에서 Fresh Run을 시작할 때 평평한 과거 미종료 Run을 삭제 없이 보존 종료한다. 가장 최근 checksum 검증 snapshot에 PAPER pending 또는 position이 있으면 신규 Run을 fail-closed한다.
- 2026-08-26: ADR-038에 따라 전략 설정을 revision·CAS·actor·reason·manual lock과 함께 불변 감사하고, 자동 Governor는 충분한 현재버전 LIVE_PUBLIC 표본과 BASE·STRESS 비용후 조건을 모두 통과할 때만 champion 교체를 허용한다.
- 2026-08-26: ADR-039에 따라 1m·3m·5m·15m·30m·1h·4h completed candle을 단일 registry로 사용하고, 사전등록 5계열×3변형×12조합 180개 가설을 시간순 purge·embargo·walk-forward·PBO·DSR·bootstrap·mirror parity로 검증한다. 이번 OOS 결과는 승격 근거가 없어 Registry를 변경하지 않는다.
- 2026-08-26: ADR-040에 따라 종료 중 persistence task를 shield해 완료 결과를 회수하고, macOS 서비스는 최신 미종료 PAPER Run의 LIVE/DEMO 의도와 사용자 수동 일시정지를 복구한다. 자동 안전복구는 사용자가 누른 일시정지를 해제하지 않는다.
- 2026-08-26: ADR-042에 따라 LIVE 거래기록은 시작 때 검증한 전체 main·전략리그 cache를 사용하고, replay 화면은 최근 candle 미리보기와 명시적 checksum 이벤트 로딩·전략 재검증을 분리한다. 거래 수를 늘리기 위한 전략 기준 변경은 하지 않는다.
- 2026-08-26: ADR-044에 따라 사용자 PAPER 신규진입 의도를 revision·CAS·idempotency·감사기록으로 자동 안전잠금과 분리한다. 큰 원장의 거래 cache는 HTTP 시작 뒤 준비하고, replay 기본 결과는 query-only 연결에서 source Run별 최신본과 최근 결정경로만 반환하되 원장 원본은 보존한다.
- 2026-08-26: ADR-045에 따라 비용후 반복 실패한 A/D/E/H는 RETIRED·OFF로 잠그고, 완성 1시간봉 K는 미래 독립 표본을 모으는 SHADOW로만 추가한다. 11전략·22계좌와 과거 원장을 보존하며 수익성은 NOT_PROVEN이다.
- 2026-08-26: ADR-046에 따라 거래 상세 세션을 먼저 완성하고 선택적 focus cache의 SQLite lock·busy는 cache miss로 처리한다. 원본·checksum·스키마 오류는 계속 실패시키고 UI에 명시적 재시도를 제공한다.
- 2026-08-26: ADR-048에 따라 현재 프로세스 RSS와 프로세스 생애 최고 RSS를 분리한다. soak 증가량은 현재 RSS만 사용하고 최고치는 별도 진단으로 유지하며, 플랫폼 측정 실패는 최고치 fallback임을 숨기지 않는다.
- 2026-08-26: ADR-049에 따라 활성 대형 SQLite에 full `quick_check`를 병행하지 않는다. 평평한 PAPER Run을 충분한 유예로 닫고 WAL 0·APFS clone을 고정한 뒤 동일 Run을 먼저 재기동하며, 제한 전송과 SHA-256 대조를 통과한 다른 device의 immutable 사본에서만 full 무결성을 검증한다.
- 2026-08-27: ADR-053에 따라 신규 시작 복구 incident에 이전·신규 상태, actor, 원인, Run, revision과 reversibility를 정규화한다. LIVE 재검증, READY 지연, fail-closed와 DEMO fixture 복구를 분리하고 과거 행·스키마는 재작성하지 않는다.
- 2026-08-27: ADR-054에 따라 신규 PAPER 후보·진입·보호·청산 lifecycle 행을 계좌·종목별 상태와 연속 revision으로 정규화한다. recovery schema v4는 cursor와 마지막 전환을 보존하고 schema v1~v3는 실제 pending·position 상태에서 새 cursor를 시작하며 과거 행은 재작성하지 않는다.
- 2026-08-27: ADR-055에 따라 11개 전략마다 불변 연구 계약을 두고 strategy version, 필요한 공개시장 데이터, warmup, 가설·반증, 종료·비용·위험예산, 대상 종목, 미래정보 방지와 1차 Source ID를 API·한국어 상세 화면에 함께 공개한다. 현재 lifecycle·변경 이유는 실행 Registry에서 가져오며 계약 공개를 수익성 증거로 해석하지 않는다.
- 2026-08-27: ADR-056에 따라 macOS LaunchAgent는 개발 worktree가 아닌 commit별 불변 runtime 릴리스에서만 backend와 frontend를 함께 실행한다. staging·release·`current` 전환과 배포 감사·rollback point를 원자화하고 화면·서버 commit 불일치는 한국어 안전 화면으로 fail-closed한다. 실제 설치 서비스 전환은 기준 6시간 observer와 평탄 상태 확인 뒤 수행한다.
- 2026-08-27: ADR-057에 따라 최상위 React 예외는 빈 화면 대신 PAPER 안전 복구 화면으로 fail-closed한다. macOS launcher는 물리 release root를 유일한 애플리케이션 `PYTHONPATH`로 고정하고 실제 `backend` import가 release 밖이면 서버 시작 전 차단한다. 기준 observer 동안 현재 8870은 같은 기준 commit의 frontend로만 임시 정합성 복구한다.
- 2026-08-27: ADR-058에 따라 저장 리플레이 결과는 Run뿐 아니라 검증 종목 범위를 함께 기록한다. 현재 선택한 Run·종목에 정확히 일치하는 checksum·평가 결과만 표시하고, 모호한 과거 결과는 숨기며, 종목 대소문자는 리플레이 경계에서 정규화한다.
- 2026-08-27: ADR-059에 따라 대형 저장 replay보다 설치 LIVE PAPER 서비스를 우선한다. replay 시작 전 경량 안전 기준선을 고정하고 실행 중 지연·이벤트 정지·비계획 재연결·gap·drop·저장 결함·실제주문·인증·포지션을 감시해 위반 시 worker를 자동 종료하며, 최종 안전 표본을 통과한 결과만 원장에 기록한다.
- 2026-08-27: ADR-060에 따라 archive 압축·fsync의 Darwin background 우선순위와 SQLite `synchronous=FULL` 원자 커밋 우선순위를 분리한다. planned rotation은 첫 한 종목이 아니라 정밀 종목 전체의 fresh depth를 확인할 때까지 실행호가와 신규진입을 잠그며, runtime/replay 순환 import도 제거한다.
- 2026-08-27: ADR-061에 따라 최종 불변 릴리스와 새 LaunchAgent를 현재 서비스를 끄지 않고 먼저 준비한다. 닫힌 원장 유지관리기가 기존 job을 한 번만 정상 종료하고 clone 뒤 준비된 릴리스로 같은 Run을 복구해 배포·원장 검증 사이의 이중 재시작과 잘못된 worktree 재기동을 차단한다.
- 2026-08-27: ADR-063에 따라 SQLite `BEGIN IMMEDIATE` 실패시 process `RLock`을 반드시 해제한다. 개별 시장 sink 예외는 consumer task를 종료하지 않고 누락·안전잠금으로 기록하며, queue 저수위·연속 성공을 확인한 뒤만 복구한다. producer·consumer 어느 task라도 종료되면 화면과 신규진입을 fail-closed하며, 재시작은 새 Run을 만들지 않고 같은 Run의 supervisor만 교체한다. 장시간 관찰도 소비 완료 전진을 독립 gate로 판정한다.
- 2026-08-27: ADR-064에 따라 consumer 사고로 이미 `ENTRY_LOCKED`·`QUEUE_LIMIT_EXCEEDED`인 평탄 PAPER 기준선은 명시적 복구 옵션에서만 단일 유지관리 전환을 허용한다. 포지션·실주문·인증·오류 등 다른 위반은 계속 차단하고, 복구된 불변 서비스에는 일반 엄격 기준을 다시 적용한다.
- 2026-08-27: ADR-065에 따라 닫힌 APFS clone의 cross-device 전송과 양쪽 SHA-256 대조를 LIVE 재시작 전에 끝낸다. source-side clone을 제거한 뒤 같은 Run 불변 서비스를 복구하고, 다른 device immutable copy의 quick-check만 엄격한 LIVE 감시와 병행한다.
- 2026-08-27: ADR-073에 따라 디스크 사용량과 archive·ledger 안전상태는 단일 비동기 storage-health worker만 갱신한다. dashboard와 시장 이벤트는 캐시를 읽고, 5초 이상 갱신이 늦으면 기존 포지션 보호는 유지한 채 신규 PAPER 진입만 fail-close한다.
- 2026-08-27: ADR-074에 따라 한 시장 이벤트의 주문·체결·거래·감사·전략계좌·복구 snapshot을 하나의 원자 트랜잭션으로 저장하고, consumer는 10ms 연속 처리마다 협력 양보한다. 전략·체결·보유·비용 기준은 변경하지 않는다.
- 2026-08-27: ADR-075에 따라 dashboard 집계와 JSON 직렬화를 이벤트 루프 밖에서 직렬화하고 LIVE 표시 메모리를 2,048건으로 제한한다. 권위 있는 공개시장 archive와 replay 입력은 줄이지 않는다.
- 2026-08-27: ADR-076에 따라 기존 1,500ms 임계값을 넘은 LIVE 실행호가는 사건·archive에 보존하면서 `EXECUTABLE_LAG_STALE`로 체결·피처·전략 입력에서 격리한다. 신선한 같은 종목 호가 전에는 data-gap을 해제하지 않는다.
- 2026-08-28: ADR-085에 따라 일간 위험기간은 UTC 00:00, 주간은 월요일 UTC 00:00에 전환한다. 후보·진입·종료에서 event-time 기간 cursor를 갱신하고 recovery는 현재 기간의 불변 완료거래와 열린 포지션만 다시 집계한다. 일간 12건·손실한도·전략기준은 유지한다.

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
| Upgrade 16 | COMPLETE | 1~2초 EDGE_DECAY churn을 10초 유예·복수 신호·3초 지속 확인으로 수정하고 독립 전략 통계·적응형 UI 자릿수·현재 Run 기록 기본필터를 구현했다. backend 207·frontend 36·E2E 3·정적·보안·build PASS. 새 정책 실제 공개시장 main 18.354초, League 15.664~38.382초 보유와 실제 browser 6/6 전략·12/12 방향·PAPER 주문 0을 확인했다. | 전략 수익성 표본 부족, 6시간·24시간·Release ZIP NOT_RUN | 완료 |
| Upgrade 17 | COMPLETE | 장시간 호가·전략통계 반복 정렬과 앱 내 브라우저 전체화면·모바일 클릭 겹침을 수정했다. backend 213·frontend 36·E2E 3·정적·보안·build PASS. 실제 8870 시작·정지·재개·자연 진입·TP/SL·기록·분석·replay·전체화면을 직접 확인했고, 1분 표본 실행경로 p95 141~382ms·gap/drop/fault 0, main 37.070초·shadow 최소 14.060초였다. | 변경 후 6시간·24시간 soak, 전략 수익성, Release ZIP NOT_RUN | 완료 |
| Upgrade 18 | COMPLETE | 공식 연구를 근거로 G/H를 SHADOW 전용으로 추가하고 8전략·16계좌로 확장했으며 LIVE 중 replay를 별도 프로세스로 격리했다. backend 248·frontend 36·E2E 3·정적·보안·build PASS. 15,045 events replay 3회의 checksum·55,504 평가·9 적격·8 후보·9 shadow가 일치했고, 실제 replay 병행 중 LIVE event 2,597건 진행·reconnect/lag/drop 0, 183초 RUNNING 32,974 events·queue 최대 2를 확인했다. 실제 H LIVE_PUBLIC 3건은 19.664~230.384초 보유했고 수익성은 미입증이다. | 수익성 표본 부족, G 자연신호 NOT_OBSERVED, 6시간·24시간·Release ZIP NOT_RUN | 완료 |
| Upgrade 19 | COMPLETE | 현재 전략 구현 revision을 Run·shadow 거래에 고정하고 LIVE_PUBLIC 현재버전만 승률·기대값·PF·비용·낙폭에 집계했다. backend 248·frontend 38·E2E 3·정적·보안·build PASS, 실제 Chrome과 3개 반응형 화면 PASS, 현재버전 자연 표본 15건·최단 13.416초·1~2초 종료 0, 과거버전 154건 제외, 저장 공개시장 85,838 events replay·304,496 평가·10 shadow PASS, GitHub Actions 32754123908 PASS다. | 수익성 표본 부족, 대형 replay 무지연 미입증, 6시간·24시간·Release ZIP NOT_RUN | 완료 |
| Upgrade 20 | COMPLETE | 대형 replay를 `nice(19)`·구간별 10% CPU 예산·schema 3 streaming checksum으로 수정하고 timeline·focus·full replay lock, `REPLAY_BUSY`, 거래 집중 cache, Parquet 전체 batch 검증, 공개 거래소 시각 보정을 구현했다. backend 260·frontend 40·fixture 11·E2E 3·정적·보안·build PASS, 85,714 events 두 replay의 checksum·집계 일치와 LIVE p95 최대 171.5/659.5ms·critical/reconnect/gap/drop/lock 0, 실제 브라우저 시작·정지·재개·5화면·1분·MA5·8/8 전략·주문 0, GitHub Actions 32780373377 PASS를 확인했다. | 새 schema의 332,553건 전체 replay, 수익성, 6시간·24시간·Release ZIP NOT_RUN | 장기 PAPER 모니터 지속 |
| Upgrade 21 | COMPLETE | I OFI·단기수익률 동행 SHADOW 전략과 9전략·18계좌를 구현했다. backend 279·frontend 40·Playwright 3·lint·typecheck·build·security 114 source·repository hygiene PASS. 실제 8870 시작·일시정지·재개·9/9 전략·18/18 방향·console error 0, 15,045 events replay 세 checksum 일치, 5% replay 병행 225초 LIVE P95 최대 369.5ms·critical/reconnect/gap/drop/lock 0, GitHub Actions 32785122708 PASS를 확인했다. | 전략별 표본 0~7건과 I 자연 표본 0으로 수익성 NOT_PROVEN, 6h·24h·Release ZIP NOT_RUN | 장기 PAPER 모니터 지속 |
| Upgrade 22 | COMPLETE | wall-clock 보정 뒤 +2,158ms 오프셋이 남아 정상 이벤트를 임계지연으로 오인하던 문제, 순차 WebSocket 종료 지연, 교체 준비 중 늦은 안전잠금과 이전 Run의 낡은 진입 알림을 수정했다. backend 283·frontend 41·Playwright 3·전체 정적·build·security·repository hygiene PASS. 실제 공개 스트림 단축 교체 3회가 최대 0.919초, 생산 기본 15분 교체 1회가 1.749초에 복구됐다. 최종 새 빌드 시작은 2.5초 안에 작동 중, P95 38.330ms·낡은 알림·console 오류 0이었다. 교체 전 임계지연 406건은 fail-closed 뒤 자동복구됐고 교체 뒤 후속 59,962 event와 최종 회귀검사에서 추가 증가·오류가 없었다. | 전략 수익성·6h·24h·Release ZIP NOT_RUN | 장기 PAPER 모니터 지속 |
| Upgrade 23 | COMPLETE | top10 호가 기울기 비대칭 J를 SHADOW 전용으로 추가해 A~J 10전략·20계좌로 확장했다. backend 294·frontend 41·Playwright 3·전체 정적·build·security 115 source·repository hygiene PASS. 저장 공개시장 15,045 events replay 2회 checksum·69,380 평가·9 적격·8 후보·9 shadow가 일치했다. 실제 8870 시작·일시정지·재개와 10/10 전략·20방향·J 상세를 확인했고 181.9초 RUNNING에서 event +25,043·P95 최대 349.242ms·critical/reconnect/gap/drop/lock/fault 0이었다. 현재버전 main 2건·shadow 14건의 최단 보유는 13.762초로 1~2초 종료 0이다. | J 자연신호 NOT_OBSERVED, 전략 수익성·6h·24h·Release ZIP NOT_RUN | 장기 PAPER 모니터 지속 |
| Upgrade 24 | COMPLETE | snapshot 계획 32→최대 4회 재사용, Parquet 별도 process, 최근 이벤트·계획거부 고정길이 queue와 성과 범위 문구를 구현했다. backend 299·frontend 41·Playwright 3·정적·build·security 115 source·repository hygiene PASS. `run-b85a51c5daed` 966초에서 event +158,700, 메모리 10,000 고정, process flush 최대 5,591ms, 계획회전/전체 reconnect 1/1, 비계획 reconnect·drop·gap·fault 0이었다. 외부 TRADE 임계지연은 fail-closed 뒤 회복했고 최종 P95 343.373ms·lock false였다. replay 2회 checksum·집계 일치, 실제 브라우저 시작·정지·재개·10전략·성과 범위와 main 보유 17.670~22.608초를 확인했다. GitHub Actions 32798366401 PASS다. | 전략 수익성·6h·24h·Release ZIP NOT_RUN | 장기 PAPER 모니터 지속 |
| Upgrade 25 | COMPLETE | 활성 원장·archive 볼륨 안전, 제한된 LIVE 처리, 실행호가·체결·wide scanner 지연 분리, 현재 PAPER 차트와 A~J 감시 가시성을 구현했다. backend 312·frontend 44·Playwright 3·전체 정적·build·security·repository hygiene PASS. 실제 181초 Run에서 event +19,056, 실행호가 p95 최대 278.431ms, queue 최대 1, critical/lock/reconnect/gap/drop/fault 0이었고 자연 shadow 16건의 보유는 11.652~65.464초였다. GitHub Actions 32809307309 PASS다. | 전략 수익성·6h·24h·Release ZIP NOT_RUN | 장기 PAPER 모니터 지속 |
| Upgrade 26 | COMPLETE | 임계지연 사건·이벤트 공백·저장 flush 시각 진단과 종료된 차트 진입 알림 정리를 구현했다. backend 313·frontend 45·Playwright 3·전체 정적·build·security·repository hygiene PASS. 새 실제 `run-4c905f26da0d`에서 A~J 각각 24경로·fault 0, 자연 shadow 8건·1~2초 종료 0, LIVE+15,045건 replay 병행 중 실행호가/체결 p95 약 43/46ms·critical/lock/reconnect/gap/drop/fault 0이었다. 동일 구현 replay checksum `5880f66a…`와 69,380평가·9적격·8후보·9 shadow가 기존 반복 결과와 일치했다. 후보 K는 비용후 train·holdout 음수라 기각했고 GitHub Actions 32811910384가 PASS했다. | 전략 수익성·6h·24h·Release ZIP NOT_RUN | 장기 PAPER 모니터 지속 |
| Upgrade 27 | COMPLETE | READY 과거 거래통계를 백그라운드 query-only SQLite 연결로 분리하고 부팅·저장 단계·이벤트 최대공백시각을 진단했다. 같은 BASE의 공동·독립 PAPER 계좌를 목록·차트에서 구분했다. backend 315·frontend 47·Playwright 3·정적·build·security·repository hygiene PASS. 실제 재시작 내부 준비 10.56초→0.212초, 통계 백그라운드 1.624초, 즉시 시작 250ms 내 연결 중·8초 뒤 작동 중을 확인했다. 실제 저장 flush 최대 6.682초는 Parquet 1.665초·manifest 1.642초·candle 3.365초였고 같은 Run 실행호가 p95 약 35ms·최대 공백 1.047초·reconnect/gap/fault 0이었다. 구현 commit `354053d`, GitHub Actions 32814598091의 validate·browser·증거업로드가 PASS했다. | 전략 수익성·6h·24h·Release ZIP NOT_RUN | 장기 PAPER 모니터 지속 |
| Upgrade 28 | COMPLETE_WITH_FOLLOWUP | archive manifest·이벤트 통계·캔들을 `synchronous=FULL` 한 커밋으로 원자 저장하고 backend 318·frontend 47·Playwright 3·정적·build·security를 통과했다. 초기 56,260 events·28 flush에서는 최대 1.506초였으나 같은 `run-2b0119b86432`을 159,663 events까지 연장하자 통합 커밋 15.520초·최대 수신 공백 11.823초·임계 지연 6회가 기록돼 지속 성능 완료 주장을 철회했다. 원자성·rollback·전략 화면 검증과 GitHub Actions 32815768312 PASS는 유효하다. | 장기 성능 후속 필요, 전략 수익성·6h·24h·Release ZIP NOT_RUN | Wave 29·30에서 저장 경로 격리 |
| Upgrade 29 | COMPLETE_WITH_FOLLOWUP | SQLite 자동 WAL checkpoint를 COMMIT에서 끄고 8 flush마다 별도 process PASSIVE checkpoint를 구현했다. backend 320·표적 46·frontend 47·Playwright 3·전체 정적·build·safety·security PASS, GitHub Actions 32817722186 PASS였다. 실제 `run-517b78c88366` 194,449 events·97 flush에서 checkpoint 17.496초와 FULL 커밋 7.741초가 분리됐지만 최대 공백 5.867초·임계 지연 4회·최장 45.896초가 남아 완전 해결로 판정하지 않았다. reconnect/gap/drop/fault는 0이었다. | 같은 process의 FULL 커밋 격리 필요, 수익성·6h·24h·Release ZIP NOT_RUN | Wave 30에서 전체 저장 process 격리 |
| Upgrade 30 | COMPLETE | Parquet 작성과 `synchronous=FULL` 원자 커밋 전체를 독립 SQLite 연결의 background I/O process로 격리했다. backend 321·표적 47·frontend 47·Playwright 3·전체 정적·build·PAPER safety·security·repository hygiene PASS, GitHub Actions 32820190558 PASS였다. 실제 `run-622167a01f3c` 207,283 events·103 flush·계획 회전 2회에서 worker FULL 커밋 12.530초와 checkpoint 22.984초 중에도 처리 p95 39.903ms, 임계 지연 2회·최장 1.816초, 최종 lock false였고 비계획 reconnect/gap/drop/fault 0이었다. 실제 차트에서 전략·방향·계좌·entry·TP1·TP2·SL·수량·최대손실을 확인했고, A~J 24경로·fault 0 중 A/C/E/F/H 자연거래와 B/D/G/I/J 정상대기를 구분했다. | 미래 지연 0·전략 수익성 NOT_PROVEN, 활성 원장 full check·6h·24h·Release ZIP NOT_RUN | 장기 PAPER 모니터 지속 |
| Upgrade 31 | COMPLETE_WITH_FOLLOWUP | E/H의 저장 `LIVE_PUBLIC` 비용후 train·holdout 실패를 재현해 기본 OFF로 두고 A를 SHADOW로 내렸다. B만 ACTIVE이며 과거 거래·20계좌는 보존했다. depth/trade 전달을 종목별 500ms로 제한해 포화 전 queue 4,096·drop 270,796·표시지연 12,128ms를 새 Run의 정상범위로 회복했다. backend 322·frontend 47·Playwright 3·정적·build·PAPER safety·security·repository hygiene PASS이며 실제 브라우저에서 8개 감시·2개 검증중지·문제 0·주문 0과 자연 A PAPER 진입의 TP/SL·비용을 확인했다. | 높은 승률·수익성 NOT_PROVEN, 6h·24h·Release ZIP NOT_RUN | 현재 revision 자연표본과 장기 PAPER 모니터 지속 |
| Upgrade 32 | COMPLETE_WITH_FOLLOWUP | 감사 event-time을 실제 결정·호가·체결 시각으로 수정하고, 저장 `LIVE_PUBLIC` 13개 Run에서 실제 A~J evaluator를 train 8·holdout 5로 선별했다. A는 train BASE 25건·승률 8%·기대값 -21.139bp·PF 0.072, holdout 10건·0승·-13.767bp·PF 0이라 기본 OFF로 전환했다. backend 323·frontend 47·Playwright 3·정적·build·PAPER safety·security·repository hygiene PASS다. 새 `run-04a41901147e`과 실제 브라우저에서 7개 감시·3개 검증중지·문제 0·실제주문 0을 확인했다. | B와 나머지 전략 수익성 NOT_PROVEN, 6h·24h·Release ZIP NOT_RUN | 현재 revision 자연표본과 장기 PAPER 모니터 지속 |
| Upgrade 33 | COMPLETE_WITH_FOLLOWUP | D의 저장 train BASE 4건·후기 자연 BASE 2건 전부 비용후 손실을 근거로 D를 기본 OFF로 내리고, 13.7~16.0초였던 LIVE 성과 API를 2.3~4.2ms 메모리 cache 경로로 전환했다. READY 시작이 누적시킨 미종료 Run 76개를 삭제 없이 보존 종료하고 복구 노출은 fail-closed한다. backend 327·frontend 47·Playwright 3·정적·build·PAPER safety·security·repository hygiene PASS며, 실제 `run-f7118bed2264`와 브라우저에서 6개 감시·4개 중지·문제 0·실제 주문 0을 확인했다. | 수익성 NOT_PROVEN, 6h·24h·활성 원장 full check·Release ZIP NOT_RUN | 현재 revision 자연표본 30건과 장기 PAPER 모니터 지속 |
| Upgrade 36 | COMPLETE_WITH_LIMITS | 거래기록 기본 전체계좌·LIVE cache·로딩/건수 표시와 replay 최근 candle 미리보기·명시적 정밀 이벤트/전략 검증을 구현했다. 현재 Run 저장·복구 거래 수의 이중 합산도 거래 ID 병합으로 제거했다. backend 362·frontend 53·fixture 15·Playwright 3·정적·build·PAPER safety·security·repository hygiene PASS다. 실제 기록 공동 0·전략별 28건과 replay 요약 28건 일치, history 12회 8.3~209.2ms, Run 목록 12회 3.0~5.4ms, 정밀 2,000 events 로딩과 주문·인증 0을 확인했다. | 검증 부하 중 저장 커밋 14.261초로 안전대기 86.3초 뒤 자동복구 PASS_WITH_LIMIT, 현재 대형 Run 전체 전략 replay 완료시간 장기관찰 필요, 수익성 NOT_PROVEN, 6h·24h·Release ZIP NOT_RUN | 현재 revision 자연표본과 장기 PAPER 모니터 지속 |
| Upgrade 46 | COMPLETE_WITH_LIMITS | 사전등록 장중 후보 4개와 K의 독립 과거구간 147일·166건을 고정 비용으로 재검증해 모두 기각했다. 기본 ACTIVE 0, B/C/F/G/I/J SHADOW, A/D/E/H/K RETIRED와 15분 Governor를 적용하고 TP1·TP2·STOP 소요시간, 원장 우선 전략버전 병합, 전체 기록 기본 표시와 긴 idle replay 진행을 구현했다. backend 402·frontend 56·fixture 17·Playwright 3·정적·PAPER safety·security 126 source·repository hygiene PASS다. 실제 63건 복구, 현재 버전 0건 분리, 7프레임 replay 1/7→7/7, 61.03초 LIVE event +3,957·실행 p95 최대 44.068ms·queue/reconnect/gap/drop/fault/lock 0을 확인했다. | 기존 63행은 1승 62패·순손익 -64.8911 USDT, 현재버전 자연 거래 0으로 수익성 NOT_PROVEN. active ledger full check·6h·24h·Release ZIP NOT_RUN, bundle 514.55kB 경고 | 현재 전략 버전 자연표본을 SHADOW에서 축적하고 승격 gate 통과 전 공동계좌 ACTIVE 0 유지 |
| Upgrade 47 | COMPLETE_WITH_LIMITS | 현재 메모리로 표시하던 `ru_maxrss` 최고치와 운영체제 현재 RSS의 불일치를 재현하고 플랫폼별 현재 RSS와 별도 최고 RSS, soak 분리 지표, 한국어 고급진단 문구와 회귀 테스트를 구현했다. Backend 405·frontend 57·fixture 17·Playwright 3·정적·build·PAPER safety·security 126 source·repository hygiene PASS이고 GitHub Actions 32962941998도 PASS다. 실제 8870 재시작 뒤 API 현재 RSS와 OS RSS 차이 0.609MB, 122.455초 LIVE +9,028 events·queue/drop/fault/lock/실제주문/인증 0, 현재 RSS 하락 중 최고 RSS 유지와 브라우저 표시를 확인했다. | 수정 전 clean 5시간 00분 34초 뒤 활성 2.798GB 원장 동시 full quick_check가 queue 4,096·drop 9,736을 유발해 중단·안전 재시작했다. 무결성 결과·수정 후 6h·24h·Release ZIP NOT_RUN, 수익성 NOT_PROVEN | 작동 중 writer와 동시 전수검사를 금지하고 안전한 닫힌 snapshot/maintenance 무결성 절차를 마련한 뒤 6h·24h 모니터링 지속 |
| Upgrade 52 | IMPLEMENTED_NOT_DEPLOYED | 활성 원장의 시작 복구 45행 전부에 정규 전환 필드가 없고 checksum 실패가 incident를 남기지 않는 경로를 재현했다. 성공·READY 지연·fail-closed·DEMO fixture를 다른 상태로 정규화하고 runtime 진단·초보자 UI를 연결했다. backend 436·frontend 59·Playwright 3·정적·build·PAPER safety·security·repository hygiene PASS다. | 실제 설치 서비스 신규 복구행·8870 화면·GitHub main·Actions NOT_RUN. 6h·24h는 기준 commit에서 IN_PROGRESS고 수익성은 NOT_PROVEN | 기준 서비스 관찰 완료 뒤 평탄 배포·실제 복구 감사·브라우저·GitHub 검증 |
| Upgrade 53 | IMPLEMENTED_NOT_DEPLOYED | 활성 원장의 실제 PAPER lifecycle 행과 fixture transition의 정규 필드 누락을 read-only로 재현했다. 후보·진입·보호·청산을 계좌·종목별 revision으로 연결하고 snapshot schema v4·진단·초보자 UI를 구현했다. backend 437·frontend 60·fixture 18·Playwright 3·정적·build·PAPER safety·security·repository hygiene PASS다. | 설치 서비스는 기준 commit이라 실제 신규 lifecycle 행·8870 화면·GitHub main·Actions NOT_RUN. 6h·24h는 기준 commit에서 IN_PROGRESS고 수익성은 NOT_PROVEN | 기준 서비스 관찰 완료 뒤 평탄 배포·실제 lifecycle 감사·브라우저·GitHub 검증 |
| Upgrade 54 | IMPLEMENTED_NOT_DEPLOYED | 11개 전략의 필수 데이터·warmup·가설·반증·종료·위험·대상·미래정보 방지·Source ID를 실행 계약과 한국어 상세 화면에 연결하고 낡은 전략상태 문서를 교정했다. backend 437·frontend 60·fixture 18·Playwright 3·정적·build·PAPER safety·security·repository hygiene PASS다. | 설치 서비스는 기준 commit이라 실제 신규 계약 API·8870 화면·GitHub main·Actions NOT_RUN. 6h·24h는 기준 commit에서 IN_PROGRESS고 수익성은 NOT_PROVEN | 기준 서비스 관찰 완료 뒤 평탄 배포·실제 전략 계약 API·브라우저·GitHub 검증 |

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
| 16 | COMPLETE | 0c7203e | Backend 207·frontend 36·Playwright 3, lint·typecheck·build·security 108 source PASS. 실제 public-market main 18.354초와 League 15.664~38.382초, browser `이번 Run` 기본 기록·6/6 전략·12/12 방향·RUNNING·실제주문 0, replay 2회 checksum과 집계 일치, GitHub Actions 32690932657 PASS. | 수익성 표본 부족, 6h·24h·Release ZIP NOT_RUN | 완료 |
| 17 | COMPLETE | 41e9063 | Backend 213·frontend 36·Playwright desktop/tablet/mobile 3, lint·typecheck·build·security 108 source PASS. 실제 8870 브라우저 전 기능, 자연 main 37.070초·shadow 최소 14.060초, replay 2회 checksum 일치와 1분 LIVE p95 141~382ms·queue 최대 1·gap/drop/fault 0, GitHub Actions 32744518964 PASS를 확인했다. | 수익성 표본 부족, 변경 후 6h·24h·Release ZIP NOT_RUN | 완료 |
| 18 | COMPLETE | e5cfcfe | Backend 248·frontend 36·Playwright desktop/tablet/mobile 3, lint·typecheck·build·security 111 source PASS. A~H 8전략·16계좌, G/H 양방향·TP/SL, replay 3회 checksum 일치, 실제 LIVE 중 process replay 격리와 H 자연 LIVE_PUBLIC 3건, 183초 RUNNING, GitHub Actions 32749612580의 validate 53초·browser 80초 PASS를 확인했다. | 수익성 표본 부족, G 자연신호·6h·24h·Release ZIP NOT_RUN | 완료 |
| 19 | COMPLETE | e471216 | Backend 248·frontend 38·Playwright desktop/tablet/mobile 3, lint·typecheck·build·security 111 source PASS. 현재 전략버전 LIVE_PUBLIC 15건만 성과에 집계하고 과거버전 154건을 불변 보존·제외 표시했다. 실제 Chrome 화면, 최단 보유 13.416초·1~2초 종료 0, 85,838 events replay, 원장 quick_check와 GitHub Actions 32754123908 PASS를 확인했다. | 수익성 표본 부족, 대형 replay 무지연·6h·24h·Release ZIP NOT_RUN | 완료 |
| 20 | COMPLETE | 924e8b3 | Backend 260·frontend 40·fixture 11·Playwright desktop/tablet/mobile 3, lint·typecheck·build·security 113 source PASS. schema 3의 85,714 events replay 2회 checksum·평가·후보·shadow·결정경로 일치, LIVE p95 최대 171.5/659.5ms·비정상 reconnect/gap/drop/critical/lock/fault 0, 실제 8870 시작·정지·재개·5화면·차트 지표·8/8 전략·주문 0, SQLite quick_check와 GitHub Actions 32780373377 PASS를 확인했다. | 새 schema 332,553건 전체·수익성·6h·24h·Release ZIP NOT_RUN | 장기 PAPER 모니터 지속 |
| 21 | COMPLETE | 24b8463 | Backend 279·frontend 40·Playwright desktop/tablet/mobile 3, lint·typecheck·build·security 114 source·repository hygiene PASS. I 전략의 양방향·prefix/no-lookahead·비용·지속성과 9전략 TP/SL·BASE/STRESS를 검증했다. 15,045 events replay 3회 checksum·62,442 평가·9 적격·8 후보·9 shadow 일치, 실제 8870 9/9 전략·18/18 방향·주문 0과 5% replay 병행 LIVE 225초 critical/reconnect/gap/drop/lock 0, GitHub Actions 32785122708 PASS를 확인했다. | 전략 수익성·I 자연신호 NOT_PROVEN, 6h·24h·Release ZIP NOT_RUN | 장기 PAPER 모니터 지속 |
| 22 | COMPLETE | 4253679 | Backend 283·frontend 41·Playwright desktop/tablet/mobile 3, lint·typecheck·build·security 114 source·repository hygiene PASS. monotonic 거래소 시각과 교체 시작 즉시 fail-closed를 검증했고 실제 Binance 공개 스트림 3회 단축 교체는 모두 최대 0.919초, 생산 기본 15분 교체 1회는 1.749초에 복구됐다. 교체 전 임계지연 406건은 안전잠금 뒤 자동회복됐고 후속 event 127,612→187,574에서 증가하지 않았다. 실제 8870 READY→CONNECTING→RUNNING, 낡은 Run 알림 제거와 자연 main 21.068초·1~2초 종료 0, GitHub Actions 32789067527 PASS를 확인했다. | 전략 수익성·6h·24h·Release ZIP NOT_RUN | 장기 PAPER 모니터 지속 |
| 23 | COMPLETE | a8a04b8 | Backend 294·frontend 41·Playwright desktop/tablet/mobile 3, lint·typecheck·build·security 115 source·repository hygiene PASS. J 양방향·과거-prefix 기울기·지속·비용과 10전략 TP/SL·BASE/STRESS를 검증했다. 저장 공개시장 15,045 events replay 2회 checksum·69,380 평가·9 적격·8 후보·9 shadow 일치, 실제 8870 시작·일시정지·재개·10/10 전략·20방향·주문 0, 181.9초 RUNNING P95 최대 349.242ms·critical/reconnect/gap/drop/lock/fault 0, 현재버전 main 2건·shadow 14건 최단 13.762초, GitHub Actions 32791918431 PASS를 확인했다. | J 자연신호 NOT_OBSERVED, 전략 수익성·6h·24h·Release ZIP NOT_RUN | 장기 PAPER 모니터 지속 |
| 24 | COMPLETE | 887b0ec | Backend 299·frontend 41·Playwright desktop/tablet/mobile 3, lint·typecheck·build·security 115 source·repository hygiene PASS. 966초 실제 공개시장 런에서 event +158,700·메모리 10,000 고정·계획회전 1·비계획 reconnect/drop/gap/fault 0, process flush 최대 5,591ms와 최종 실행경로 P95 343.373ms를 확인했다. 외부 순간지연은 fail-closed 뒤 자동회복했고 15,045 events replay 2회 checksum·69,380·9·8·0·9 일치, 실제 8870 시작·일시정지·재개·성과범위와 GitHub Actions 32798366401 PASS를 확인했다. | 전략 수익성·6h·24h·Release ZIP NOT_RUN | 장기 PAPER 모니터 지속 |
| 25 | COMPLETE | 7d4175d | 활성 SQLite·archive 독립 fail-closed, 상태변경 기준 recovery snapshot, SQLite 1초·180초 candle, deep 12·dashboard 512건·성과 cache, archive worker prewarm·SQLite thread, 실행호가/체결/scanner 지연 분리, 늦은 체결 전략 제외, 현재 PAPER chart·전략 감시상태를 구현했다. backend 312·frontend 44·Playwright 3·정적·build·security 115 source·repository hygiene·2GB SQLite quick check PASS. 실제 Run 181초 event +19,056·호가 p95 최대 278.431ms·queue 최대 1·critical/reconnect/gap/drop/fault 0, A~J 각 24경로·자연 shadow 16건·3초 미만 0, 실제 browser와 GitHub Actions 32809307309 PASS다. | 전략 수익성·6h·24h·Release ZIP NOT_RUN | 장기 PAPER 모니터 지속 |
| 26 | COMPLETE | 9842c33 | Backend 313·frontend 45·Playwright 3·전체 정적·build·security·repository hygiene PASS. 지연 사건·수신 공백·저장 flush 시각, 종료 차트 정리, A~J 감시와 실제 90초 LIVE·저장 replay를 검증했고 GitHub Actions 32811910384 PASS다. | 전략 수익성·6h·24h·Release ZIP NOT_RUN | 완료 |
| 27 | COMPLETE | 354053d | Backend 315·frontend 47·Playwright 3·Ruff·mypy 82·ESLint·TypeScript·build·PAPER safety·security 115 source·repository hygiene PASS. 실제 재시작·즉시 시작·LIVE 유지·A~J 24경로·공동/독립 계좌 차트 구분과 GitHub Actions 32814598091 validate 1분13초·browser 1분13초·증거업로드 PASS를 확인했다. | 전략 수익성·6h·24h·Release ZIP NOT_RUN | 장기 PAPER 모니터 지속 |
| 28 | COMPLETE_WITH_FOLLOWUP | ef12928 | Backend 318·frontend 47·Playwright 3와 원자성·rollback은 PASS했다. 다만 같은 실제 Run을 159,663 events까지 연장해 통합 커밋 15.520초·최대 공백 11.823초·임계 지연 6회를 확인했으므로 초기 28 flush의 성능 완료 판단은 후속 정정했다. GitHub Actions 32815768312 PASS다. | 장기 저장 성능 후속 필요, 전략 수익성·6h·24h·Release ZIP NOT_RUN | Wave 29·30에서 해결 진행 |
| 29 | COMPLETE_WITH_FOLLOWUP | 48823ee | Backend 320·표적 46·frontend 47·Playwright 3·정적·build·PAPER safety·security·저장소 위생 PASS. 별도 PASSIVE checkpoint를 실제 194,449 events까지 검증했으나 같은 process FULL 커밋 7.741초와 임계 지연 최장 45.896초가 남았다. GitHub Actions 32817722186 PASS다. | FULL 커밋 process 격리 필요, 수익성·6h·24h·Release ZIP NOT_RUN | Wave 30으로 이관 |
| 30 | COMPLETE | 663e385 | Backend 321·표적 47·frontend 47·Playwright desktop/tablet/mobile 3·Ruff·mypy 82·ESLint·TypeScript·build·PAPER safety·security 115 source·repository hygiene PASS. 실제 207,283 events·103 flush·계획 회전 2회에서 긴 worker FULL 커밋·checkpoint 중에도 p95 39.903ms, 임계 지연 최장 1.816초, 비계획 reconnect/gap/drop/fault 0을 확인했다. 실제 차트 진입계획과 A~J 감시·자연 A/C/E/F/H 거래·B/D/G/I/J 정상대기를 확인했고 GitHub Actions 32820190558 PASS다. | 미래 지연 0·수익성 NOT_PROVEN, 활성 원장 full check·6h·24h·Release ZIP NOT_RUN | 장기 PAPER 모니터 지속 |
| 31 | COMPLETE_WITH_FOLLOWUP | 60cecaf | Backend 322·표적 supervisor 18·frontend 47·Playwright desktop/tablet/mobile 3·Ruff·mypy 82·ESLint·TypeScript·build·PAPER safety·security 115 source·repository hygiene PASS. E/H 비용후 train·holdout 실패와 대체후보 자연신호 0을 숨기지 않고 기본 OFF, A SHADOW, B ACTIVE를 적용했다. 새 `run-0ca162282d14`과 실제 브라우저에서 RUNNING·지연 정상범위·8개 감시·2개 검증중지·주문 0과 자연 A PAPER 진입을 확인했고 GitHub Actions 32829795266이 PASS했다. | 높은 승률·수익성 NOT_PROVEN, 6h·24h·Release ZIP NOT_RUN | 현재 revision 자연표본과 장기 PAPER 모니터 지속 |
| 32 | COMPLETE_WITH_FOLLOWUP | 293a3db | Backend 323·frontend 47·Playwright desktop/tablet/mobile 3·Ruff·mypy 82·ESLint·TypeScript·build·PAPER safety·security 115 source·repository hygiene PASS. 실제 A~J evaluator의 시간순 train·holdout을 추가하고 실패한 A를 기본 OFF로 내렸다. 새 Run은 1,000 USDT·main 손익/비용/거래 0, queue/drop/critical/reconnect/gap/fault 0이었고 실제 브라우저는 7개 감시·3개 중지·문제 0·주문 0·console 오류 0이었다. GitHub Actions 32835366808도 PASS했다. | 수익성 NOT_PROVEN, 활성 원장 full check·6h·24h·Release ZIP NOT_RUN | 현재 revision 자연표본과 장기 PAPER 모니터 지속 |
| 33 | COMPLETE_WITH_FOLLOWUP | 0c256ab | D 비용후 실패 후 기본 OFF, LIVE 분석 cache, READY Fresh Run 보존 종료·복구노출 fail-closed를 구현했다. Backend 327·frontend 47·Playwright desktop/tablet/mobile 3·Ruff·mypy 82·ESLint·TypeScript·build·PAPER safety·security 115 source·repository hygiene PASS. 실제 새 Run 32,571 events에서 처리 p95 26.190ms·queue/drop/critical/unplanned reconnect/gap/fault/lock 0이고, 두 번째 checkpoint는 1.278초였다. 실제 브라우저는 6개 감시·4개 OFF·문제 0·주문 0·console 오류 0이다. GitHub Actions 32840334068의 validate 1분2초·browser 1분11초·증거업로드가 PASS했다. | 수익성 NOT_PROVEN, 일시 저장 flush 11.142초 PASS_WITH_LIMIT, 활성 원장 full check·6h·24h·Release ZIP NOT_RUN | 자연표본 30건과 장기 PAPER 모니터 지속 |
| 34 | COMPLETE_WITH_LIMITS | f571487 | 상태·제어 멱등성/CAS, 기록 scope, 단일 timeframe registry, Strategy Governor, canonical candle, 전략 운용 계약, 사전등록 장중 연구와 graceful service recovery를 구현했다. Backend 359·frontend 51·Playwright desktop/tablet/mobile 3·lint·typecheck·build·PAPER safety·security 124 source·repository hygiene PASS다. 13개 저장 Run 2,232,327 events·180개 가설의 시간순 OOS와 mirror parity 190쌍을 검증했으나 승격은 NOT_PROVEN이라 Registry 변경 0이다. 실제 30분 soak 130,248 events·비계획 reconnect/gap/drop/fault 0, 활성 2.2GB 원장 quick_check ok·FK 0, 실제 8870에서 수동 pause·resume 재시작 보존과 저장 replay checksum을 확인했다. GitHub Actions 32880481225의 validate 1분4초·browser 1분58초·증거업로드도 PASS했다. | 수익성 NOT_PROVEN, 기존 A~J full repeat·6h·24h·Release ZIP NOT_RUN, production bundle 502.44kB 경고와 동시 host 고부하 계획회전 86.467초는 PASS_WITH_LIMIT | 현재 revision 자연표본과 장기 PAPER 모니터 지속 |
| 35 | COMPLETE_WITH_LIMITS | e8bbc22 | 계획회전 뒤 99.325초·98.882초 임계지연을 WebSocket depth warmup backlog로 재현하고, stale delta는 sequence에만 적용하며 첫 fresh depth 전까지 진입잠금을 유지하도록 수정했다. Backend 360·frontend 51·Playwright 3+fixture 15·정적·build·PAPER safety·security 124 source·repository hygiene PASS다. 실제 단축 회전 2회와 생산 15분 회전 2회에서 critical 0, 비계획 reconnect/gap/resync/drop/fault 0, 생산 146,510 events·실행 p95 39.409ms였고 실제 브라우저 console 오류·경고 0·실제주문 0을 확인했다. GitHub Actions 32906261858도 PASS했다. | 수익성 NOT_PROVEN, 6h·24h·Release ZIP NOT_RUN, build 502.44kB 경고, 활성 원장 전수검사는 Wave34 PASS 뒤 재실행하지 않음 | 현재 revision 자연표본과 장기 PAPER 모니터 지속 |
| 36 | COMPLETE_WITH_LIMITS | 1a088ac | Backend 362·frontend 53·fixture 15·Playwright desktop/tablet/mobile 3·Ruff·mypy 91·ESLint·TypeScript·build·PAPER safety·security 124 source·repository hygiene PASS다. 실제 8870 거래기록 공동 0·전략별 28건과 replay 현재 Run 전략별 28건이 일치했다. 최종 history 12회 8.3~209.2ms·Run 목록 3.0~5.4ms, 현재 Run 정밀 2,000 events와 소형 Run 동일조건 전략검증·checksum 일치·실제주문/인증 0·console 오류 0을 확인했다. 구현 Actions 32912271959와 증거 Actions 32912523249의 validate·browser·증거업로드도 PASS했다. | 검증 부하 중 자동 안전대기 86.3초 뒤 복구 PASS_WITH_LIMIT, 현재 대형 Run 전체 전략 replay 완료시간 장기관찰 필요, 수익성 NOT_PROVEN, 6h·24h·Release ZIP NOT_RUN | 현재 revision 자연표본과 장기 PAPER 모니터 지속 |
| 37 | COMPLETE_WITH_LIMITS | bc11352 | 전략 replay를 202 백그라운드 operation·상태·경과시간·취소·timeout으로 전환하고, 혼합 SQLite/Parquet 상한과 이벤트 구간 candle로 timeline을 제한했다. Backend 366·frontend 54·fixture 15·Playwright desktop/tablet/mobile 3·Ruff·mypy 92·ESLint·TypeScript·build·PAPER safety·security 125 source·repository hygiene PASS다. 실제 8870 브라우저 기록 37건 뒤 최종 API 39건까지 전진했고, 현재 Run timeline 100건 0.63초, 소형 Run 125건 전체 replay·288회 평가·checksum, 대형 replay CANCELLED, console 오류 0·실제주문/인증 0을 확인했다. 최종 GitHub Actions 32917820261의 validate·browser·증거 upload가 PASS했다. | 활성 2.55GB 원장 전수 quick_check·6h·24h·Release ZIP NOT_RUN, 전략 자연표본 BASE 0~5건으로 수익성 NOT_PROVEN | 현재 revision 자연표본과 장기 PAPER 모니터 지속 |
| 38 | COMPLETE_WITH_LIMITS | 1b93414 | 사용자 진입 의도를 revision·CAS·idempotency로 자동 안전잠금과 분리하고, HTTP 전 동기 거래 cache와 writer 경합 replay 조회를 제거했다. Backend 371·frontend 54·fixture 17·Playwright desktop/tablet/mobile 3·lint·typecheck·build·PAPER safety·security·repository hygiene PASS다. 같은 실제 Run의 내부 시작은 165.615초에서 3.651초로 줄었고, 실제 기록 43건·replay Run 79개·source별 최신 결과 16개와 브라우저 정밀 100건 재생을 확인했다. GitHub Actions 32922393330의 validate 58초·browser 1분11초·증거 upload도 PASS했다. | 정밀 100건 첫 로딩 약 14.7초 PASS_WITH_LIMIT, 수익성 NOT_PROVEN, 활성 원장 quick_check·6h·24h·Release ZIP NOT_RUN | 현재 revision 자연표본과 장기 PAPER 모니터 지속 |
| 39~42 | COMPLETE_WITH_LIMITS | 067342c | 공개 5분봉 후보 6개 전부 기각, 완성 시간봉 K SHADOW와 A/D/E/H 퇴역, 계획별 최대보유, recovery schema 3, 거래 focus cache lock 복구를 구현했다. Backend 393·frontend 55·fixture 17·Playwright desktop/tablet/mobile 3·Ruff·mypy 93·ESLint·TypeScript·build·PAPER safety·security 126 source·repository hygiene PASS다. 실제 8870에서 기록 63건과 K 상세·XRP 재생의 entry·SL·TP1·TP2·actual exit를 눌러 확인했고 GitHub Actions 32932420777의 validate·browser·증거 업로드도 PASS했다. | 활성 원장 full quick_check는 runtime contention으로 NOT_COMPLETED, 수익성·미래 독립 OOS NOT_PROVEN, 6h·24h·Release ZIP NOT_RUN | K 자연 LIVE_PUBLIC 표본과 장기 PAPER 모니터 지속 |
| 43~46 | COMPLETE_WITH_LIMITS | e261e3f | 장중 후보와 K 독립 복제 실패를 보존하고 기본 ACTIVE 0·6 SHADOW·5 RETIRED, 15분 no-false-promotion Governor, 거래 결과 도달시간, 원장 우선 버전 병합, 전체 기록 기본값과 idle-gap replay 진행을 구현했다. Backend 402·frontend 56·fixture 17·Playwright desktop·tablet·mobile 3·Ruff·mypy 93·ESLint·TypeScript·build·PAPER safety·security 126 source·repository hygiene PASS다. 실제 8870에서 거래 63건, 현재 버전 0건, 상세의 과거 기록 없음, replay 1/7→7/7과 console 오류 0을 확인했다. 61.03초 LIVE는 +3,957 events·실행 p95 최대 44.068ms·queue/reconnect/gap/drop/fault/lock 0·실제주문/인증 0이다. 구현 commit `e261e3f`의 GitHub Actions 32941018295도 validate 1분7초·browser 1분40초·증거 업로드까지 PASS했다. | 현재버전 자연표본 0과 과거 1승62패로 수익성 NOT_PROVEN, active ledger full check·6h·24h·Release ZIP NOT_RUN, bundle 514.55kB 경고 | 현재 전략 버전 자연표본과 장기 PAPER 모니터 지속 |
| 47 | COMPLETE_WITH_LIMITS | 2cc68f0 | 현재 RSS·최고 RSS 분리, backend 405·frontend 57·fixture 17·Playwright 3·전체 정적·build·PAPER safety·security·repository hygiene와 구현·증거 GitHub Actions PASS다. 실제 8870은 같은 Run 복구, API/OS 현재 RSS 차이 0.609MB, 현재값 하락 중 최고값 유지, 122.455초 +9,028 events·queue/drop/fault/lock/주문/auth 0, 브라우저 문구·값 PASS다. | 수정 전 clean 5시간 00분 34초까지 PASS_WITH_LIMIT. 활성 2.798GB 원장 동시 full quick_check는 437초 결과 없이 queue 포화·drop 9,736으로 중단돼 무결성 NOT_COMPLETED. 수정 후 6h·24h·Release ZIP NOT_RUN, 수익성 NOT_PROVEN | 안전한 닫힌 snapshot/maintenance 원장검사와 장기 PAPER 모니터 지속 |
| 48 | COMPLETE_WITH_LIMITS | 820e8ac | 대형 원장 온라인 backup 상한, LaunchAgent 60초 유예, 닫힌 WAL checkpoint·APFS clone·다른 device 제한 전송·SHA-256·immutable full check와 LIVE fail-closed 감시를 구현했다. 실제 2,842,066,944byte 사본은 `quick_check=ok`·FK 0, 동일 Run 16.912초 복구, 재기동 후 event +28,348, queue 최대 22, 실행 p95 최대 189.040ms, 비계획 reconnect·gap·resync·drop·fault·buffer drop·critical·position·실제주문·인증 0으로 PASS했다. backend 423·frontend 57·fixture 17·Playwright 3·정적·build·PAPER safety·security·repository hygiene와 GitHub Actions 32977393998도 PASS했다. | 유지관리 localhost 중단 16.912초는 필요했다. 수정 후 6h·24h·Release ZIP NOT_RUN, 수익성 NOT_PROVEN | 장기 PAPER 모니터 지속 |
| 49 | COMPLETE_WITH_LIMITS | 61a15ce | 설치된 8870 PAPER 서비스만 읽는 비침습 observer, 누적 전략평가·적격신호 진단과 동적 11전략·22계좌 검사를 구현했다. 실제 같은 Run·프로세스 1,800.038초에서 event +158,346·전략평가 +486,276, 계획 rotation/reconnect 2/2, 비계획 reconnect·gap·resync·drop·fault·critical·주문·인증 0, queue 최대 23, 실행호가/체결 p95 최대 122.399/508.430ms, RSS 증가 95.610MB와 45개 검사 전부 PASS다. 실제 모바일 시스템·거래집중 화면과 48px control·overflow 0, GitHub Actions 32983734662도 PASS했다. | 적격신호·신규거래 0, 현재버전 BASE/STRESS 각 5건 비용후 손실로 수익성 NOT_PROVEN. 6h·24h·Release ZIP NOT_RUN | 비침습 6h·24h와 자연 LIVE_PUBLIC 표본을 별도 지속 |
| 50 | IN_PROGRESS | 482f334 | 로컬 미배포 소스에서 control·replay 신규 incident에 이전·새 상태, actor, 원인, 한국어 설명, Run·종목, 요청·응답 revision과 terminal reversibility를 정규화했다. 기존 snapshot·history·incident ID를 보존했고 targeted 2, control/replay/recovery/storage 67, backend 432, frontend 57, fixture 17, Playwright 3와 전체 정적·build·PAPER safety·security·repository hygiene가 PASS했다. | 설치 서비스는 아직 기준 commit을 실행 중이고 실제 배포 후 신규 정규 행·GitHub main·Actions는 NOT_RUN. 6h·24h observer는 기준 commit에서 IN_PROGRESS, 수익성 NOT_PROVEN | 기준 commit 장시간 관찰을 보존한 뒤 평평한 상태에서 배포·실제 신규 전환 감사·증거·GitHub 검증 |
| 51 | IN_PROGRESS | 0f5fd77 | 정책 퇴역 전략의 과거 SHADOW revision rollback 우회를 격리 API에서 HTTP 200으로 재현한 뒤 backend 422 fail-closed로 수정했다. 일반 사용자 OFF는 되돌릴 수 있게 정책 잠금을 별도 표시하고, 전략 설정·Governor·rollback·migration과 PAPER 진입 의도를 정규 감사했다. backend 433·frontend 58·fixture 18·Playwright 3·전체 정적·build·PAPER safety·security·repository hygiene PASS다. | 설치 서비스는 기준 commit이라 배포 후 신규 전략 전환 행·실제 8870 화면·GitHub main·Actions NOT_RUN. 6h·24h observer는 기준 commit에서 IN_PROGRESS, 수익성 NOT_PROVEN | 기준 장시간 관찰 뒤 평평한 원자 배포·실제 전략 설정 감사·브라우저·GitHub 검증 |
| 52 | IN_PROGRESS | eafbc60 | 시작 복구 성공·지연·실패·fixture의 정규 상태전환 감사와 초보자·고급진단 UI를 구현했다. 수정 전 2건의 실패를 재현한 뒤 표적 4, 관련 77, backend 436, frontend 59, Playwright desktop·tablet·mobile 3과 Ruff·mypy·ESLint·TypeScript·build·PAPER safety·security·repository hygiene를 통과했다. | 설치 서비스는 기준 commit을 실행 중이라 배포 후 신규 복구 행·실제 8870 화면·GitHub main·Actions NOT_RUN. 6h·24h observer는 기준 commit에서 IN_PROGRESS, 수익성 NOT_PROVEN | 기준 장시간 관찰 뒤 평탄 배포·실제 복구 감사·브라우저·GitHub 검증 |
| 53 | IN_PROGRESS | 9d9823a | 후보·진입·보호·청산 실제 lifecycle 행만 정규화하고 위험·중복·일시정지 진단 행은 기존 의미를 유지했다. 계좌·종목별 결정적 revision·ID, recovery schema v4, fixture parity와 초보자·고급진단 UI를 구현했다. 수정 전 backend 2·frontend 1 실패를 재현한 뒤 backend 437, frontend 60, fixture 18, Playwright desktop·tablet·mobile 3과 Ruff·mypy·ESLint·TypeScript·build·PAPER safety·security·repository hygiene를 통과했다. | 설치 서비스는 기준 commit이라 실제 신규 lifecycle 행·8870 화면·GitHub main·Actions NOT_RUN. 6h·24h observer는 기준 commit에서 IN_PROGRESS, 수익성 NOT_PROVEN | 기준 장시간 관찰 뒤 평탄 배포·실제 lifecycle 감사·브라우저·GitHub 검증 |
| 54 | IN_PROGRESS | 7d0bf16 | 11개 전략별 연구 계약과 API·한국어 상세 화면을 구현하고 모든 Source ID를 1차 근거 catalog와 대조했다. 수정 전 backend 1·frontend 1 실패를 재현한 뒤 backend 437, frontend 60, fixture 18, Playwright desktop·tablet·mobile 3과 Ruff·mypy·ESLint·TypeScript·build·PAPER safety·security·repository hygiene를 통과했다. | 설치 서비스는 기준 commit이라 실제 신규 계약 API·8870 화면·GitHub main·Actions NOT_RUN. 6h·24h observer는 기준 commit에서 IN_PROGRESS, 수익성 NOT_PROVEN | 기준 장시간 관찰 뒤 평탄 배포·실제 전략 계약 API·브라우저·GitHub 검증 |
| 55 | IN_PROGRESS | 1bfbd21 | 실제 8870에서 구형 backend와 개발 worktree의 신형 frontend가 섞여 `전략 → 자세히`가 빈 화면이 되는 결함을 재현했다. commit별 불변 release·manifest hash·원자 `current`·`CODEX_DEPLOY` rollback 기록과 버전 불일치 fail-closed UI를 구현했다. backend 441·frontend 62·Playwright release snapshot desktop·tablet·mobile 3·정적·타입·build·PAPER safety·security·repository hygiene가 PASS했다. | 설치 서비스는 observer 보존 때문에 아직 기준 commit이며 실제 LaunchAgent 전환·8870 새 hash·배포 후 원장·GitHub main·Actions NOT_RUN. 6h·24h observer IN_PROGRESS, 수익성 NOT_PROVEN | 6시간 기준 observer 완료와 평탄 상태 뒤 불변 릴리스 배포·실제 8870·원장·rollback·GitHub 검증 |
| 56 | IN_PROGRESS | d8e5bae | 기준 backend에 같은 기준 frontend를 복구해 실제 8870 전략 상세 빈 화면을 무중단 해소했다. 최상위 PAPER 안전 오류 경계와 물리 release backend import 고정·preflight를 구현했다. 수정 전 표적 2건이 실패했고 최종 backend 442·frontend 63·불변 snapshot Playwright desktop·tablet·mobile 3·정적·타입·build·PAPER safety·security·repository hygiene가 PASS했다. | 실제 8870은 기준 commit 정합성만 복구됐고 새 release 배포·LaunchAgent import·원장·screenshot·GitHub main·Actions NOT_RUN. 6h·24h observer IN_PROGRESS, 수익성 NOT_PROVEN | 6시간 기준 observer 완료와 평탄 상태 뒤 최종 불변 릴리스 배포·실제 import/hash·원장·브라우저·rollback·GitHub 검증 |
| 57 | IN_PROGRESS | 7b593cb | 실제 8870에서 거래 기록 75건과 저장 Run·정밀 이벤트·재생·일시정지를 직접 확인하고 48만 건 저우선순위 전략 검증을 시작했다. Run만 맞고 종목 범위가 다른 과거 checksum이 남을 수 있는 결함과 소문자 종목 필터 0건 결함을 재현해 `scope_symbol`·정규화·Run+종목 일치 표시를 구현했다. backend 442·frontend 64·불변 release Playwright desktop·tablet·mobile 3·정적·타입·build·PAPER safety·security·repository hygiene가 PASS했다. | 실제 48만 건 검증과 6h·24h observer IN_PROGRESS. 새 release 설치·8870 새 범위 UI·배포 후 원장·GitHub main·Actions NOT_RUN, 수익성 NOT_PROVEN | 실제 저장 replay 완료 결과와 6시간 기준 observer를 확인한 뒤 불변 릴리스 배포·원장·브라우저·rollback·GitHub 검증 |
| 58 | IN_PROGRESS | e33ef4e | 실제 485,283건 ONGUSDT replay 병행 중 설치 LIVE 처리 p95 약 23.938초·trade p95 약 11.216초·wide p95 약 11.071초, critical·entry lock과 공개 provider ping timeout, 비계획 reconnect 1건을 관찰해 작업을 수동 취소했다. operation은 `CANCELLED`, replay 결과는 없고 LIVE는 자동 복구했다. 재발 방지를 위해 경량 LIVE 안전감시와 cancellable worker 자동 종료, `REPLAY_ABORTED_LIVE_SAFETY`, 안전검사 뒤 parent-only 결과 저장을 구현했다. 표적·관련 backend 38, 전체 backend 448, frontend 64와 Ruff·mypy·ESLint·TypeScript·security·repository hygiene가 PASS다. | 첫 전체 replay는 FAIL/CANCELLED다. replay 없는 30분도 trade p95 1,343.622ms·flush 22.636초 상한 초과와 planned rotation 중 critical incident 1건으로 FAIL해 replay 단독 인과는 입증되지 않았다. 설치 배포, 동일범위 재시도, 브라우저·원장·GitHub main·Actions·6h·24h·Release ZIP은 IN_PROGRESS 또는 NOT_RUN이고 수익성은 NOT_PROVEN이다. | 기준 6시간 observer를 그대로 완료해 저장·회전 장시간 한계를 함께 보존한다. 평탄 상태에서 불변 배포한 뒤 자동 보호가 동일 범위를 안전하게 중단하거나 완료하는지 확인하고 새 6h·24h를 시작한다. |
| 59 | IN_PROGRESS | c2dca3b | replay 없는 30분에서 별도로 발생한 22.636초 flush와 planned rotation 8.027초 critical incident를 추적했다. 누적 최장 archive 588.476ms·ledger 66,179.757ms와 background policy 범위를 근거로 archive background와 FULL commit 정상 우선순위를 분리하고, 12개 정밀 종목 전체 fresh depth 전까지 회전 출력을 잠갔다. 단독 runtime import 순환도 수정했다. 수정 전 표적 2건과 import가 실패했고, 수정 뒤 표적 3·관련 84·전체 backend 450, frontend 64·fixture 18·release fixture Playwright 3·build safety·정적·보안·저장소 위생이 PASS다. commit `1530898` 불변 stage의 manifest·frontend hash·release backend import도 확인했다. | Playwright는 fixture UI 범위이고 실제 8870은 기준 commit이라 새 flush·rotation·import·release 활성화·LIVE_PUBLIC 브라우저 검증은 NOT_RUN이다. 기준 6h·24h IN_PROGRESS, 동일범위 replay·원장·GitHub·Release ZIP NOT_RUN, production bundle 500kB 경고와 수익성 NOT_PROVEN이다. | 기준 6시간 결과를 보존한 뒤 최종 clean commit을 불변 배포하고 실제 flush·planned rotation·브라우저를 확인한 다음 동일 485,283건 replay를 보호경로로 재시도한다. |
| 60 | IN_PROGRESS | WORKTREE | 로드된 LaunchAgent는 mutable worktree를 가리키지만 현재 runner는 release manifest를 요구해, 기준 서비스를 먼저 끄면 exit 75로 복구하지 못하는 순서 결함을 확인했다. 설치기에 현재 서비스를 유지한 채 불변 release·`current`·plist만 준비하는 `--prepare-only`를 추가했다. 수정 전 service contract 1 failed·7 passed, 수정 뒤 표적 8·전체 backend 451, Ruff·mypy·security·repository hygiene와 zsh syntax가 PASS다. | 실제 prepare-only·기준 bootout·닫힌 clone·다른 device 전수검사·새 release same-Run 복구는 기준 6h observer 종료 전이라 NOT_RUN이다. 24h 기준은 오염됐고 배포 후 새로 시작해야 하며 수익성은 NOT_PROVEN이다. | 6시간 결과와 flat 상태를 고정한 뒤 prepare-only→원장 유지관리→단일 새 릴리스 복구 순서로 실행하고 실제 8870·rotation·flush를 검증한다. |
| 61 | IN_PROGRESS | WORKTREE | 취소된 ONGUSDT 검증 범위가 485,283건이었지만 같은 LIVE Run이 계속 증가해 현재 재시도는 더 많은 이벤트를 읽는 재현성 결함을 확인했다. API·격리 worker·ReplayEngine에 고정 `event_limit`과 입력 전용 checksum을 추가하고 UI가 정밀 이벤트 시점 건수를 전송하도록 구현했다. 수정 전 표적 실패를 고정했고, 수정 뒤 backend 459·frontend 66·fixture 18·Playwright 3과 정적·보안·build·PAPER safety·repository hygiene 전체가 PASS다. | 취소된 과거 작업은 결과 checksum이 없어 소급 일치를 주장할 수 없다. 실제 485,283건 재시도·불변 배포·실제 8870은 NOT_RUN이고, bundle 500kB 경고와 수익성 NOT_PROVEN이 남아 있다. | clean commit과 불변 stage를 만들고 실제 설치 후 고정 485,283건의 입력 checksum·종단간 결과 또는 안전 자동중단을 검증한다. |
| 62 | IN_PROGRESS | WORKTREE | 기준 서비스에서 계획 재연결 뒤 전략 평가·저장이 멈추고 queue 4,096건 포화·누락이 계속 증가하는 사고를 재현했다. SQLite RLock 누수, sink 예외 후 consumer 종료, producer task liveness 누락, 저장잠금이 task 정지를 가리는 UI, START가 새 Run을 만드는 모순, soak가 소비 전진을 보지 않는 결함을 실패 우선으로 고정했다. lock 해제·task 유지·supervisor fail-closed·queue 저수위 복구·같은 Run 재연결·진단·장시간 gate를 구현했다. 실제 기준 6시간은 21,601.135초·720표본 뒤 queue 4,096·누락 239,541로 FAIL했고, 오염된 24시간은 21,566.902초 뒤 ABORTED_OPERATOR다. 수정 뒤 backend 459·frontend 66·fixture 18·Playwright 3과 전체 정적·보안·build가 PASS다. | 기준 사고의 정확한 첫 예외문자열은 이전 서비스가 보존하지 않아 직접 인과는 `STRONG_MATCH_NOT_DIRECTLY_LOGGED`다. 변경 후 배포·실제 8870 복구·planned rotation·flush·30분·6시간·24시간은 NOT_RUN이고 수익성은 NOT_PROVEN이다. | 변경 source·증거를 clean commit한 뒤 prepare-only→닫힌 원장 clone→same-Run 단일 전환으로 배포하고 실제 브라우저와 런타임을 검증한다. |
| 63 | IN_PROGRESS | WORKTREE | prepare-only로 commit `55cd097` 불변 릴리스와 plist를 준비하고 기존 PID가 유지되는 것을 확인했다. 실제 기준선은 평탄·PAPER·실주문 false·인증 false지만 `ENTRY_LOCKED`·`QUEUE_LIMIT_EXCEEDED`라 정상 전용 유지관리기가 단일 전환을 시작할 수 없었다. 이 두 기존 fail-closed 상태만 명시적으로 허용하고 나머지 위반은 거부하는 복구 계약을 추가했다. service contract 10, 전체 backend 461, Ruff, backend app 96 source·script mypy, security 131 source와 repository hygiene가 PASS다. | 실제 닫힌 원장 checkpoint·clone·다른 device 전수검사·same-Run 복구는 새 commit과 불변 stage를 갱신한 뒤 실행해야 한다. 전체 mypy 대상은 기존 계약대로 `backend/app` 96 source와 변경 script이며 tests 전체를 mypy 대상으로 오인하지 않는다. | clean commit·prepare-only를 다시 수행하고 `--allow-failed-runtime-recovery`로 단 한 번 전환한다. |
| 64 | IN_PROGRESS | WORKTREE | 첫 실제 유지관리는 기존 PID를 정상 종료하고 WAL 0·3,002,593,280 byte APFS clone·commit `a577e4d` 같은 Run 복구까지 성공했다. 그러나 clone을 다른 device로 복사하면서 새 LIVE가 같은 source device에 FULL commit해 critical lag가 발생했고 372MB 지점에서 `ABORTED_RUNTIME_SAFETY`로 멈췄다. 원본과 서비스는 보존됐고 실제 주문·포지션은 0이다. 전송·SHA-256을 서비스가 닫힌 동안 끝내고 재시작 뒤 다른 device quick-check만 감시하도록 순서를 수정했다. service contract 11, 전체 backend 462, Ruff, backend app 96 source·script mypy, security 131 source와 repository hygiene가 PASS다. | 첫 시도의 cross-device transfer와 integrity는 FAIL/NOT_RUN이다. 재시작 뒤 5분 관찰은 event·평가·consumer가 누락 없이 전진했지만 같은 source volume에서 전체 회귀를 병행해 flush 42.654초·checkpoint 38.309초로 FAIL했으므로 clean 성능 증거로 사용하지 않는다. 새 순서 실제 재시도와 무부하 관찰이 남아 있다. | clean commit을 prepare-only하고 실제 닫힌 전송→same-Run 복구→다른 device 전수검사를 수행한 뒤, 테스트 부하 없는 새 5분·30분 관찰로 저장 지연을 다시 판정한다. |
| 65 | IN_PROGRESS | 1adf0ba | 수정 순서의 실제 재시도는 WAL 0, 3,009,531,904 byte clone, cross-device 전송과 양쪽 SHA-256 일치, 같은 Run 불변 복구까지 PASS했다. 그러나 전수검사와 실제 browser pause·focus replay·전체 회귀를 겹쳐 `MANUALLY_PAUSED`를 `OPERATION_NOT_RUNNING`으로 감지했고 quick-check 전에 안전중단했다. | integrity는 `NOT_RUN`이고 성능은 `NOT_PROVEN_CONTAMINATED`다. failed verification copy는 감사용으로 보존했다. | 새 Wave 66 clean commit을 stage한 뒤 어떤 테스트·브라우저·replay도 겹치지 않고 유지관리 전수검사를 단독 재시도한다. |
| 66 | IN_PROGRESS | WORKTREE | pause·resume 즉시 pending 문구·중복 클릭 방지와 LIVE focus process 격리·정확 거래 조회·제한 비교를 구현했다. 표적 backend 2, 전체 backend 462, frontend 66, Ruff·mypy 96 source·ESLint·TypeScript·build·PAPER safety·security 131 source·repository hygiene가 PASS다. | 새 불변 릴리스 배포, 실제 pending 문구·focus 지연, clean integrity·30분·6시간·24시간·GitHub는 `NOT_RUN`이고 수익성은 `NOT_PROVEN`이다. | 문서·증거를 commit하고 clean immutable stage→단독 integrity→실제 browser→무부하 soak→고정 485,283 replay 순서로 진행한다. |
| 67 | IN_PROGRESS | WORKTREE | commit `715692c` 단독 전수검사는 전송·SHA-256·같은 Run 복구와 LIVE +68,229 events를 통과했지만 15분 planned rotation의 정상 `SAFETY_WAITING`을 `OPERATION_NOT_RUNNING`으로 오인해 중단했다. planned rotation 유예의 상태·lock 계약을 일치시켰고 수동 pause 거부를 고정했다. 표적 30, 전체 backend 464, Ruff·mypy·PAPER safety·security·hygiene가 PASS다. | quick-check·FK는 완료 전 중단돼 `NOT_RUN`이다. 수정 commit·불변 stage·실제 단독 재시도는 남아 있다. | 수정 commit을 새 불변 릴리스로 준비하고 동일 전체범위 유지관리를 무간섭으로 다시 실행한다. |
| 68 | IN_PROGRESS | 3e4e728 | Wave 67 integrity PASS 뒤 focus 비용 분류·선택적 쓰기·비교 조회·저우선순위 대기·history temp B-tree와 launchd 종료 인계·stage JSON 결함을 순차 수정했다. backend 467, frontend 67, Playwright 3과 전체 정적·build·PAPER safety·security·hygiene가 PASS다. 실제 불변 릴리스 같은 Run에서 기록 81건 308ms·첫 focus 2,223ms·API 1.051초와 0.190초·비용 합계·콘솔 오류 0을 확인했다. 무오염 300.029초는 event +23,229·평가 +80,232·consumer +23,229·queue 최대 1·처리 p95 42.443ms·trade p95 81.730ms·비계획 reconnect/gap/resync/drop/fault 0으로 PASS했다. | 30분 무오염 관찰, 고정 485,283 replay, 새 6h·24h, GitHub가 남았다. 수익성은 NOT_PROVEN이다. | 최종 증거 commit 불변 stage→30분 무간섭 관찰→LIVE 안전감시 병행 고정 replay→6h·24h→GitHub main·Actions·Release 순서로 계속한다. |
| 69-93 | COMPLETE_WITH_LIMITS | 30분 무오염 기준선은 1,800.037초·event +132,983으로 PASS했다. 이후 고정 485,283-event replay의 archive 읽기와 LIVE `synchronous=FULL` 저장이 같은 외장 I/O를 경쟁하는 현상을 여러 번 재현해 replay thread 제한·저우선순위·고정 durable prefix·chunk 경계를 추가했지만 대형 replay 시도는 완료 증거 없이 중단 또는 FAIL로 보존했다. 통계 cache 준비 전 수치 숨김, ledger worker 우선순위 10, WAL 4 flush 간격, 1,000-event 저장 batch를 적용한 최종 릴리스 `667ad7b`에서 backend 476·frontend 68·Ruff·mypy·lint·typecheck·build·PAPER safety·security가 PASS했다. 최종 300.031초는 event +21,706·평가 +79,224·queue 최대 1·처리/체결 p95 최대 55.290/90.192ms·flush 최대 14.831초·checkpoint 최대 8.274초·비계획 reconnect/gap/resync/drop/fault 0으로 PASS했다. 실제 브라우저의 ETHUSDT 125-event 전략 replay도 14.635초에 완료되어 입력 checksum·288 평가·실주문/인증 0과 다음 이벤트 전진을 확인했다. GitHub main `a08a14f`와 Actions `33049813379`의 validate·browser·증거업로드도 PASS했다. | 485,283-event 고정 replay 완료는 NOT_RUN, 변경 후 6h·24h는 NOT_RUN, 현재버전 BASE/STRESS 각 12건이고 전부 30건 미만·비용후 음수라 수익성 NOT_PROVEN이다. 기존 Vite 525.71kB 경고가 남았다. | SHADOW 6개를 같은 입력에서 계속 평가하고 30건·시간순 OOS·STRESS·강건성 gate 전에는 순위·ACTIVE 승격을 금지한다. 5개 RETIRED는 실패근거를 보존한다. 장시간 무간섭 관찰 뒤 대형 replay를 다시 수행한다. |
| 94 | IN_PROGRESS | 현재 전략버전 `LIVE_PUBLIC` 전략계좌 24건을 API와 실제 기록 화면에서 대조했다. 최소 보유 14.010초·중앙 28.080초·p90 44.868초·최대 46.368초이며 10초 미만은 0이라 1~3초 종료는 재발하지 않았다. 14건은 비용전 양수였지만 24건 전부 비용후 음수이고 TP1·TP2·STOP 도달은 0이라 수익성은 `NOT_PROVEN`이다. 거래 상세의 단일 목표가 표시를 TP1·TP2로 분리하고 과거 단일목표는 별도 문구로 보존했다. backend 476, frontend 69, fixture 18, lint·typecheck·build, Playwright 3이 PASS했고 commit `b1a8927` 불변 릴리스로 같은 Run을 복구해 실제 브라우저에서 79,162.28·79,528.75를 확인했다. GitHub main `10d56d2`와 Actions `33051706575`의 validate·browser·증거업로드도 PASS했다. | 6시간 관찰은 2026-08-27 16:51 KST에 시작해 `IN_PROGRESS`, 24시간과 485,283-event replay는 `NOT_RUN`이다. Vite 526.08kB 경고가 남아 있다. | 무간섭 6시간 관찰을 완료해 PASS/FAIL을 그대로 보존한다. 그 뒤 현재버전 자연표본과 비용·TP/SL 도달을 다시 평가하며 30건·OOS·STRESS·강건성 전에는 순위나 ACTIVE 승격을 하지 않는다. |
| 95 | COMPLETE_WITH_LIMITS | Wave 94 6시간 관찰을 2,464.693초에서 안전상 중단해 `ABORTED_OPERATOR`로 보존했다. 저장 대기가 24,735건까지 증가하고 처리/체결 p95가 1,080.879/3,334.171ms, flush/checkpoint가 41.236/57.324초까지 커진 결함을 재현했다. event-loop watchdog과 포지션 보호계약을 observer에 추가하고, 저장 적체 중 불필요한 checkpoint 연기·16MiB WAL 강제검사·10,000건 신규진입 안전잠금·2,000건 회복 기준을 구현했다. backend 484·frontend 71·fixture 18·Playwright 3·정적·build·PAPER safety·security가 PASS했다. commit `55d59a1`과 초기 dashboard 준비 전 거짓 버전잠금을 막은 `ddcd098`을 불변 배포했다. 실제 8870에서 거래 87행, 정밀 이벤트 로딩, 다음 이벤트 2/100, 재생·일시정지, 11전략 분석과 새 고급진단을 눌러 확인했다. 개선 후 300.044초는 event +25,596·평가 +81,828·queue 최대 1·처리/체결 p95 최대 71.449/139.369ms·저장 대기 최대 2,567건·재연결/gap/resync/drop/fault/실주문/인증 0으로 PASS했다. | 5분은 PASS지만 새 6시간·24시간은 실제 시간을 채우지 않았다. 적격신호·거래 증가 0, 현재버전 BASE/STRESS 각 12건·비용후 음수라 수익성은 `NOT_PROVEN`이다. Vite 526.86kB 경고와 485,283-event replay 미완료가 남아 있다. | 증거와 문서를 GitHub main에 동기화한 최종 불변 릴리스에서 새 6시간 무간섭 관찰을 시작한다. 그동안 replay·전체 테스트·빌드·원장 전수검사를 겹치지 않고, 30건·OOS·STRESS·강건성 전에는 순위나 ACTIVE 승격을 하지 않는다. |
| 96 | COMPLETE_WITH_LIMITS | 누적 event-loop 최대값을 새 관찰구간 결함으로 오인하던 observer를 관찰구간 500ms 초과 counter delta로 수정하고 고급진단에 횟수·시각·값을 추가했다. backend 485·frontend 71·fixture 18·Playwright 3·정적·build·PAPER safety·security가 PASS했고 commit `8da4fa6` 불변 릴리스와 실제 브라우저에서 새 진단 문구, 작동 중, PAPER·실제주문 0을 확인했다. 첫 300.047초는 실행경로 치명지연 1회·처리 p95 579.710ms로 FAIL을 보존했다. 재시작 없이 이어진 300.041초는 event +28,629·평가 +82,920·queue 최대 12·처리/체결 p95 최대 147.376/272.032ms·신규 치명지연·재연결·gap·drop·저장결함·실주문·인증 0으로 PASS했다. 새 CBR 자연 거래는 34.298초 보유했지만 BASE/STRESS 모두 비용후 손실이었다. | 잘못 시작한 210.311초 장시간 관찰과 첫 5분 FAIL은 삭제하지 않는다. 새 6시간·24시간은 실제 시간을 채우지 않았고, 현재버전 BASE/STRESS 각 13건·누적 -8.639132072/-15.666633704 USDT라 수익성은 `NOT_PROVEN`이다. 485,283-event replay와 Vite 527.10kB 경고가 남아 있다. | 코드·문서·증거를 GitHub main과 Actions에 동기화하고 같은 최종 릴리스에서 유효한 baseline을 확인한 뒤 새 6시간 무간섭 관찰을 시작한다. 30건·OOS·walk-forward·STRESS·강건성 전에는 순위·ACTIVE 승격을 하지 않는다. |
| 97 | COMPLETE_WITH_FAILURE_EVIDENCE | universe snapshot과 storage health를 시장 루프 밖 worker로 옮겼다. 최종 깨끗한 1,200.035초는 event +95,886·평가 +323,088·비계획 reconnect/gap/resync/drop/fault/critical 0이었지만 자연 거래 시 queue 최대 464·event-loop 최대 874ms가 발생해 FAIL로 보존했다. AGGRESSOR BTCUSDT LONG BASE/STRESS는 13.864초 뒤 EDGE_DECAY로 종료돼 1~3초 재발은 아니었고 비용후 모두 손실이었다. | queue와 event-loop gate가 실패했다. 현재버전 BASE/STRESS 각 14건·net -10.512252272/-17.957519704 USDT로 수익성 `NOT_PROVEN`, 6h·24h `NOT_RUN`이다. | 실행상태 원자 저장과 consumer 협력 양보를 구현하고 별도 깨끗한 관찰로 재검증한다. |
| 98 | COMPLETE_WITH_LIMITS | 실행상태를 한 원자 트랜잭션으로 저장하고 consumer 협력 양보를 추가했으나 첫 300.032초는 577ms 루프 지연 1회로 FAIL했다. dashboard 집계·JSON을 `to_thread`로 격리하고 LIVE 표시 메모리를 2,048건으로 제한한 commit `5f82e4e`에서 다음 300.032초와 1,200.036초가 연속 PASS했다. 최종 20분은 event +90,759·평가 +318,432·queue 최대 26·처리/체결 p95 최대 47.507/106.292ms·500ms 초과 루프 지연·비계획 reconnect·gap·resync·drop·fault·실주문·인증 0이었다. backend 496·frontend 71·Playwright desktop/tablet/mobile 3·정적·build·safety·security가 PASS했고 실제 브라우저의 정지·재개·전략·기록·재생·분석·설정·차트와 세 반응형 화면을 확인했다. GitHub Actions `33071478970`도 PASS했다. | 활성 3.1GB writer의 direct full SQLite 검사는 ADR-049 위반을 발견해 결과 전 중단했고 `NOT_RUN`이다. 6h·24h와 485,283-event replay는 `NOT_RUN`, 현재버전 각 14건·비용후 음수라 수익성은 `NOT_PROVEN`이다. 기존 Vite 527.10kB 경고가 남아 있다. | 문서·증거를 GitHub main에 동기화한 뒤 replay·build·원장 전수검사를 겹치지 않는 실제 6시간 무간섭 관찰을 시작한다. |
| 99 | IN_PROGRESS | 6caad21 | 첫 6시간 시도를 1,141.869초에서 중단해 critical lag +30·incident +1·최장 5,037.395ms를 보존했다. 같은 immutable Parquet에서 4종목 DEPTH 30건·1,502.087~1,717.235ms·2,145.405ms burst와 기존 non-stale 분류를 확인했다. 기존 1,500ms 기준은 유지하고 임계 초과 실행호가를 archive에 남기면서 최신호가·체결·피처·전략평가에서 격리하고 fresh depth에서만 data-gap을 해제했다. backend 497·관련 90·fixture 19·frontend 71·Playwright 3·lint·typecheck·build·PAPER safety·security·repository hygiene가 PASS했다. 같은 Run 불변 배포와 실제 브라우저 세 화면·console 0을 확인했고 새 300.037초는 event +29,420·평가 +83,160·queue 최대 28·처리/체결 p95 최대 54.568/152.271ms·critical·비계획 reconnect·gap·drop·fault·실주문·인증 0으로 PASS했다. GitHub main `ad37caf`과 Actions `33080439159`의 validate 69초·browser 71초·증거 업로드도 PASS했다. | 배포 뒤 실제 임계 지연 재발은 없어 격리 누적 0이며 고지연 격리 종단은 deterministic 회귀로 검증했다. 새 6h·24h는 `NOT_RUN`, 현재버전 BASE/STRESS 각 14건·비용후 음수라 수익성 `NOT_PROVEN`이다. | replay·build·원장 검사를 겹치지 않는 새 6시간 무간섭 관찰을 시작한다. 30건·OOS·walk-forward·STRESS·강건성 전에는 순위·ACTIVE 승격을 하지 않는다. |
| 100 | COMPLETE_WITH_LIMITS | 12e86d7 | 20 alpha×5 exit 100개를 사전등록하고 13 Run·2,690,582 event를 Train 6·Validation 2·봉인 Final OOS 5로 동결했다. Stage 1은 MICRO 20 EXECUTED·FAST 55/SWING 15 FAILED_PRESERVED·SIHO 10 BLOCKED, 계획계좌 200·관찰 180·실행 40·실패보존 140·차단 20·거래 77건을 보존했다. 선택·ACTIVE·LIVE SHADOW는 0이며 수익성 `NOT_PROVEN`이다. 부분 TP·runner·활성화형 trailing, fee-safe activation, 수신순 replay, data-health 잠금 자동복구를 구현했다. 기능 commit `7d58a1c…`를 불변 배포해 실제 자동복구·정지/재개·5화면·기록 91건·24-event 재생·차트 구간·전체화면·지표를 직접 확인했다. 1,800.028초는 event +148,304·평가 +483,036·queue 최대 16·처리/체결 p95 최대 95.911/137.366ms·critical·비계획 reconnect·gap·drop·fault·실주문·인증 0으로 PASS했다. 수동 진입 일시정지 유지관리를 최종 증거 commit `12e86d7…`에 고정했고 backend 647·frontend 72·fixture 19·Playwright 3·정적·build·PAPER safety·security·hygiene를 PASS했다. 같은 commit의 GitHub main·Actions `33143156840` validate·browser·증거 업로드도 PASS했다. | Final OOS·event/full PAPER replay·LIVE SHADOW는 후보 0으로 `NOT_RUN/BLOCKED_GATE`, 신규 trailing 자연표본·6h·24h는 `NOT_RUN`, SIHO timeline·frame·full-video 내용 검토는 0개로 `BLOCKED`다. 원장 cross-device SHA-256·동일 Run 복구는 PASS했지만 일반 운영 시도는 `POSITION_OPENED`, 수동 일시정지 시도는 `CRITICAL_LAG_INCIDENT`로 중단돼 `quick_check`·foreign key는 `NOT_RUN`이다. 수익성 `NOT_PROVEN`, 실자금 `NOT_READY`다. | 다음 full integrity는 더 빠른 별도 장치 또는 localhost를 전체 검사 동안 내릴 수 있는 명시적 유지관리 시간에만 수행한다. 30건·Final OOS·STRESS·강건성·6h·24h 전에는 순위·ACTIVE·실자금을 금지한다. |

| 101 | COMPLETE | 93de81d | GitHub Linux에서 현재 RSS 219.031MiB가 ru_maxrss peak 218.754MiB보다 큰 교차 계측으로 Actions `33143465472`가 backend 647건 중 1건 FAIL했다. peak를 현재 RSS 하한으로 보정하고 출처를 명시했다. 전용 2건×10회와 GitHub과 동일 전체 명령의 backend 648·frontend 72·build·PAPER safety·security·hygiene는 로컬 PASS다. 불변 release에서 동일 Run·작동 중·포지션 0·3초 event +228·peak≥current·실주문·인증 0을 확인했다. GitHub Actions `33143784984`의 validate 1분 5초·browser 1분 21초·증거 업로드도 PASS했다. | 원장 full check·6h·24h·수익성 경계는 Wave 100과 동일하게 `NOT_RUN`·`NOT_PROVEN`이다. | 메모리 현재/최대 불변조건을 유지하고 장기 PAPER 자연표본을 계속 보존한다. |
| 102 | COMPLETE_WITH_LIMITS | ef146de / 3e56425 | 배포 전 현재 개정 30건이 모두 EDGE_DECAY이고 가격손익 +1.532780 USDT보다 비용 33.260573416 USDT가 커 순손익 -31.727793416 USDT인 원인을 재현했다. 일반 관리청산을 30초 유예·복수 불리근거·실제 bid/ask·`max(0.25R, 왕복비용 R)` 가격악화·3초 지속으로 강화하고 전략 개정을 wave102로 분리했다. 거래기록·전략·진행거래·성과·종목별성과·안전설정·과거재생은 쉬운 한국어 핵심을 기본으로, 원시 ID·코드·연구진단은 접힌 상세로 옮겼다. backend 650·frontend 74·Playwright 3·정적·build·PAPER safety·security 142 source·hygiene가 PASS했다. 같은 Run 불변 릴리스와 실제 브라우저 전체/모바일, 거래상세·replay 진입/종료를 확인했고 60.046초는 event +4,285·평가 +15,900·queue 0·처리/체결 p95 최대 37.955/46.233ms·오류·실주문·인증 0으로 PASS했다. GitHub main `3e56425…`와 Actions `33153112970`의 validate 1분20초·browser 1분38초·증거업로드도 PASS했다. | 활성 writer snapshot은 30.197초와 60.405초 무진행으로 안전 중단해 full quick_check는 `NOT_RUN`이다. 새 개정 BASE/STRESS 자연표본은 0/0, 수익성 `NOT_PROVEN`, 실자금 `NOT_READY`, 6h·24h `NOT_RUN`이다. | 새 개정 자연표본을 기준 완화 없이 보존하고 30건·시간순 OOS·BASE/STRESS·강건성 gate 전에는 순위·ACTIVE·실자금을 금지한다. full integrity는 명시적 유지관리 시간이나 더 빠른 닫힌 사본에서만 재시도한다. |
| 103 | COMPLETE_WITH_LIMITS | 119a354 / 47cf13d / 1aec71a | 실제 같은 Run의 VWAP BASE·STRESS 각 12건이 UTC 3일에 4·7·1건으로 분산됐는데도 새 일자 후보 2회가 `MAX_DAILY_TRADES`로 거절된 원인을 재현했다. UTC 일간·월요일 주간 cursor, 후보·진입·종료 rollover, snapshot·복구 현재기간 재집계를 구현했다. backend 653·표적 최종 117·frontend 74·Playwright 3·Ruff·mypy 106 source·build·PAPER safety·security 142 source·hygiene가 PASS했다. 불변 릴리스 `47cf13d…`로 같은 Run을 복구한 뒤 VWAP BASE·STRESS 현재 UTC 일간계수 0/12, `realized_today=0`을 확인했다. 실제 화면의 작동 중·PAPER 실제주문 0·기록 3건·6개 감시/5개 퇴역·console 오류 0과 60.052초 event +5,254·평가 +16,500·queue 최대 6·처리/체결 p95 최대 49.277/64.500ms·런타임 위반 0을 확인했다. GitHub main `1aec71a…`의 Actions `33162070164`도 validate 1분 14초·browser 1분 27초·증거 업로드까지 PASS했다. | 활성 writer online snapshot은 43.991초 중 30.962초 무진행으로 중단해 full quick_check는 `NOT_RUN`. 6h·24h·수익성은 `NOT_RUN`·`NOT_PROVEN`, 실자금 `NOT_READY` | 같은 기준을 유지해 자연표본을 계속 수집하되 30건·OOS·STRESS·강건성 gate 전에는 순위·ACTIVE·실자금을 금지한다. full integrity는 명시적 유지관리 시간이나 더 빠른 닫힌 사본에서만 재시도한다. |
| 104 | IN_PROGRESS_WITH_BASELINE_FAILURE | 0f09703 | 수정 전 불변 릴리스 6시간을 실제 완료해 event +1,560,430·평가 +5,706,432·queue 최대 59·비계획 reconnect/gap/drop/fault 0을 확인했지만 처리 p95 1,032.383ms·loop 1,914ms·flush/checkpoint 24.263/30.508초·critical event/incident +46/+3으로 FAIL을 보존했다. 약 447KB dashboard snapshot과 HTTP/WS JSON을 1초 공용 캐시로 바꾸고 상태변경은 즉시 갱신했다. backend 655·frontend 74·fixture 21·Playwright 3·정적·build·PAPER safety·security·hygiene PASS 후 같은 Run의 불변 릴리스로 배포했다. 실제 화면 연결+HTTP 60회는 평균/최대 12.585/49.556ms·loop 500ms 초과 0, 깨끗한 300.021초는 event +19,094·평가 +78,048·queue 최대 1·처리/체결 p95 34.640/51.182ms·loop 최대 203ms·fault 0으로 PASS했다. 기록 25건·ETHUSDT 13-event replay 진입/종료·전략/분석/설정·console 0을 직접 확인했다. | 수정 후 5분만 PASS다. 새 6시간·24시간과 active ledger full check는 `NOT_RUN`; 현재버전 BASE/STRESS 13/12건·비용후 음수라 수익성 `NOT_PROVEN`, 실자금 `NOT_READY`다. | 코드·증거를 GitHub main과 Actions에 동기화한 같은 릴리스에서 새 6시간 관찰을 시작한다. 6시간 PASS 전 24시간을 시작하지 않고, 30건·OOS·STRESS·강건성 전 순위·ACTIVE·실자금을 금지한다. |
| 104 후속 | COMPLETE_WITH_POSTFIX_6H_FAILURE | 276c047 | 수정 후 6시간도 21,600.016초를 실제 채워 event +1,430,678·평가 +5,643,228·비계획 reconnect/gap/drop/fault 0이었지만 queue 최대 533·loop 1,505ms·500ms 초과 13회·flush/checkpoint 34.418/51.049초로 FAIL했다. BASE/STRESS 자연표본은 각각 4건 늘었지만 누적 비용후 손실이다. | 앞 행의 수정 후 6시간 `NOT_RUN`을 이 실패로 정정한다. 24시간·원장 full check는 `NOT_RUN`, 수익성 `NOT_PROVEN`, 실자금 `NOT_READY`다. | 짧은 회복구간을 6시간 PASS로 대체하지 않는다. 연구 replay·build·원장검사를 겹치지 않은 새 커밋에서 6시간을 다시 수행한다. |

| 105 | IN_PROGRESS | WORKTREE | 11개 전략·22개 독립 BASE/STRESS 계좌를 동결 13-Run·2,690,582-event의 같은 공개시장 입력에서 실제 PAPER 경로로 전수 비교하는 연구 CLI를 확인했다. 직접 실행이 기존 LIVE 자동중단 계약을 우회하는 빈틈을 막기 위해 nice 19·background taskpolicy·단일 thread 자식, 1초 안전감시, 신규 500ms event-loop 지연·프로세스 재시작·작동 정지·PAPER 이탈·포지션·비계획 재연결·gap·drop·저장 fault 즉시 중단, 임시 결과 폐기와 최종 불변조건 원자 확정을 구현했다. Ruff, 표적 11건과 실제 8870 parser가 PASS했다. | 현재 무간섭 6시간 observer가 진행 중이어서 동결 archive 현재 bytes 재검증과 2,690,582-event 전체 재생은 `NOT_RUN`이다. 전략별 30기회·70%·BASE/STRESS·비용후 기대값·PF·OOS·bootstrap·DSR·PBO 판정과 수익성은 `NOT_PROVEN`이다. | 6시간 observer의 실제 PASS/FAIL을 먼저 고정한다. 평탄 상태와 배포 후 phase 진단을 확인한 뒤 새 LIVE 안전 실행기로 전체 전수 재생하고, 불완전 결과를 순위에 사용하지 않는다. |
| 110 | IMPLEMENTED_VALIDATION_PASS_REPLAY_ABORTED | bd60745 | 해결결함 15개 누적 회귀계약, append-only 연구시험 이력, 같은 완료시험 중복차단, 실제 파라미터 변형 분리, 최대 10개 생존후보와 엄격한 교체 gate, 11전략 one-pass gate와 전략별 회계, archive replay 전역 자원잠금을 구현했다. backend 730건·frontend 76건·Playwright desktop/tablet/mobile 3건·lint·typecheck 108 source·build·PAPER safety·security 144 source·hygiene·회귀계약 15건을 PASS했다. 외부 연구는 중복·비용을 먼저 걸러 신규 Registry 복제 0개, OFI dollar-volume 가설 1개만 데이터계약 선행 상태로 사전등록했다. | 이전 worktree 코드로 시작한 13-Run 기준선은 4,394.737초·Run 7 진행 중 LIVE event-loop 740ms 1회를 감지해 `ABORTED_RUNTIME_SAFETY`, 부분 결과 제거로 종료했다. 결과 파일은 없고 수익성은 `NOT_PROVEN`, 6h·24h는 `NOT_RUN`, 실자금은 `NOT_READY`다. | 변경을 GitHub main에 동기화한 뒤 다른 부하를 겹치지 않고 같은 새 커밋에서 13-Run `NONE` 기준선과 `ALL_REGISTERED_STRATEGIES` TP1 후보를 순차 실행한다. 완료 결과만 비교하고 30기회·BASE/STRESS·OOS·강건성·독립 forward 전에는 순위·ACTIVE·실자금을 금지한다. |
| 113B | LIVE_PRIORITY_SMOKE_PASS_FULL_REPLAY_PENDING | a2ffd83 | 13-Run `NONE` 기준선은 2,690,582 event로 완료했고 수익성 `NOT_PROVEN`을 유지했다. TP1 전 전략 시도 두 건은 신규 500ms 초과를 감지해 안전 중단·부분 결과 제거했다. archive 검증을 1MiB·16MiB/s·LIVE 원장 우선 I/O와 CPU 25% 예산으로 바꾸고 단계 로그를 추가했다. hive 종목 호환성 실패를 보존한 뒤 수정한 1-Run·100-event smoke는 LIVE event +1,487·신규 500ms 초과 0·queue 최대 9·p95 29.417ms로 PASS했다. | 전체 13-Run TP1은 새 구현에서 아직 `NOT_RUN`, 30개 기회·BASE/STRESS·OOS·강건성·독립 forward와 수익성은 `NOT_PROVEN`, 실자금은 `NOT_READY`다. | 문서·실패·PASS 원본을 먼저 GitHub에 고정한 뒤 같은 clean commit에서 CPU25 전체 13-Run TP1을 실행한다. LIVE 안전 위반이면 즉시 중단하고 원인을 고친 뒤에만 구현 지문이 다른 재검증을 허용한다. |

Wave 104 구현·문서·원시 증거는 GitHub main `8fdf14f5a818294679d06c111aab8de4792fa816`에 동기화했고 Actions `33221372546`의 validate 1분 16초, browser 1분 21초와 브라우저 증거 업로드가 모두 PASS했다. 기존 Release `v0.2.0-paper-wave10`은 확인만 했고 Wave 104 Release는 만들지 않았다.

Wave 95 코드·문서·기계판독 증거는 GitHub main `3c5e4a9fc8cdeb8e7ae1ca9c265fa29ffe18449d`에 동기화했고 Actions `33056662395`의 validate 1분 4초, browser 1분 17초와 브라우저 증거 업로드가 모두 PASS했다.

Wave 96 코드·문서·기계판독 증거는 GitHub main `fa8fc09f47d7530beae774ae198c93e756b0c232`에 동기화했고 Actions `33059333016`의 validate 1분 15초, browser 1분 23초와 브라우저 증거 업로드가 모두 PASS했다.

Wave 98 코드 릴리스 `5f82e4e00f057c6a6bcb338d41b7a45a290cf63f`의 Actions `33071478970`이 PASS했다. 문서·원시 관찰·실제 브라우저 증거는 GitHub main 증거 커밋 `fa8f526a3f6c84de0c6f78abc3b937b552ad0300`에 동기화했고 그 커밋의 validate 1분 10초·browser 1분 42초와 증거 업로드도 Actions `33075575481`에서 모두 PASS했다.

## Wave 116F 거래 수명주기 설치 검증

- 상태는 `COMPLETE_WITH_LIMITS`다. 기록 수명주기 source를 포함한 불변 release
  `ce5b6499844bd0b4cb48e14789c3ab5f1f45d186`을 실제 8870 서비스에 설치했다.
- 현재 Run의 현재버전 33건은 API와 브라우저가 일치하고, 모든 버전 128건은 query-only
  SQLite 공동 1건·전략별 127건과 일치한다. 모든 Run·모든 버전 853건도 API와 브라우저가
  일치하며 과거 버전 820건을 보존한다.
- 실제 브라우저에서 진행 포지션 0건, 수동 새로고침, 5초 자동 확인, 공동·전략별·전체·과거
  필터와 ZECUSDT 진입·TP1·TP2·SL·실제 종료 replay를 확인했다. desktop·tablet·phone
  console 오류는 0이다.
- 현재 공개시장 이벤트와 전략 평가는 전진하지만 적격신호와 진행 포지션은 0이다. 따라서 새
  거래행 부재를 최신화 오류로 분류하지 않으며 신호 기준을 낮추지 않는다.
- E06 후보 계산은 LIVE 안전감시가 신규 500ms 초과 loop 지연 1회를 감지해 결과 미발행으로
  중단했다. 같은 지문의 재시도 전에 원인분리와 실제 구현·파라미터·데이터 변경이 필요하다.
- 남은 gate는 자연 비영 진행 포지션의 실제 화면 관찰, 설치 뒤 6시간·24시간, 안전한 닫힌
  원장 전수검사와 30개 고유 기회·BASE/STRESS·OOS·bootstrap·DSR·PBO·독립 forward다.
  수익성은 `NOT_PROVEN`, 실자금은 `NOT_READY`다.

## Wave 116G 거래 최신화 재확인과 E06 원인 귀속 준비

- 상태는 `COMPLETE_WITH_LIMITS`다. 75.024초 동안 같은 Run에서 event +5,763·전략평가
  +20,136, queue 최대 0, 비계획 재연결·누락·저장결함·신규 500ms 초과 0으로 실행 경로가
  계속 전진했다.
- 적격신호·진행 포지션·신규 완료거래는 모두 0이었다. 현재버전 33건, 현재 Run 모든 버전
  128건, 전체 853건이 유지됐으며 새 거래행 부재를 최신화 오류로 분류하지 않는다.
- 실제 브라우저에서 진행 0건·현재 범위 33건을 확인하고 수동 새로고침과 5초 자동 확인을
  다시 검증했다. 자연 비영 포지션의 진행→종료 화면은 `NOT_OBSERVED`다.
- E06 guard는 다음 안전중단 시 직전·사고 표본, 카운터 변화, 계획 회전·재연결 동시 변화와
  loop·시장단계·dashboard·flush·WAL 시각 차이를 보존한다. 시간 근접성만으로 원인을
  단정하지 않는다.
- 같은 구현·파라미터·데이터 replay는 반복하지 않는다. 새 500ms 사고는 아직
  `NOT_OBSERVED`, 수익성은 `NOT_PROVEN`, 실자금은 `NOT_READY`다.

## Wave 116H 거래기록·다중화면·계획회전 검증

- 상태는 `COMPLETE_WITH_LIMITS`다. localhost WebSocket per-message 압축과 정상 HTTP
  접근 로그를 지원 실행기에서 비활성화하고 E06 구현 지문에 런타임·연구 인프라 소스를 묶었다.
- source, GitHub main과 불변 설치 release는 `baf43d056...`로 일치하며 같은 Run에서
  LIVE 공개시장·PAPER 실행·실제 주문 0·인증 0을 유지한다.
- 실제 브라우저와 추가 읽기 전용 세 화면을 총 네 화면으로 900초 유지했다. 추가 화면은 각각
  879개 상태와 event +70,058을 받았고 압축 협상은 0이었다.
- 계획회전 포함 1,050.016초는 event +81,431·전략평가 +280,140, queue 최대 19,
  처리/체결 p95 67.682/92.143ms, 최대 loop 291ms로 PASS했다. 계획 1 = reconnect 1이며
  비계획 reconnect·gap·resync·drop·저장결함·buffer drop·500ms 초과는 0이다.
- 현재버전 기록 33건은 API와 실제 화면이 일치하고, 현재 Run 모든 버전 128건·전체 853건을
  유지했다. 자동·수동 확인 시각과 계획회전 뒤 화면 연결도 전진했다.
- 자연 적격신호·진행 포지션·신규 완료거래는 0이었다. 실제 자연 진행→종료는
  `NOT_OBSERVED`지만 결정론 회귀가 진행 3→1→0·완료 0→2→3을 고정한다.
- 다음 단계는 이번 런타임 인프라 변경으로 달라진 E06 구현 지문을 기록한 뒤 LIVE 안전감시 아래
  비용포함 후보를 재검증하는 것이다. 6시간·24시간은 `NOT_RUN`, 수익성은 `NOT_PROVEN`,
  실자금은 `NOT_READY`다.

## Wave 116I 후보 저장 비동기화와 거래화면 진단

- 상태는 `SOURCE_VALIDATION_PASS_INSTALL_PENDING`이다. 적격 후보 한 건마다 LIVE 판단 루프에서
  SQLite FULL 커밋을 수행하던 경로를 같은 이벤트의 원자 저장 배치로 옮겼다.
- 후보 저장 실패는 시장 입력을 버리지 않고 persistence fault와 신규진입 잠금으로 보존한다.
  저장 Run replay 직전에는 후보 buffer까지 명시적으로 flush한다.
- 요약·전략·거래기록 화면은 현재 진행 포지션, 시장판정, 진입조건 통과, 수동 새로고침과
  자동 확인시각을 함께 보여 무거래와 화면 정지를 구분한다.
- 수정 전 자연 `AGGRESSOR_FLOW_CONTINUATION_V1` BTCUSDT LONG 한 건은 약 900초 뒤
  MAX_HOLD로 종료됐다. BASE 순손익은 양수, STRESS는 비용후 음수였으며 한 건을 수익성으로
  해석하지 않는다.
- backend 793건, frontend 79건, Ruff, 정식 mypy 110 source, ESLint, TypeScript, build,
  PAPER safety, security 146 source, repository hygiene, 24개 누적 회귀계약과 반응형 Playwright
  3건은 PASS했다. 불변 설치와 실제 서비스·브라우저 검증 전까지 설치 상태는 진행 중이다.

## Wave 116J~116K 완성봉 추세 V2와 TP·SL 결판 청산

- 상태는 `COMPLETE_WITH_LIMITS`다. 완성 15분 눌림, 15분·30분 돌파 후 재확인,
  30분·1시간 재합류 네 전략을 SHADOW PAPER V2로 사전등록했다. 브라우저·런타임을
  검증한 구현 source와 당시 불변 release는 `5ed7e4c0...`로 일치했다.
- Registry는 15개·BASE/STRESS 30계좌다. 기존 검증 중 6개와 신규 4개, 총 10개 SHADOW를
  같은 공개시장 입력에서 LONG·SHORT 20경로로 동시에 평가한다. 실패 5개는 RETIRED/OFF로
  거래·근거·계좌를 보존한다.
- 신규 네 전략은 일반 미시구조 `EDGE_DECAY`, 900초 종료와 8~18시간 시간청산을 사용하지
  않는다. 구조 손절, TP1·TP2, 이익보호 방향의 stop 단축과 데이터·시스템 안전종료만 사용한다.
- 진행 중 봉, 최근 100봉 또는 1시간 50봉 gap, stale·sequence invalid 호가, 12bp 초과
  spread, 1초 공개흐름 미확인, 0.65~3.0 ATR 밖 구조 손절과 비용후 순손익비 1.20 미만을
  fail-closed한다.
- 최종 전체 backend 798건, frontend 83건, Ruff, 정식 mypy 110 source, ESLint,
  TypeScript, build, PAPER safety, security 146 source와 desktop·tablet·mobile Playwright
  3건은 PASS했다. 저장 미리보기 중복 읽기를 10초 공유하고 이전 화면 요청 취소·20초 안내·재시도를
  추가했다.
- 실제 설치 브라우저에서 500캔들 미리보기 3.342초, 정밀 100이벤트 3.399초, 다음 이벤트
  `1 / 100→2 / 100`, console 0을 확인했다. 현재버전 0건과 보존 과거 130건을 필터로 분리했고
  BTC 과거 거래의 진입·TP1·TP2·SL·실제 종료와 13-frame replay를 직접 조작했다.
- 최종 release의 120.011초 관찰은 event +7,759·전략평가 +52,620, queue 최대 1,
  처리·체결 p95 최대 23.210·55.960ms, loop 최대 156ms, fault·drop·gap·실제주문·인증 0으로
  PASS했다. 시작 거래 cache 42.667초와 wide 관찰 p95 1.891초는 후속 성능 한계다.
- 70%는 보장값이 아니다. 현재버전 자연표본 30개 전 순위 금지, BASE·STRESS·OOS·bootstrap,
  DSR·PBO·drawdown·집중도 전 수익성 `NOT_PROVEN`, 실자금 `NOT_READY`를 유지한다. 이번
  관찰의 신규 V2 자연표본은 BASE/STRESS 0/0, 6시간·24시간은 `NOT_RUN`이다.

## Wave 133 비대칭 추세 runner 연구

- 상태는 `COMPLETE_WITH_LIMITS`다. HYP-130의 30개 고정 진입에 +1R 활성화 뒤 이전 완성
  22봉 Chandelier ATR 3·4배를 결합한 60개 PAPER 후보를 결과 전에 commit `1ada60d`로
  고정했다.
- 고정 익절·부분익절·일반 근거약화·최대보유는 없고 최초 구조 손절, 불리하게 넓어지지 않는
  추적손절과 데이터 종료 미결만 허용한다. 현재 봉 미래참조, 동일 봉 손절 우선, 갭 손절의 더
  불리한 시가 체결을 결정론 회귀로 고정했다.
- 12종목 완성 4시간봉 148,824개와 펀딩 74,487개에서 완료거래 10,211개를 평가했다. 7개가
  walk-forward, 4개가 Train·Validation 동시 선발을 통과했지만 전체 강건성 통과는 0개다.
- 대표 수축돌파 ATR4 후보는 진단 OOS 75건, STRESS 승률 38.7%, payoff 2.400, 기대값
  +10.095 계좌 bp, 최대 9.670R의 양의 비대칭을 보였다. 하지만 bootstrap 하한 -4.372,
  DSR 0, 전체 PBO 0.80이라 수익성 `NOT_PROVEN`, 실자금 `NOT_READY`다.
- 전체 재실행 SHA는 일치했다. 연구와 전체 검사를 겹친 300초 guard의 loop 지연 1회 FAIL은
  보존했고, 연구만 겹친 분리 150초는 queue 19·처리/체결 p95 31.215/77.898ms·신규 500ms
  초과 0으로 PASS했다.
- Registry·LIVE SHADOW 변경은 0이다. 다음 단계는 네 규칙을 바꾸지 않은 다른 공개 perpetual
  venue 복제이며, 통과해도 실제 bid·ask 미래 SHADOW 전에는 승격하지 않는다.

## Wave 134 Bybit 비대칭 runner 외부 venue 복제

- 상태는 `COMPLETE_WITH_RESEARCH_GATE_FAILURE`다. HYP-131의 네 선발 규칙을
  commit `86f2c92`에서 고정한 뒤 Bybit 공개 linear 4시간봉·펀딩에 변경 없이
  복제했다.
- 12종목 완성봉 141,422개·펀딩 71,609개를 사용했고 종목별 gap은 모두 0이다.
  전체 재실행의 생성시각 제외 SHA-256은 일치했다.
- 네 후보 모두 BASE·STRESS 평균, 양의 왜도와 7.99R~28.61R 최대 승자를 보였지만
  bootstrap 95% 하한과 DSR을 모두 실패했다.
- 최선의 수축돌파 ATR4는 203건·STRESS 승률 38.4%·payoff 2.137·기대값
  +7.546 계좌 bp·최대 15.414R이었다. 그러나 양수 fold 4/7과 ETHUSDT 양의 기여
  54.7%로 시간·종목 집중 gate를 통과하지 못했다.
- 최초 300초 LIVE guard의 checkpoint 구간 내 미완료 `FAIL`은 보존했다. 완료 후 연구
  재실행을 겹친 180초 분리 guard는 event +13,024·평가 +79,420·queue 2·처리/
  체결 p95 28.213/79.395ms·오류·실주문·인증 0으로 PASS했다.
- 전체 backend 897건, frontend 83건, Ruff, mypy 112 source, frontend lint·typecheck·build,
  PAPER safety, security 148 source와 repository hygiene는 PASS했다.
- Registry·LIVE SHADOW 변경은 0, 수익성은 `NOT_PROVEN`, 실자금 준비는 `NOT_READY`다.
  다음 가설은 같은 Bybit 결과에 맞추지 않고 ADX 상승·DMI 방향·분산 규칙을
  사전등록한 뒤 아직 열지 않은 공개 venue 또는 미래 bid·ask SHADOW에서 검증한다.

## Wave 135 전략 결과표·정렬·홈 이동

- 상태는 `SOURCE_VALIDATION_PASS_INSTALL_WAITING_FOR_FLAT_PAPER`다.
- 전략 기본표를 초보자가 바로 판단할 승률·거래 수·비용 후 순손익·보유 중심으로 단순화한다.
- 데스크톱 표 머리글과 모바일 큰 버튼에 오름차순·내림차순, BASE·STRESS 비용 전환을 연결한다.
- 30건 미만이나 승률이 없는 전략이 잘못된 순위를 만들지 않게 하고 상세·설정·과거 기록은 삭제하지 않는다.
- `FlowScalper` 이름을 누르면 항상 시장 기본화면으로 돌아가게 한다.
- 단위·lint·typecheck·build·desktop·tablet·mobile E2E를 통과한 후 현재 PAPER 포지션이 자연 종료될 때까지 기존 8870 서비스를 유지한다.
- 포지션 0건에서만 커밋된 불변 릴리스를 설치하고 실제 8870 브라우저 클릭과 화면을 다시 검증한다.

## Wave 136 ADX·DMI 분산형 비대칭 추세 진단

- 상태는 `COMPLETE_WITH_RESEARCH_GATE_FAILURE`다.
- HYP-132 결과를 본 뒤 만든 적응 가설임을 명시하고 ADX 25, 3개 완성봉 동안 상승하는 ADX,
  방향 일치 DMI와 동일 종목 168시간 재진입 제한을 결과 전 commit `b8dd147`에서 고정했다.
- 네 후보는 같은 Bybit 공개 완성 4시간봉·펀딩, BASE 13bp·STRESS 25bp와 비대칭 runner
  규칙으로 한 번만 평가하고 결정론적 전체 재실행 SHA 일치를 요구한다.
- 결과가 좋아도 같은 자료의 적응 진단이므로 Registry·LIVE SHADOW 변경은 0으로 유지한다.
- 실패·초기 동시 LIVE guard의 500ms 초과 1회·분리 관찰 PASS를 모두 보존하고 원인으로 단정하지 않는다.
- 다음 Wave는 네 규칙을 바꾸지 않은 OKX 공개 완성 4시간봉·실제 펀딩 독립 복제다.

## Wave 137 거래기록 진입기회 묶음과 승률 기본 정렬

- 상태는 `SOURCE_PASS_INSTALL_WAITING_FOR_FLAT_PAPER`다.
- 같은 후보를 BASE·STRESS 독립 PAPER 비용 조건으로 계산한 두 원장 행은 삭제하지 않고,
  거래기록 기본 화면에서 한 진입기회로 묶어 비용별 순손익을 나란히 표시한다.
- 같은 비용 조건의 행이 중복되면 자동으로 묶지 않아 실제 원장 중복 가능성을 숨기지 않는다.
- 상세 화면에서 BASE·STRESS를 전환해 비용·종료·순손익과 리플레이 대상을 각각 확인한다.
- 전략 결과표는 기본 비용 승률 내림차순으로 시작하되, 30건 미만은 계속 `표본 부족 · 순위 제외`로
  표시하고 승률이 없는 전략은 마지막에 둔다.
- 현재 8870은 PAPER 포지션 2건을 보호 중이므로 강제 종료·재시작하지 않는다. 포지션 0건에서만
  커밋된 불변 릴리스를 설치하고 실제 8870 브라우저를 다시 검증한다.

## Wave 138 OKX 비대칭 추세 runner 고정 외부복제

- 상태는 `COMPLETE_WITH_RESEARCH_GATE_FAILURE`다.
- 먼저 현재버전 완료 PAPER 원장을 분해해 총 gross `+7.68109 USDT`보다 수수료
  `17.063736 USDT`와 슬리피지 `1.696577 USDT`가 커서 순손익이
  `-11.079223 USDT`인 비용 지배 상태를 확인했다. 같은 품질의 거래 수만 늘리는 것은
  개선 방향에서 제외한다.
- HYP-134에서 고정한 네 ADX·DMI 비대칭 추세 runner를 규칙 변경 없이 OKX USDT
  perpetual 12종목의 완성 4시간봉 83,232개와 실제 펀딩 41,645개에 복제한다.
- 모든 후보는 실제 공개자료, BASE 13bp·STRESS 25bp, 거래당 위험 40bp, 동시에 최대
  2포지션·일 2진입, 최초 구조손절과 +1R 뒤 Chandelier runner를 그대로 사용한다.
- 네 후보 모두 비용 후 평균·PF·양의 왜도와 최대 7.832R~14.899R 승자를 보였지만,
  bootstrap 95% 하한·DSR·시간순 안정성 gate를 실패했다. 외부복제 통과와 Registry·
  LIVE SHADOW 변경은 모두 0이다.
- 후속 연구는 실패 결과와 원장을 삭제하지 않고 별도 사전등록·독립 미래자료로만 진행한다.
  실제 bid·ask BASE/STRESS 자연표본 30건 전에는 후보를 승격하지 않는다.
- 첫 불변 릴리스 준비는 내부 임시볼륨 여유 204MiB에서 153MiB Git archive를 만들다
  exit 128로 중단됐다. 서비스 재시작은 0이었다. archive 임시파일을 외장 runtime release
  staging과 같은 볼륨에 생성하도록 수정하고 macOS service 계약 회귀 16건으로 고정한다.
- 실제 주문, private API, API Key, secret, wallet과 입출금은 계속 0이며 수익성은
  `NOT_PROVEN`, 실자금은 `NOT_READY`다.

## Wave 139 외장 전용 저장·대형 WAL 시작 복구

- 상태는 `SOURCE_VALIDATION_PASS_INSTALL_PENDING`이다.
- One Touch의 APFS sparsebundle을 256GiB 가변 이미지로 확장하고 소스·원장·시장자료·
  불변 릴리스·Python·cache·temp·로그를 외장에만 두는 설치 계약으로 교체한다.
- 내장에 남았던 ROBOM 전용 cache 3.0GB를 외장으로 복사·전수 대조한 뒤 제거하고,
  과거 Application Support 586MB는 외장 migration archive에 보존·원장검사한 뒤
  새 외장 서비스 복구가 통과할 때만 내장 사본을 제거한다.
- 5.207GB 활성 원장과 2.354GB WAL은 서비스 handle 0에서 DB·WAL·SHM을 복구본으로
  보존한 뒤 `TRUNCATE` checkpoint 0byte를 확인했다. 다른 물리 device 사본의
  SHA-256 일치, `quick_check=ok`, 외래키 위반 0을 실행 근거로 남긴다.
- 새 시작 경로는 64MiB 초과 WAL에서 open writer를 거부하고 동일 APFS clone을
  checkpoint보다 먼저 만든다. 어떤 실패도 localhost 성공으로 표시하지 않는다.
- 전체 backend 916건, frontend 87건, Ruff, mypy 112 source, ESLint, TypeScript,
  build, PAPER safety, security 148 source, 저장소 위생과 누적 회귀계약 30개는 PASS했다.
- 커밋된 불변 릴리스 설치, 동일 Run 복구, 실제 공개시장 event 전진,
  거래기록·다시보기·브라우저는 설치 후 별도 실행 검증한다.
- 실제 주문·private API·API Key·secret·wallet은 0을 유지한다. 수익성은
  `NOT_PROVEN`, 6시간·24시간은 실제 경과 전까지 `NOT_RUN`으로 유지한다.
