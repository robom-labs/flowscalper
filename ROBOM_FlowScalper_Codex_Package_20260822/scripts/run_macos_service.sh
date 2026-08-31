#!/bin/zsh
# macOS 로그인 세션에서 외장 저장소의 localhost 서버를 고정 포트로 계속 실행한다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"
PROJECT_VOLUME_NAME="${PROJECT_DIR#/Volumes/}"
PROJECT_VOLUME_NAME="${PROJECT_VOLUME_NAME%%/*}"
PROJECT_MOUNT="/Volumes/$PROJECT_VOLUME_NAME"
if [[ "$PROJECT_DIR" != /Volumes/*/* && -z "${ROBOM_RUNTIME_ROOT:-}" ]]; then
  echo "외장 APFS 실행 릴리스를 찾지 못했습니다: $PROJECT_DIR" >&2
  exit 75
fi
RUNTIME_ROOT="${ROBOM_RUNTIME_ROOT:-$PROJECT_MOUNT/05_RUNTIME/ROBOM_FlowScalper}"
SUPPORT_DIR="$RUNTIME_ROOT/support"
CACHE_DIR="$RUNTIME_ROOT/cache"
TMP_DIR="$RUNTIME_ROOT/tmp"
RUNTIME_PYTHON="${ROBOM_RUNTIME_PYTHON:-$SUPPORT_DIR/runtime-venv/bin/python}"
DEFAULT_ACTIVE_LEDGER_DIR="$RUNTIME_ROOT/active-ledger"
ACTIVE_LEDGER_DIR="${ROBOM_ACTIVE_LEDGER_DIR:-$DEFAULT_ACTIVE_LEDGER_DIR}"
RELEASE_MANIFEST="$PROJECT_DIR/release-manifest.json"

if [[ ! -f "$PROJECT_DIR/frontend/dist/index.html" ]]; then
  echo "불변 실행 릴리스의 프론트엔드가 준비되지 않았습니다: $PROJECT_DIR" >&2
  exit 75
fi
if [[ ! -f "$RELEASE_MANIFEST" ]]; then
  echo "불변 실행 릴리스 manifest가 없습니다: $RELEASE_MANIFEST" >&2
  exit 75
fi
if [[ ! -x "$RUNTIME_PYTHON" ]]; then
  echo "외장 Python 실행환경이 없습니다: $RUNTIME_PYTHON" >&2
  exit 75
fi
MANIFEST_VALUE='import json,sys; print(json.loads(open(sys.argv[1], encoding="utf-8").read())[sys.argv[2]])'
RELEASE_COMMIT="$($RUNTIME_PYTHON -c "$MANIFEST_VALUE" "$RELEASE_MANIFEST" commit)"
MANIFEST_MARKET_ARCHIVE="$($RUNTIME_PYTHON -c "$MANIFEST_VALUE" "$RELEASE_MANIFEST" market_archive_path)"
MANIFEST_ACTIVE_LEDGER="$($RUNTIME_PYTHON -c "$MANIFEST_VALUE" "$RELEASE_MANIFEST" active_ledger_dir)"
ACTIVE_LEDGER_DIR="${ROBOM_ACTIVE_LEDGER_DIR:-$MANIFEST_ACTIVE_LEDGER}"
MARKET_ARCHIVE_PATH="${ROBOM_MARKET_ARCHIVE_PATH:-$MANIFEST_MARKET_ARCHIVE}"
[[ -d "$MARKET_ARCHIVE_PATH" ]] || { echo "공개시장 archive가 없습니다: $MARKET_ARCHIVE_PATH" >&2; exit 75; }

cd "$PROJECT_DIR"
umask 077
mkdir -p "$ACTIVE_LEDGER_DIR" "$CACHE_DIR/python" "$CACHE_DIR/xdg" "$CACHE_DIR/uv" "$TMP_DIR"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export PYTHONPYCACHEPREFIX="$CACHE_DIR/python"
export XDG_CACHE_HOME="$CACHE_DIR/xdg"
export UV_CACHE_DIR="$CACHE_DIR/uv"
export TMPDIR="$TMP_DIR/"
export PYTHONUNBUFFERED=1
export PYTHONNOUSERSITE=1
export PYTHONPATH="$PROJECT_DIR"
BACKEND_PACKAGE_ROOT="$("$RUNTIME_PYTHON" -c 'from pathlib import Path; import backend; print(Path(backend.__file__).resolve().parent)')"
if [[ "$BACKEND_PACKAGE_ROOT" != "$PROJECT_DIR/backend" ]]; then
  echo "backend가 불변 실행 릴리스 밖에서 로드됐습니다: $BACKEND_PACKAGE_ROOT" >&2
  exit 75
fi
export ROBOM_HOST="127.0.0.1"
export ROBOM_PORT="8870"
export ROBOM_OPEN_BROWSER="false"
export ROBOM_RELOAD="false"
export ROBOM_RELEASE_COMMIT="$RELEASE_COMMIT"
export ROBOM_RELEASE_ISOLATED="true"
export ROBOM_DB_PATH="${ROBOM_DB_PATH:-$ACTIVE_LEDGER_DIR/run-ledger.sqlite3}"
"$RUNTIME_PYTHON" "$PROJECT_DIR/scripts/recover_oversized_wal.py" \
  --source "$ROBOM_DB_PATH" \
  --snapshot-root "$RUNTIME_ROOT/maintenance-snapshots" \
  --output "$SUPPORT_DIR/startup-ledger-recovery.json" \
  --max-wal-bytes 67108864
export ROBOM_MODE="${ROBOM_MODE:-$($RUNTIME_PYTHON scripts/select_service_mode.py "$ROBOM_DB_PATH")}"
export ROBOM_MARKET_ARCHIVE_PATH="$MARKET_ARCHIVE_PATH"
export ROBOM_MIN_FREE_BYTES="5368709120"
export ROBOM_MIN_FREE_RATIO="0.04"

exec "$RUNTIME_PYTHON" "$PROJECT_DIR/scripts/run_server.py"
