#!/bin/zsh
# FlowScalper를 로그인·재부팅·프로세스 종료 뒤 자동 복구되는 LaunchAgent로 설치한다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LABEL="kr.robom.flowscalper"
USER_ID="$(id -u)"
LAUNCH_AGENT_DIR="$HOME/Library/LaunchAgents"
SUPPORT_DIR="$HOME/Library/Application Support/ROBOM FlowScalper"
TARGET_PLIST="$LAUNCH_AGENT_DIR/$LABEL.plist"
TEMPLATE_PLIST="$PROJECT_DIR/packaging/macos/$LABEL.plist"
RUNTIME_VENV="$SUPPORT_DIR/runtime-venv"

if [[ ! -f "$TEMPLATE_PLIST" || ! -x "$PROJECT_DIR/.venv/bin/python" || ! -f "$PROJECT_DIR/frontend/dist/index.html" ]]; then
  echo "먼저 외장 저장소에서 ./scripts/setup_macos.sh를 실행해야 합니다." >&2
  exit 1
fi

mkdir -p "$LAUNCH_AGENT_DIR" "$SUPPORT_DIR"
if [[ ! -x "$RUNTIME_VENV/bin/python" ]]; then
  ditto "$PROJECT_DIR/.venv" "$RUNTIME_VENV"
fi
"$RUNTIME_VENV/bin/python" -c "import duckdb, fastapi, uvicorn"
escaped_project="${PROJECT_DIR//&/\\&}"
escaped_logs="${SUPPORT_DIR//&/\\&}"
sed -e "s|__PROJECT_DIR__|$escaped_project|g" -e "s|__LOG_DIR__|$escaped_logs|g" "$TEMPLATE_PLIST" > "$TARGET_PLIST"
chmod 600 "$TARGET_PLIST"
chmod 755 "$PROJECT_DIR/scripts/run_macos_service.sh"
plutil -lint "$TARGET_PLIST"

launchctl bootout "gui/$USER_ID" "$TARGET_PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$USER_ID" "$TARGET_PLIST"
launchctl enable "gui/$USER_ID/$LABEL"
launchctl kickstart -k "gui/$USER_ID/$LABEL"

echo "PASS: 자동 실행 서비스 설치 완료"
echo "주소: http://127.0.0.1:8870/"
echo "로그: $SUPPORT_DIR"
