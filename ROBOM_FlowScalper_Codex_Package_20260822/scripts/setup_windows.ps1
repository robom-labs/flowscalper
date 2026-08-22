# Windows에서 고정 의존성과 정적 대시보드를 한 번에 준비한다.
$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectDir

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv가 필요합니다. https://docs.astral.sh/uv/ 에서 설치하세요."
}
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    throw "pnpm이 필요합니다. https://pnpm.io/installation 에서 설치하세요."
}

uv sync --frozen --all-groups
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
uv run python scripts/migrate.py
Write-Host "PASS: Windows 설치 완료"
Write-Host "실행: scripts\run_windows.bat"
