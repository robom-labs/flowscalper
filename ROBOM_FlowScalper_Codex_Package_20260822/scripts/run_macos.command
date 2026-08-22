#!/bin/zsh
# macOS에서 localhost PAPER 서버를 시작하고 브라우저를 연다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

if [[ ! -x .venv/bin/python || ! -d frontend/dist ]]; then
  "$SCRIPT_DIR/setup_macos.sh"
fi

export ROBOM_MODE="${ROBOM_MODE:-READY}"
export ROBOM_OPEN_BROWSER=true
uv run python scripts/run_server.py
