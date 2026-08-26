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
SUPPORT_DIR="$HOME/Library/Application Support/ROBOM FlowScalper"
TARGET_PLIST="$LAUNCH_AGENT_DIR/$LABEL.plist"
TEMPLATE_PLIST="$SOURCE_PROJECT_DIR/packaging/macos/$LABEL.plist"
RUNTIME_VENV="$SUPPORT_DIR/runtime-venv"
SERVICE_TARGET="gui/$USER_ID/$LABEL"
SOURCE_VOLUME_NAME="${SOURCE_PROJECT_DIR#/Volumes/}"
SOURCE_VOLUME_NAME="${SOURCE_VOLUME_NAME%%/*}"
if [[ "$SOURCE_PROJECT_DIR" == /Volumes/*/* ]]; then
  RUNTIME_ROOT="${ROBOM_RUNTIME_ROOT:-/Volumes/$SOURCE_VOLUME_NAME/05_RUNTIME/ROBOM_FlowScalper}"
else
  RUNTIME_ROOT="${ROBOM_RUNTIME_ROOT:-$SUPPORT_DIR}"
fi
MARKET_ARCHIVE_PATH="${ROBOM_MARKET_ARCHIVE_PATH:-$SOURCE_PROJECT_DIR/data/market-parquet-v6}"

if [[ ! -f "$TEMPLATE_PLIST" || ! -x "$SOURCE_PROJECT_DIR/.venv/bin/python" || ! -d "$SOURCE_PROJECT_DIR/frontend/node_modules" ]]; then
  echo "먼저 외장 저장소에서 ./scripts/setup_macos.sh를 실행해야 합니다." >&2
  exit 1
fi

mkdir -p "$LAUNCH_AGENT_DIR" "$SUPPORT_DIR" "$RUNTIME_ROOT"
if [[ ! -x "$RUNTIME_VENV/bin/python" ]]; then
  ditto "$SOURCE_PROJECT_DIR/.venv" "$RUNTIME_VENV"
fi
"$RUNTIME_VENV/bin/python" -c "import duckdb, fastapi, uvicorn"
STAGE_RESULT="$SUPPORT_DIR/latest-release-stage.json"
"$SOURCE_PROJECT_DIR/.venv/bin/python" "$SOURCE_PROJECT_DIR/scripts/stage_macos_release.py" \
  --source-root "$SOURCE_PROJECT_DIR" \
  --runtime-root "$RUNTIME_ROOT" \
  --market-archive "$MARKET_ARCHIVE_PATH" \
  --active-ledger-dir "$RUNTIME_ROOT/active-ledger" \
  --activate > "$STAGE_RESULT"
PROJECT_DIR="$RUNTIME_ROOT/current"
[[ -f "$PROJECT_DIR/release-manifest.json" ]] || { echo "불변 릴리스 준비에 실패했습니다." >&2; exit 1; }
escaped_project="${PROJECT_DIR//&/\\&}"
escaped_logs="${SUPPORT_DIR//&/\\&}"
sed -e "s|__PROJECT_DIR__|$escaped_project|g" -e "s|__LOG_DIR__|$escaped_logs|g" "$TEMPLATE_PLIST" > "$TARGET_PLIST"
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

if launchctl print "$SERVICE_TARGET" >/dev/null 2>&1; then
  launchctl bootout "$SERVICE_TARGET"
fi
launchctl bootstrap "gui/$USER_ID" "$TARGET_PLIST"
launchctl enable "$SERVICE_TARGET"
launchctl kickstart "$SERVICE_TARGET"

echo "PASS: 자동 실행 서비스 설치 완료"
echo "주소: http://127.0.0.1:8870/"
echo "로그: $SUPPORT_DIR"
echo "릴리스: $(cd "$PROJECT_DIR" && pwd -P)"
