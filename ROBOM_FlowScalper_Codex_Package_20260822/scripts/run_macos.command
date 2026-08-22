#!/bin/zsh
# macOS에서 localhost PAPER 서버를 시작하고 브라우저를 연다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

command -v uv >/dev/null || { echo "uv가 필요합니다: https://docs.astral.sh/uv/"; exit 1; }

if [[ ! -x .venv/bin/python || ! -f frontend/dist/index.html ]]; then
  "$SCRIPT_DIR/setup_macos.sh"
fi

export UV_LINK_MODE="${UV_LINK_MODE:-copy}"
export ROBOM_MODE="${ROBOM_MODE:-READY}"
export ROBOM_OPEN_BROWSER="${ROBOM_OPEN_BROWSER:-true}"
export ROBOM_PORT="${ROBOM_PORT:-$(uv run --frozen python scripts/select_local_port.py)}"
export ROBOM_DB_PATH="${ROBOM_DB_PATH:-$PROJECT_DIR/data/run-ledger.sqlite3}"
echo "데이터 위치: $ROBOM_DB_PATH"
uv run --frozen python scripts/run_server.py
