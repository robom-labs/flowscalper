#!/bin/zsh
# macOS 로그인 세션에서 외장 저장소의 localhost 서버를 고정 포트로 계속 실행한다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SUPPORT_DIR="$HOME/Library/Application Support/ROBOM FlowScalper"
INTERNAL_PYTHON="$SUPPORT_DIR/runtime-venv/bin/python"
PROJECT_VOLUME_NAME="${PROJECT_DIR#/Volumes/}"
PROJECT_VOLUME_NAME="${PROJECT_VOLUME_NAME%%/*}"
PROJECT_MOUNT="/Volumes/$PROJECT_VOLUME_NAME"
if [[ "$PROJECT_DIR" == /Volumes/*/* && -d "$PROJECT_MOUNT" ]]; then
  DEFAULT_ACTIVE_LEDGER_DIR="$PROJECT_MOUNT/05_RUNTIME/ROBOM_FlowScalper/active-ledger"
else
  DEFAULT_ACTIVE_LEDGER_DIR="$SUPPORT_DIR/active-ledger"
fi
ACTIVE_LEDGER_DIR="${ROBOM_ACTIVE_LEDGER_DIR:-$DEFAULT_ACTIVE_LEDGER_DIR}"

if [[ ! -f "$PROJECT_DIR/frontend/dist/index.html" ]]; then
  echo "외장 저장소의 실행환경 또는 프론트엔드 빌드가 준비되지 않았습니다: $PROJECT_DIR" >&2
  exit 75
fi
if [[ -x "$INTERNAL_PYTHON" ]]; then
  RUNTIME_PYTHON="$INTERNAL_PYTHON"
elif [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  RUNTIME_PYTHON="$PROJECT_DIR/.venv/bin/python"
else
  echo "Python 실행환경이 없습니다: $PROJECT_DIR" >&2
  exit 75
fi

cd "$PROJECT_DIR"
umask 077
mkdir -p "$ACTIVE_LEDGER_DIR" "$SUPPORT_DIR/python-cache"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export PYTHONPYCACHEPREFIX="$SUPPORT_DIR/python-cache"
export PYTHONUNBUFFERED=1
export ROBOM_MODE="READY"
export ROBOM_HOST="127.0.0.1"
export ROBOM_PORT="8870"
export ROBOM_OPEN_BROWSER="false"
export ROBOM_RELOAD="false"
export ROBOM_DB_PATH="${ROBOM_DB_PATH:-$ACTIVE_LEDGER_DIR/run-ledger.sqlite3}"
export ROBOM_MARKET_ARCHIVE_PATH="$PROJECT_DIR/data/market-parquet-v6"
export ROBOM_MIN_FREE_BYTES="5368709120"
export ROBOM_MIN_FREE_RATIO="0.04"

exec "$RUNTIME_PYTHON" "$PROJECT_DIR/scripts/run_server.py"
