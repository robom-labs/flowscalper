#!/bin/zsh
# 사용자가 Finder에서 더블클릭해 안전한 localhost PAPER 프로그램을 시작한다.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"
exec "$PROJECT_DIR/scripts/run_macos.command"
