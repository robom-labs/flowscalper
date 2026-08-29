# 로컬 개발과 검증 명령을 한 인터페이스로 제공한다.
PYTHON := .venv/bin/python
UV := uv
PNPM := pnpm
SIHO_YT_DLP ?= yt-dlp
SIHO_FFMPEG ?= ffmpeg
SIHO_ASR ?= whisper
SIHO_ASR_MODEL ?= small
SIHO_ASR_MODEL_DIR ?= data/research/siho/models
SIHO_ASR_DEVICE ?= cpu
SIHO_ASR_THREADS ?= 4

.PHONY: setup dev run test lint typecheck build e2e fixture-demo network-smoke
.PHONY: security-scan repo-hygiene regression-contracts soak-30m soak-6h soak-24h
.PHONY: service-soak-30m service-soak-6h service-soak-24h
.PHONY: ledger-verify-snapshot ledger-verify-maintenance package-release clean-data-safe
.PHONY: research-siho-video research-siho-current-strategy research-100-candidates
.PHONY: research-siho-review-assets research-100-dataset-freeze research-100-manifest
.PHONY: research-100-instruments research-100-screening
.PHONY: research-trailing-ablation research-walk-forward test-trailing-replay benchmark-100-candidates

setup:
	$(UV) sync --all-groups
	cd frontend && $(PNPM) install --frozen-lockfile

dev:
	ROBOM_MODE=READY ROBOM_RELOAD=true $(UV) run python scripts/run_server.py

run:
	ROBOM_MODE=$${ROBOM_MODE:-READY} $(UV) run python scripts/run_server.py

test:
	$(UV) run pytest
	cd frontend && $(PNPM) test

lint:
	$(UV) run ruff check backend
	cd frontend && $(PNPM) lint

typecheck:
	$(UV) run mypy
	cd frontend && $(PNPM) typecheck

build:
	cd frontend && $(PNPM) build
	REAL_TRADING=false $(UV) run python scripts/assert_build_safety.py

e2e: build
	$(UV) run pytest backend/tests/test_fixture_app.py
	cd frontend && $(PNPM) e2e

fixture-demo: build
	ROBOM_MODE=DEMO_FIXTURE $(UV) run python scripts/run_server.py

network-smoke:
	$(UV) run python scripts/network_smoke.py --output evidence/PHASE03_PUBLIC_MARKET_SMOKE.json

security-scan:
	$(UV) run python scripts/security_scan.py

repo-hygiene:
	$(UV) run python scripts/check_repository_hygiene.py

regression-contracts:
	$(UV) run python scripts/verify_regression_contracts.py

research-siho-video:
	$(UV) run python scripts/research_siho_video_index.py --hydrate --collect-captions
	$(UV) run python scripts/collect_siho_video_review_assets.py

research-siho-review-assets:
	$(UV) run python scripts/collect_siho_video_review_assets.py \
		--collect --resume \
		--yt-dlp "$(SIHO_YT_DLP)" \
		--ffmpeg "$(SIHO_FFMPEG)" \
		--asr-executable "$(SIHO_ASR)" \
		--asr-model "$(SIHO_ASR_MODEL)" \
		--asr-model-dir "$(SIHO_ASR_MODEL_DIR)" \
		--asr-device "$(SIHO_ASR_DEVICE)" \
		--asr-threads "$(SIHO_ASR_THREADS)"

research-siho-current-strategy:
	$(UV) run python scripts/export_siho_evidence_matrix.py

research-100-manifest:
	$(UV) run python scripts/export_strategy_100_manifest.py

research-100-dataset-freeze:
	$(UV) run python scripts/freeze_strategy_100_dataset.py

research-100-instruments:
	$(UV) run python scripts/collect_strategy_100_instruments.py

research-100-screening:
	$(UV) run python scripts/research_strategy_100_candidates.py

research-100-candidates: research-100-manifest research-100-dataset-freeze research-100-instruments research-100-screening

research-trailing-ablation: research-100-screening
	@test -s evidence/TRAILING_ABLATION.json

research-walk-forward: research-100-screening
	@test -s evidence/WALK_FORWARD_RESULTS.json
	@test -s evidence/MULTIPLE_TESTING_RESULTS.json

test-trailing-replay:
	$(UV) run pytest \
		backend/tests/test_trailing_state_machine.py \
		backend/tests/test_candidate_paper_portfolio.py \
		backend/tests/test_strategy_100_screening.py \
		backend/tests/test_strategy_100_screening_runner.py \
		backend/tests/test_strategy_100_secondary_reports.py

benchmark-100-candidates:
	$(UV) run python scripts/benchmark_strategy_100_candidates.py

soak-30m:
	$(UV) run python scripts/soak_live.py --duration-seconds 1800 --sample-seconds 10 --output evidence/PHASE03_SOAK_30M.json

soak-6h:
	./scripts/soak_6h.command

soak-24h:
	./scripts/soak_24h.command

service-soak-30m:
	$(UV) run python scripts/observe_running_service.py \
		--duration-seconds 1800 --sample-seconds 10 \
		--output evidence/WAVE49_RUNNING_SERVICE_SOAK_30M.json

service-soak-6h:
	$(UV) run python scripts/observe_running_service.py \
		--duration-seconds 21600 --sample-seconds 30 \
		--output evidence/WAVE49_RUNNING_SERVICE_SOAK_6H.json

service-soak-24h:
	$(UV) run python scripts/observe_running_service.py \
		--duration-seconds 86400 --sample-seconds 60 \
		--output evidence/WAVE49_RUNNING_SERVICE_SOAK_24H.json

ledger-verify-snapshot:
	@test -n "$${ROBOM_DB_PATH}" || (echo "ROBOM_DB_PATH가 필요합니다." >&2; exit 2)
	@test -n "$${ROBOM_LEDGER_SNAPSHOT_DIR}" || (echo "ROBOM_LEDGER_SNAPSHOT_DIR가 필요합니다." >&2; exit 2)
	$(UV) run python scripts/verify_ledger_snapshot.py --source "$${ROBOM_DB_PATH}" --snapshot-dir "$${ROBOM_LEDGER_SNAPSHOT_DIR}" --output evidence/WAVE48_LEDGER_SNAPSHOT_INTEGRITY.json

ledger-verify-maintenance:
	@test -n "$${ROBOM_DB_PATH}" || (echo "ROBOM_DB_PATH가 필요합니다." >&2; exit 2)
	@test -n "$${ROBOM_LEDGER_SNAPSHOT_DIR}" || (echo "ROBOM_LEDGER_SNAPSHOT_DIR가 필요합니다." >&2; exit 2)
	@test -n "$${ROBOM_LEDGER_VERIFICATION_DIR}" || (echo "ROBOM_LEDGER_VERIFICATION_DIR가 필요합니다." >&2; exit 2)
	$(UV) run python scripts/verify_macos_ledger_maintenance.py --source "$${ROBOM_DB_PATH}" --snapshot-dir "$${ROBOM_LEDGER_SNAPSHOT_DIR}" --verification-dir "$${ROBOM_LEDGER_VERIFICATION_DIR}" --output evidence/WAVE48_MACOS_LEDGER_MAINTENANCE.json

package-release: build security-scan repo-hygiene
	$(UV) run python scripts/package_release.py

clean-data-safe:
	@echo "기존 Run 데이터는 자동 삭제하지 않습니다. 내보내기 후 명시적으로 파일을 선택하세요."
