# ROBOM FlowScalper v0.2 Package Manifest

이 패키지는 구현 지시서만 있는 초기 계약이 아니라 빌드된 프런트엔드, PAPER backend, 실행기, 테스트, 문서, 검증 증거를 포함하는 0.2.0-paper 릴리스 소스다.

## AI·개발자 시작점

- `00_AI_HANDOFF_먼저읽기.md`는 제품 목적, 사용자 요구, 기능별 코드·문서·테스트 지도, 안전 경계, 검증 상태와 다음 작업 완료 기준을 한곳에 정리한다.
- `01_GPT_업그레이드_방향_요청프롬프트_KO.txt`는 GitHub를 읽을 수 있는 GPT에게 업그레이드 방향과 Codex 실행 프롬프트를 요청할 때 그대로 복사한다.
- `RELEASE_NOTES_v0.2_WAVE10.md`는 현재 배포물의 기능·검증·checksum을 요약한다.
- `FINAL_UPGRADE_EVIDENCE.md`는 PASS·NOT_RUN·BLOCKED를 구분한 최종 증거다.
- `VERSION`은 현재 제품 버전의 단일 원본이고 `CHANGELOG.md`는 과거 버전의 중요한 변화만 짧게 남긴다.
- `docs/18_VERSIONING_AND_UPGRADE_POLICY_KO.md`와 ADR-009는 최신 소스 한 벌·tag·Release·migration archive로 반복 업그레이드를 정리하는 규칙이다.

## 사용자 실행기

- `ROBOM_FlowScalper.app`
- `ROBOM_FlowScalper.command`
- `scripts/run_macos.command`
- `scripts/run_windows.bat`

## 개발·안전 지침

- `AGENTS.md`
- `PLANS.md`
- `IMPLEMENT.md`

Detailed specifications:

- `docs/01_PRODUCT_REQUIREMENTS.md`
- `docs/02_ARCHITECTURE.md`
- `docs/03_MARKET_DATA_AND_VENUES.md`
- `docs/04_UNIVERSE_SELECTION.md`
- `docs/05_STRATEGY_SPEC.md`
- `docs/06_PAPER_EXECUTION_ENGINE.md`
- `docs/07_POSITION_AND_EXIT_MANAGEMENT.md`
- `docs/08_RISK_MANAGEMENT.md`
- `docs/09_DASHBOARD_UI_UX.md`
- `docs/10_STORAGE_REPLAY_ANALYTICS.md`
- `docs/11_SECURITY_PRIVACY.md`
- `docs/12_TEST_PLAN.md`
- `docs/13_ACCEPTANCE_CRITERIA.md`
- `docs/14_BUILD_AND_RELEASE.md`
- `docs/15_FAILURE_RECOVERY.md`
- `docs/16_MODEL_CALIBRATION.md`
- `docs/17_GLOSSARY.md`
- `docs/18_VERSIONING_AND_UPGRADE_POLICY_KO.md`
- `docs/19_STRATEGY_LEAGUE_SPEC_KO.md`
- `docs/20_RESEARCH_FOUNDATIONS_AND_ADAPTATION.md`
- `docs/adr/ADR-010-compact-market-workspace-full-catalog-strategy-symbol-analytics.md`
- `docs/adr/ADR-011-runtime-latency-partitioning-and-ledger-replay-transitions.md`
- `docs/adr/ADR-012-explicit-operation-state-and-orderbook-top-cache.md`
- `docs/adr/ADR-013-cost-viable-event-time-strategy-gates.md`
- `docs/adr/ADR-014-position-churn-and-independent-strategy-statistics.md`
- `docs/adr/ADR-015-duration-stable-runtime-and-chart-fullscreen.md`
- `docs/adr/ADR-016-depth-normalized-flow-and-multilevel-fair-price-shadow-strategies.md`
- `docs/adr/ADR-017-current-strategy-version-performance-scope.md`
- `docs/adr/ADR-018-replay-cpu-budget-focus-cache-and-venue-clock.md`
- `docs/adr/ADR-019-ofi-return-confluence-shadow-strategy.md`
- `docs/adr/ADR-020-monotonic-venue-clock-and-rotation-recovery.md`
- `docs/adr/ADR-021-book-slope-asymmetry-shadow-strategy.md`
- `docs/adr/ADR-022-snapshot-plan-input-reuse.md`
- `docs/adr/ADR-023-process-isolated-market-archive.md`
- `docs/adr/ADR-024-bounded-active-ledger-and-volume-safety.md`
- `docs/adr/ADR-025-bounded-live-dashboard-and-deep-capacity.md`
- `docs/adr/ADR-026-executable-book-trade-lag-and-strategy-visibility.md`
- `docs/adr/ADR-038-strategy-governor-lifecycle-and-audit.md`
- `docs/adr/ADR-039-preregistered-intraday-research-and-runtime-separation.md`
- `docs/adr/ADR-040-graceful-shutdown-and-service-intent-recovery.md`
- `docs/adr/ADR-041-planned-rotation-depth-warmup.md`
- `docs/adr/ADR-042-nonblocking-history-and-replay-preview.md`
- `docs/adr/ADR-043-observable-cancellable-bounded-replay.md`
- `docs/adr/ADR-044-revisioned-entry-intent-and-nonblocking-history-startup.md`
- `docs/adr/ADR-045-cost-aware-hourly-trend-shadow-and-evidence-retirement.md`
- `docs/adr/ADR-046-best-effort-focus-cache-under-durable-writer.md`
- `docs/adr/ADR-047-strategy-survival-governor-and-outcome-timing.md`
- `docs/adr/ADR-048-current-and-peak-process-memory.md`
- `docs/adr/ADR-049-closed-cross-device-ledger-integrity.md`
- `docs/adr/ADR-050-noninvasive-running-service-soak.md`

