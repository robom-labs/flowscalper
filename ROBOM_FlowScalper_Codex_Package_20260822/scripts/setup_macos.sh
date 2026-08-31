#!/bin/zsh
# macOS에서 고정 의존성과 정적 대시보드를 한 번에 준비한다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"
PROJECT_VOLUME_NAME="${PROJECT_DIR#/Volumes/}"
PROJECT_VOLUME_NAME="${PROJECT_VOLUME_NAME%%/*}"
if [[ "$PROJECT_DIR" != /Volumes/*/* ]]; then
  echo "FlowScalper 설치와 캐시는 외장 APFS 작업공간에서만 준비합니다." >&2
  exit 1
fi
CACHE_ROOT="${ROBOM_CACHE_ROOT:-/Volumes/$PROJECT_VOLUME_NAME/03_CACHES/ROBOM_FlowScalper}"
FRONTEND_MODULES="$CACHE_ROOT/frontend-node_modules"
cd "$PROJECT_DIR"

command -v uv >/dev/null || { echo "uv가 필요합니다: https://docs.astral.sh/uv/"; exit 1; }
command -v node >/dev/null || { echo "Node.js 22.13 이상 또는 24 이상이 필요합니다."; exit 1; }
command -v pnpm >/dev/null || { echo "pnpm이 필요합니다: https://pnpm.io/installation"; exit 1; }
node -e 'const [major, minor] = process.versions.node.split(".").map(Number); if (!((major === 22 && minor >= 13) || major >= 24)) process.exit(1)' || {
  echo "Node.js 22.13 이상 또는 24 이상이 필요합니다."
  exit 1
}

mkdir -p "$CACHE_ROOT/uv" "$CACHE_ROOT/xdg" "$CACHE_ROOT/tmp" "$CACHE_ROOT/pnpm-store" "$FRONTEND_MODULES"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"
export UV_CACHE_DIR="$CACHE_ROOT/uv"
export UV_PYTHON_INSTALL_DIR="$CACHE_ROOT/python"
export XDG_CACHE_HOME="$CACHE_ROOT/xdg"
export TMPDIR="$CACHE_ROOT/tmp/"
export NPM_CONFIG_CACHE="$CACHE_ROOT/npm"
export PLAYWRIGHT_BROWSERS_PATH="$CACHE_ROOT/playwright"
uv python install 3.12 --no-bin
if [[ ! -x .venv/bin/python ]] || [[ "$(.venv/bin/python -c 'import sys; print(sys.base_prefix)')" != "$UV_PYTHON_INSTALL_DIR"/* ]]; then
  uv venv --python 3.12 --clear .venv
fi
uv sync --python 3.12 --frozen --all-groups
if [[ -L frontend/node_modules ]]; then
  if [[ "$(readlink frontend/node_modules)" != "$FRONTEND_MODULES" ]]; then
    echo "frontend/node_modules가 다른 경로를 가리킵니다. 안전 이관 후 다시 실행하세요." >&2
    exit 1
  fi
elif [[ -e frontend/node_modules ]]; then
  echo "frontend/node_modules 실제 폴더를 외장 cache로 먼저 이관해야 합니다." >&2
  exit 1
else
  ln -s "$FRONTEND_MODULES" frontend/node_modules
fi
pnpm --dir frontend install --store-dir "$CACHE_ROOT/pnpm-store" --frozen-lockfile
pnpm --dir frontend build
uv run python scripts/migrate.py
echo "PASS: macOS 설치 완료"
echo "실행: ./scripts/run_macos.command"
