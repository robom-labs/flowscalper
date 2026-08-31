#!/bin/zsh
# 외장 sparsebundle을 연결하고 외장 로그로 불변 PAPER 서비스를 시작한다.
set -euo pipefail

WORKSPACE_MOUNT="__WORKSPACE_MOUNT__"
SPARSEBUNDLE_PATH="__SPARSEBUNDLE_PATH__"
RUNTIME_ROOT="__RUNTIME_ROOT__"
LOG_DIR="$RUNTIME_ROOT/logs"
SERVICE_LOG="$LOG_DIR/service.log"
ERROR_LOG="$LOG_DIR/service-error.log"
MAX_LOG_BYTES=10485760

if [[ ! -d "$WORKSPACE_MOUNT" ]]; then
  /usr/bin/hdiutil attach -nobrowse "$SPARSEBUNDLE_PATH"
fi
for mount_wait in {1..60}; do
  [[ -d "$RUNTIME_ROOT" ]] && break
  sleep 1
done
if [[ ! -d "$RUNTIME_ROOT" ]]; then
  exit 75
fi

mkdir -p "$LOG_DIR"
for log_file in "$SERVICE_LOG" "$ERROR_LOG"; do
  if [[ -f "$log_file" ]] && (( $(/usr/bin/stat -f %z "$log_file") >= MAX_LOG_BYTES )); then
    /bin/mv -f "$log_file" "$log_file.previous"
  fi
done
exec >>"$SERVICE_LOG" 2>>"$ERROR_LOG"

RUNNER="$RUNTIME_ROOT/current/scripts/run_macos_service.sh"
if [[ ! -f "$RUNNER" ]]; then
  echo "불변 PAPER 서비스 실행기가 없습니다: $RUNNER" >&2
  exit 75
fi
exec /bin/zsh "$RUNNER"
