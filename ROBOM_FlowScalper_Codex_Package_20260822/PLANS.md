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
| 37 | COMPLETE_WITH_LIMITS | LOCAL_VERIFIED | 전략 replay를 202 백그라운드 operation·상태·경과시간·취소·timeout으로 전환하고, 혼합 SQLite/Parquet 상한과 이벤트 구간 candle로 timeline을 제한했다. Backend 366·frontend 54·fixture 15·Playwright desktop/tablet/mobile 3·Ruff·mypy 92·ESLint·TypeScript·build·PAPER safety·security 125 source·repository hygiene PASS다. 실제 8870 브라우저 기록 37건 뒤 최종 API 39건까지 전진했고, 현재 Run timeline 100건 0.63초, 소형 Run 125건 전체 replay·288회 평가·checksum, 대형 replay CANCELLED, console 오류 0·실제주문/인증 0을 확인했다. | 활성 2.55GB 원장 전수 quick_check·6h·24h·Release ZIP NOT_RUN, 전략 자연표본 BASE 0~5건으로 수익성 NOT_PROVEN | GitHub main·Actions 증거 후 식별자 갱신 |
