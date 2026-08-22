# 로컬 개발과 검증 명령을 한 인터페이스로 제공한다.
PYTHON := .venv/bin/python
UV := uv
PNPM := pnpm

.PHONY: setup dev run test lint typecheck build e2e fixture-demo network-smoke clean-data-safe

setup:
	$(UV) sync --all-groups
	cd frontend && $(PNPM) install --frozen-lockfile

dev:
	ROBOM_MODE=FIXTURE_OFFLINE $(UV) run uvicorn backend.app.main:app --host 127.0.0.1 --port 8765 --reload

run:
	ROBOM_MODE=$${ROBOM_MODE:-FIXTURE_OFFLINE} $(UV) run uvicorn backend.app.main:app --host 127.0.0.1 --port 8765

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

e2e:
	$(UV) run pytest backend/tests/test_fixture_app.py

fixture-demo: build
	ROBOM_MODE=FIXTURE_OFFLINE $(UV) run uvicorn backend.app.main:app --host 127.0.0.1 --port 8765

network-smoke:
	$(UV) run python scripts/network_smoke.py

clean-data-safe:
	@echo "기존 Run 데이터는 자동 삭제하지 않습니다. 내보내기 후 명시적으로 파일을 선택하세요."

