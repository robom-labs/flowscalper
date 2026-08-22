#!/bin/zsh
# 공개시장 PAPER supervisor의 24시간 자원·rotation 증거를 생성한다.
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
cd "$PROJECT_DIR"
uv run python scripts/soak_live.py \
  --duration-seconds 86400 \
  --sample-seconds 60 \
  --output evidence/WAVE07_SOAK_24H.json
