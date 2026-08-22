#!/bin/zsh
# 공개시장 PAPER supervisor의 6시간 자원·재연결 증거를 생성한다.
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
cd "$PROJECT_DIR"
uv run python scripts/soak_live.py \
  --duration-seconds 21600 \
  --sample-seconds 30 \
  --output evidence/WAVE07_SOAK_6H.json