## Supporting contracts

- `config/*.yaml`
- `schemas/*.json`
- `templates/*.md`
- `OFFICIAL_REFERENCES.md`
- `CHECKLIST_FOR_USER.md`
- `THIRD_PARTY_NOTICES.md`
- `THIRD_PARTY_LICENSES/`

## 사용자 문서와 증거

- `00_사용법_먼저읽기.md`
- `RUNBOOK_LIVE_SHADOW_PAPER.md`
- `STRATEGY_CATALOG_KO.md`
- `UI_USER_GUIDE_KO.md`
- `MIGRATION_NOTES_v0.2.md`
- `SOAK_TEST_REPORT.md`
- `FINAL_UPGRADE_EVIDENCE.md`
- `design-qa.md`
- `evidence/PHASE03_PUBLIC_MARKET_SMOKE.json`
- `evidence/PHASE03_SOAK_30M.json`
- `evidence/PHASE03_INTEGRATED_LIVE_POSTFIX_180S.json`
- `evidence/PHASE03_ACTUAL_UI_SIMULATION.json`
- `evidence/PHASE03_ACTUAL_LIVE_MOBILE_390x844.png`
- `evidence/PHASE03_UI_DEMO_MOBILE_390x844.png`
- `evidence/PHASE03_ACTUAL_FOCUS_REPLAY_DESKTOP_1408x900.png`
- `evidence/PHASE03_ACTUAL_FOCUS_REPLAY_TABLET_820x1180.png`
- `evidence/PHASE03_ACTUAL_FOCUS_REPLAY_MOBILE_390x844.png`
- `evidence/PHASE04_START_STATUS_AND_SOAK.json`
- `evidence/screenshots/phase04-start-ready-mobile.png`
- `evidence/screenshots/phase04-start-running-mobile.png`
- `evidence/PHASE06_POSITION_CHURN_AND_STRATEGY_STATISTICS.json`
- `evidence/PHASE07_FULL_RUNTIME_AUDIT.json`
- `evidence/PHASE08_EIGHT_STRATEGY_AND_REPLAY_ISOLATION.json`
- `evidence/PHASE09_PUBLIC_MARKET_SMOKE.json`
- `evidence/PHASE09_CURRENT_STRATEGY_VERSION_SCOPE.json`
- `evidence/PHASE10_REPLAY_LIVE_ISOLATION.json`
- `evidence/WAVE21_OFI_RETURN_AND_REPLAY_QA.json`
- `evidence/WAVE22_CLOCK_ROTATION_QA.json`
- `evidence/WAVE23_BOOK_SLOPE_STRATEGY_QA.json`
- `evidence/WAVE24_RUNTIME_STALL_QA.json`
- `evidence/WAVE25_STORAGE_RUNTIME_UI_QA.json`
- `evidence/WAVE34_EXISTING_STRATEGY_RESEARCH.json`
- `evidence/WAVE34_INTRADAY_RESEARCH.json`
- `evidence/WAVE34_INTRADAY_RESEARCH.html`
- `evidence/WAVE34_FULL_AUDIT_QA.json`
- `evidence/WAVE35_ROTATION_WARMUP_QA.json`
- `evidence/WAVE39_PUBLIC_TREND_RESEARCH.json`
- `evidence/WAVE40_PUBLIC_HOURLY_TREND_DIAGNOSTIC.json`
- `evidence/WAVE41_PUBLIC_COST_AWARE_TREND_DIAGNOSTIC.json`
- `evidence/WAVE42_STRATEGY_POLICY_AND_REPLAY_QA.json`
- `evidence/WAVE42_TRADE_REPLAY_ACTUAL_EXIT.png`
- `evidence/WAVE42_STRATEGY_POLICY_ACTUAL.png`
- `evidence/wave48-ledger-integrity/actual-cross-device-maintenance-integrity.json`
- `evidence/WAVE49_PUBLIC_MARKET_SMOKE.json`
- `evidence/WAVE49_RUNNING_SERVICE_SOAK_30M.json`
- `evidence/WAVE49_RUNNING_SERVICE_AND_UI_QA.json`
- `evidence/screenshots/wave49-actual-system-mobile-415x734.png`
- `evidence/screenshots/wave49-actual-trade-replay-mobile-415x734.png`
- `scripts/observe_running_service.py`
- `scripts/verify_ledger_snapshot.py`
- `scripts/verify_macos_ledger_maintenance.py`
- `scripts/research_public_trend_candidates.py`
- `scripts/research_public_hourly_trend_diagnostic.py`
- `evidence/screenshots/wave21-live-market-1280x720.png`
- `evidence/screenshots/wave21-live-strategies-1280x720.png`
- `evidence/screenshots/wave21-live-strategies-full.png`
- `evidence/screenshots/phase09-current-version-strategy-detail-actual-chrome.jpg`
- `evidence/screenshots/phase09-current-version-performance-actual-chrome.jpg`
- `evidence/screenshots/phase09-current-version-strategy-symbol-actual-chrome.jpg`
- `evidence/screenshots/phase09-current-version-performance-{desktop,tablet,mobile}.png`
- `evidence/screenshots/phase09-current-version-strategy-symbol-{desktop,tablet,mobile}.png`

## Visual references

- `ui_reference/reference_dark_dashboard.jpg`
- `ui_reference/reference_trade_chart.jpg`
- `ui_reference/README.md`
