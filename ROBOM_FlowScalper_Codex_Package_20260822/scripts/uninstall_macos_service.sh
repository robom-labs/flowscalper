#!/bin/zsh
# 설치된 FlowScalper LaunchAgent만 안전하게 해제하고 외장 저장 데이터는 보존한다.
set -euo pipefail

LABEL="kr.robom.flowscalper"
USER_ID="$(id -u)"
TARGET_PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ -f "$TARGET_PLIST" ]]; then
  launchctl bootout "gui/$USER_ID" "$TARGET_PLIST" 2>/dev/null || true
  /bin/rm -f "$TARGET_PLIST"
fi

echo "PASS: 자동 실행 서비스 해제 완료 · 거래 원장과 외장 저장소 파일은 보존됨"
