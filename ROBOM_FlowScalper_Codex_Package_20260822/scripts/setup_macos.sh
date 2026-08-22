#!/bin/zsh
# macOS에서 고정 의존성과 정적 대시보드를 한 번에 준비한다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

command -v uv >/dev/null || { echo "uv가 필요합니다: https://docs.astral.sh/uv/"; exit 1; }
command -v node >/dev/null || { echo "Node.js 22.13 이상 또는 24 이상이 필요합니다."; exit 1; }
command -v pnpm >/dev/null || { echo "pnpm이 필요합니다: https://pnpm.io/installation"; exit 1; }
node -e 'const [major, minor] = process.versions.node.split(".").map(Number); if (!((major === 22 && minor >= 13) || major >= 24)) process.exit(1)' || {
  echo "Node.js 22.13 이상 또는 24 이상이 필요합니다."
  exit 1
}

export UV_LINK_MODE="${UV_LINK_MODE:-copy}"
uv sync --frozen --all-groups
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
uv run python scripts/migrate.py
echo "PASS: macOS 설치 완료"
echo "실행: ./scripts/run_macos.command"
