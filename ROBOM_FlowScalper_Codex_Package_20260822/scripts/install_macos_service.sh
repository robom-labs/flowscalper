#!/bin/zsh
# FlowScalper를 로그인·재부팅·프로세스 종료 뒤 자동 복구되는 LaunchAgent로 설치한다.
set -euo pipefail

PREPARE_ONLY="false"
if [[ "${1:-}" == "--prepare-only" ]]; then
  PREPARE_ONLY="true"
  shift
fi
if (( $# != 0 )); then
  echo "사용법: $0 [--prepare-only]" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"
LABEL="kr.robom.flowscalper"
USER_ID="$(id -u)"
LAUNCH_AGENT_DIR="$HOME/Library/LaunchAgents"
TARGET_PLIST="$LAUNCH_AGENT_DIR/$LABEL.plist"
TEMPLATE_PLIST="$SOURCE_PROJECT_DIR/packaging/macos/$LABEL.plist"
SERVICE_TARGET="gui/$USER_ID/$LABEL"
SOURCE_VOLUME_NAME="${SOURCE_PROJECT_DIR#/Volumes/}"
SOURCE_VOLUME_NAME="${SOURCE_VOLUME_NAME%%/*}"
WORKSPACE_MOUNT="${ROBOM_WORKSPACE_MOUNT:-/Volumes/$SOURCE_VOLUME_NAME}"
DEFAULT_EXTERNAL_HOME="/Volumes/One Touch/ROBOM_AUTOTRADING/FlowScalper_v0.2_20260822"
EXTERNAL_HOME="${ROBOM_EXTERNAL_HOME:-$DEFAULT_EXTERNAL_HOME}"
SPARSEBUNDLE_PATH="${ROBOM_WORKSPACE_SPARSEBUNDLE:-$EXTERNAL_HOME/ROBOM_FlowScalper_Workspace.sparsebundle}"
RUNTIME_ROOT="${ROBOM_RUNTIME_ROOT:-$WORKSPACE_MOUNT/05_RUNTIME/ROBOM_FlowScalper}"
SUPPORT_DIR="$RUNTIME_ROOT/support"
LOG_DIR="$RUNTIME_ROOT/logs"
CACHE_DIR="$RUNTIME_ROOT/cache"
RUNTIME_VENV="$SUPPORT_DIR/runtime-venv"
PYTHON_BASE="$SUPPORT_DIR/python-base"
SERVICE_LOG="$LOG_DIR/service.log"
ERROR_LOG="$LOG_DIR/service-error.log"
MARKET_ARCHIVE_PATH="${ROBOM_MARKET_ARCHIVE_PATH:-$SOURCE_PROJECT_DIR/data/market-parquet-v6}"

if [[ "$SOURCE_PROJECT_DIR" != "$WORKSPACE_MOUNT"/* ]]; then
  echo "자동 서비스 소스는 외장 APFS 작업공간 안에 있어야 합니다: $SOURCE_PROJECT_DIR" >&2
  exit 1
fi
if [[ "$RUNTIME_ROOT" != /Volumes/*/* || "$EXTERNAL_HOME" != /Volumes/*/* ]]; then
  echo "런타임과 sparsebundle은 외장 볼륨 경로여야 합니다." >&2
  exit 1
fi
if [[ ! -d "$SPARSEBUNDLE_PATH" ]]; then
  echo "외장 APFS sparsebundle이 없습니다: $SPARSEBUNDLE_PATH" >&2
  exit 1
fi
if [[ ! -f "$TEMPLATE_PLIST" || ! -x "$SOURCE_PROJECT_DIR/.venv/bin/python" || ! -d "$SOURCE_PROJECT_DIR/frontend/node_modules" ]]; then
  echo "먼저 외장 저장소에서 ./scripts/setup_macos.sh를 실행해야 합니다." >&2
  exit 1
fi

mkdir -p "$LAUNCH_AGENT_DIR" "$SUPPORT_DIR" "$LOG_DIR" "$CACHE_DIR"
SOURCE_PYTHON="$SOURCE_PROJECT_DIR/.venv/bin/python"
SOURCE_PYTHON_BASE="$($SOURCE_PYTHON -c 'import sys; print(sys.base_prefix)')"
PYTHON_BINARY="$($SOURCE_PYTHON -c 'import sys; print(f"python{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ ! -x "$PYTHON_BASE/bin/$PYTHON_BINARY" ]]; then
  ditto "$SOURCE_PYTHON_BASE" "$PYTHON_BASE"
fi
if [[ ! -x "$RUNTIME_VENV/bin/python" ]]; then
  ditto "$SOURCE_PROJECT_DIR/.venv" "$RUNTIME_VENV"
fi
if [[ -L "$RUNTIME_VENV/bin/python" ]]; then
  unlink "$RUNTIME_VENV/bin/python"
  ln -s "$PYTHON_BASE/bin/$PYTHON_BINARY" "$RUNTIME_VENV/bin/python"
fi
"$SOURCE_PYTHON" -c \
  'import pathlib,sys; path=pathlib.Path(sys.argv[1]); rows=path.read_text().splitlines(); path.write_text("\n".join((f"home = {sys.argv[2]}" if row.startswith("home = ") else row) for row in rows) + "\n")' \
  "$RUNTIME_VENV/pyvenv.cfg" "$PYTHON_BASE/bin"
"$RUNTIME_VENV/bin/python" -c "import duckdb, fastapi, uvicorn"
STAGE_RESULT="$SUPPORT_DIR/latest-release-stage.json"
"$SOURCE_PROJECT_DIR/.venv/bin/python" "$SOURCE_PROJECT_DIR/scripts/stage_macos_release.py" \
  --source-root "$SOURCE_PROJECT_DIR" \
  --runtime-root "$RUNTIME_ROOT" \
  --market-archive "$MARKET_ARCHIVE_PATH" \
  --active-ledger-dir "$RUNTIME_ROOT/active-ledger" \
  --activate > "$STAGE_RESULT"
"$RUNTIME_VENV/bin/python" -c \
  'import json,sys; payload=json.loads(open(sys.argv[1], encoding="utf-8").read()); assert payload["status"] == "ACTIVATED"' \
  "$STAGE_RESULT"
PROJECT_DIR="$RUNTIME_ROOT/current"
[[ -f "$PROJECT_DIR/release-manifest.json" ]] || { echo "불변 릴리스 준비에 실패했습니다." >&2; exit 1; }
RUNNER_SCRIPT="$PROJECT_DIR/scripts/run_macos_service.sh"
[[ -f "$RUNNER_SCRIPT" ]] || { echo "불변 PAPER 서비스 실행기가 없습니다: $RUNNER_SCRIPT" >&2; exit 1; }
"$SOURCE_PYTHON" - "$TEMPLATE_PLIST" "$TARGET_PLIST" "$RUNNER_SCRIPT" "$SERVICE_LOG" "$ERROR_LOG" <<'PY'
from pathlib import Path
from sys import argv
from xml.sax.saxutils import escape

source, target = map(Path, argv[1:3])
payload = source.read_text(encoding="utf-8")
for placeholder, value in zip(
    ("__RUNNER_SCRIPT__", "__SERVICE_LOG__", "__ERROR_LOG__"),
    argv[3:],
    strict=True,
):
    payload = payload.replace(placeholder, escape(value))
target.write_text(payload, encoding="utf-8")
PY
xattr -d com.apple.provenance "$TARGET_PLIST" 2>/dev/null || true
chmod 600 "$TARGET_PLIST"
chmod 755 "$(cd "$PROJECT_DIR" && pwd -P)/scripts/run_macos_service.sh"
plutil -lint "$TARGET_PLIST"

if [[ "$PREPARE_ONLY" == "true" ]]; then
  echo "PASS: 불변 PAPER 릴리스와 LaunchAgent 준비 완료 · 실행 서비스 유지"
  echo "현재 서비스는 원장 유지관리 또는 명시적 설치 전까지 재시작하지 않았습니다."
  echo "릴리스: $(cd "$PROJECT_DIR" && pwd -P)"
  exit 0
fi

service_pid=""
if service_snapshot="$(launchctl print "$SERVICE_TARGET" 2>/dev/null)"; then
  service_pid="$(printf '%s\n' "$service_snapshot" | awk '$1 == "pid" && $2 == "=" { print $3; exit }')"
  launchctl bootout "$SERVICE_TARGET"
fi
if [[ -n "$service_pid" ]]; then
  for shutdown_wait in {1..60}; do
    if ! kill -0 "$service_pid" 2>/dev/null; then
      break
    fi
    sleep 1
  done
  if kill -0 "$service_pid" 2>/dev/null; then
    echo "기존 PAPER 서비스가 60초 안에 안전 종료되지 않았습니다: PID $service_pid" >&2
    exit 5
  fi
fi
bootstrap_succeeded="false"
for attempt in 1 2 3; do
  if launchctl bootstrap "gui/$USER_ID" "$TARGET_PLIST"; then
    bootstrap_succeeded="true"
    break
  fi
  if (( attempt < 3 )); then
    echo "LaunchAgent 등록이 아직 정리 중입니다. ${attempt}/3 재시도합니다." >&2
    launchctl bootout "$SERVICE_TARGET" >/dev/null 2>&1 || true
    sleep 1
  fi
done
if [[ "$bootstrap_succeeded" != "true" ]]; then
  echo "LaunchAgent 등록이 3회 연속 실패했습니다: $TARGET_PLIST" >&2
  exit 5
fi
launchctl enable "$SERVICE_TARGET"
launchctl kickstart "$SERVICE_TARGET"

EXPECTED_RELEASE_COMMIT="$("$RUNTIME_VENV/bin/python" -c \
  'import json,sys; print(json.loads(open(sys.argv[1], encoding="utf-8").read())["commit"])' \
  "$PROJECT_DIR/release-manifest.json")"
service_ready="false"
for readiness_wait in {1..180}; do
  if dashboard_payload="$(curl -fsS --max-time 2 http://127.0.0.1:8870/api/dashboard 2>/dev/null)"; then
    if printf '%s' "$dashboard_payload" | "$RUNTIME_VENV/bin/python" -c \
      'import json,sys
payload=json.load(sys.stdin)
expected=sys.argv[1]
status=payload["status"]
system=payload["system"]
operation=payload["operation_status"]
assert system["release_commit"] == expected
assert system["release_isolated"] is True
assert status["market_data_state"] == "LIVE"
assert status["execution_state"] == "PAPER"
assert status["real_orders_enabled"] is False
assert status["auth_required"] is False
assert operation["market_observation_active"] is True
assert operation["automatic_recovery"] is True
assert float(system["lag_p95_ms"]) <= 500.0
assert float(system["trade_lag_p95_ms"]) <= 1000.0
assert system["persistence_worker_warmed"] is True
assert int(system["persistence_flush_count"]) >= 4
assert float(system["persistence_flush_last_ms"]) <= 20000.0
assert int(system["persistence_fault_count"]) == 0
assert int(system["persistence_buffer_dropped"]) == 0
assert system["storage_entry_allowed"] is True' \
      "$EXPECTED_RELEASE_COMMIT" 2>/dev/null; then
      service_ready="true"
      break
    fi
  fi
  sleep 1
done
if [[ "$service_ready" != "true" ]]; then
  echo "PAPER 서비스가 180초 안에 안전한 LIVE 준비 상태가 되지 않았습니다." >&2
  echo "로그를 확인하세요: $LOG_DIR" >&2
  exit 6
fi

"$SOURCE_PROJECT_DIR/.venv/bin/python" "$SOURCE_PROJECT_DIR/scripts/stage_macos_release.py" \
  --runtime-root "$RUNTIME_ROOT" \
  --prune-only > "$SUPPORT_DIR/latest-release-prune.json"
"$RUNTIME_VENV/bin/python" -c \
  'import json,sys; payload=json.loads(open(sys.argv[1], encoding="utf-8").read()); assert payload["status"] == "PASS"' \
  "$SUPPORT_DIR/latest-release-prune.json"

echo "PASS: 자동 실행 서비스 설치 및 안전한 LIVE 준비 완료"
echo "주소: http://127.0.0.1:8870/"
echo "로그: $LOG_DIR"
echo "릴리스: $(cd "$PROJECT_DIR" && pwd -P)"
