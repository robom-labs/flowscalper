# 로컬 개발과 검증 명령을 한 인터페이스로 제공한다.
PYTHON := .venv/bin/python
UV := uv
PNPM := pnpm

.PHONY: setup dev run test lint typecheck build e2e fixture-demo network-smoke security-scan repo-hygiene soak-30m soak-6h soak-24h package-release clean-data-safe

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
	$(UV) run python scripts/network_smoke.py

security-scan:
	$(UV) run python scripts/security_scan.py

repo-hygiene:
	$(UV) run python scripts/check_repository_hygiene.py

soak-30m:
	$(UV) run python scripts/soak_live.py --duration-seconds 1800 --sample-seconds 10 --output evidence/WAVE07_SOAK_30M.json

soak-6h:
	./scripts/soak_6h.command

soak-24h:
	./scripts/soak_24h.command

package-release: build security-scan repo-hygiene
	$(UV) run python scripts/package_release.py

clean-data-safe:
	@echo "기존 Run 데이터는 자동 삭제하지 않습니다. 내보내기 후 명시적으로 파일을 선택하세요."
