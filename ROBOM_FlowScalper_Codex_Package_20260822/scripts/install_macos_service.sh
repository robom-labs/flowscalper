#!/bin/zsh
# FlowScalper를 로그인·재부팅·프로세스 종료 뒤 자동 복구되는 LaunchAgent로 설치한다.
set -euo pipefail

PREPARE_ONLY="false"
MAINTENANCE_STOPPED="false"
while (( $# > 0 )); do
  case "$1" in
    --prepare-only)
      PREPARE_ONLY="true"
      ;;
    --maintenance-stopped)
      MAINTENANCE_STOPPED="true"
      ;;
    *)
      echo "사용법: $0 [--prepare-only] [--maintenance-stopped]" >&2
      exit 2
      ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"
LABEL="kr.robom.flowscalper"
USER_ID="$(id -u)"
LAUNCH_AGENT_DIR="$HOME/Library/LaunchAgents"
TARGET_PLIST="$LAUNCH_AGENT_DIR/$LABEL.plist"
TEMPLATE_PLIST="$SOURCE_PROJECT_DIR/packaging/macos/$LABEL.plist"
SERVICE_TARGET="gui/$USER_ID/$LABEL"
SOURCE_VOLUME_NAME="${SOURCE_PROJECT_DIR#/Volumes/}"
SOURCE_VOLUME_NAME="${SOURCE_VOLUME_NAME%%/*}"
WORKSPACE_MOUNT="${ROBOM_WORKSPACE_MOUNT:-/Volumes/$SOURCE_VOLUME_NAME}"
DEFAULT_EXTERNAL_HOME="/Volumes/One Touch/ROBOM_AUTOTRADING/FlowScalper_v0.2_20260822"
EXTERNAL_HOME="${ROBOM_EXTERNAL_HOME:-$DEFAULT_EXTERNAL_HOME}"
SPARSEBUNDLE_PATH="${ROBOM_WORKSPACE_SPARSEBUNDLE:-$EXTERNAL_HOME/ROBOM_FlowScalper_Workspace.sparsebundle}"
RUNTIME_ROOT="${ROBOM_RUNTIME_ROOT:-$WORKSPACE_MOUNT/05_RUNTIME/ROBOM_FlowScalper}"
SUPPORT_DIR="$RUNTIME_ROOT/support"
LOG_DIR="$RUNTIME_ROOT/logs"
CACHE_DIR="$RUNTIME_ROOT/cache"
ACTIVE_LEDGER_DIR="$RUNTIME_ROOT/active-ledger"
RUNTIME_VENV="$SUPPORT_DIR/runtime-venv"
PYTHON_BASE="$SUPPORT_DIR/python-base"
SERVICE_LOG="$LOG_DIR/service.log"
ERROR_LOG="$LOG_DIR/service-error.log"
MARKET_ARCHIVE_PATH="${ROBOM_MARKET_ARCHIVE_PATH:-$SOURCE_PROJECT_DIR/data/market-parquet-v6}"
CURRENT_POINTER="$RUNTIME_ROOT/current"
PREFLIGHT_DASHBOARD="$SUPPORT_DIR/latest-install-preflight.json"
PREFLIGHT_IDENTITY="$SUPPORT_DIR/latest-install-preflight-identity.json"
ROLLBACK_RESULT="$SUPPORT_DIR/latest-install-rollback.json"
TRUSTED_RUNNER_SCRIPT="$SUPPORT_DIR/run_macos_service.sh"
RELEASE_INTEGRITY_ANCHOR="$SUPPORT_DIR/current-release-integrity.json"
MAINTENANCE_STOPPED_EVIDENCE="$SUPPORT_DIR/latest-install-maintenance-stopped-evidence.json"
MAINTENANCE_POSTFLIGHT_FIRST="$SUPPORT_DIR/latest-install-maintenance-postflight-first.json"
MAINTENANCE_POSTFLIGHT_FINAL="$SUPPORT_DIR/latest-install-maintenance-postflight-final.json"
MAINTENANCE_ARTIFACT_BACKUP="$SUPPORT_DIR/.maintenance-stopped-artifacts.$$"
MAINTENANCE_TRANSITION_STARTED="false"

if [[ "$SOURCE_PROJECT_DIR" != "$WORKSPACE_MOUNT"/* ]]; then
  echo "자동 서비스 소스는 외장 APFS 작업공간 안에 있어야 합니다: $SOURCE_PROJECT_DIR" >&2
  exit 1
fi
if [[ "$RUNTIME_ROOT" != /Volumes/*/* || "$EXTERNAL_HOME" != /Volumes/*/* ]]; then
  echo "런타임과 sparsebundle은 외장 볼륨 경로여야 합니다." >&2
  exit 1
fi
if [[ ! -d "$SPARSEBUNDLE_PATH" ]]; then
  echo "외장 APFS sparsebundle이 없습니다: $SPARSEBUNDLE_PATH" >&2
  exit 1
fi
if [[ ! -f "$TEMPLATE_PLIST" || ! -x "$SOURCE_PROJECT_DIR/.venv/bin/python" || ! -d "$SOURCE_PROJECT_DIR/frontend/node_modules" ]]; then
  echo "먼저 외장 저장소에서 ./scripts/setup_macos.sh를 실행해야 합니다." >&2
  exit 1
fi

mkdir -p "$LAUNCH_AGENT_DIR" "$RUNTIME_ROOT"
if [[ -L "$RUNTIME_ROOT" || ! -d "$RUNTIME_ROOT" ]] || \
  [[ "$(cd "$RUNTIME_ROOT" && pwd -P)" != "$RUNTIME_ROOT" ]]; then
  echo "runtime root가 canonical regular 디렉터리가 아닙니다: $RUNTIME_ROOT" >&2
  exit 1
fi
for runtime_directory in "$SUPPORT_DIR" "$LOG_DIR" "$CACHE_DIR" "$ACTIVE_LEDGER_DIR"; do
  if [[ -L "$runtime_directory" || ( -e "$runtime_directory" && ! -d "$runtime_directory" ) ]]; then
    echo "runtime 하위 경로가 regular 디렉터리가 아닙니다: $runtime_directory" >&2
    exit 1
  fi
  mkdir -p "$runtime_directory"
  if [[ "$(cd "$runtime_directory" && pwd -P)" != "$runtime_directory" ]]; then
    echo "runtime 하위 경로가 canonical 디렉터리가 아닙니다: $runtime_directory" >&2
    exit 1
  fi
done
INSTALL_LOCK_DIR="$SUPPORT_DIR/.install-macos-service.lock"
if ! mkdir "$INSTALL_LOCK_DIR" 2>/dev/null; then
  echo "다른 FlowScalper 설치가 진행 중이거나 이전 설치 lock이 남아 있습니다: $INSTALL_LOCK_DIR" >&2
  exit 4
fi
release_install_lock() {
  rmdir "$INSTALL_LOCK_DIR" 2>/dev/null || true
}
UNVERIFIED_SERVICE_MAY_BE_RUNNING="false"
handle_install_exit() {
  local exit_code="$1"
  set +e
  trap '' EXIT HUP INT TERM
  if [[ "${UNVERIFIED_SERVICE_MAY_BE_RUNNING:-false}" == "true" ]]; then
    echo "설치 종료 시점에 readiness 미증명 서비스가 있어 fail-closed 종료합니다." >&2
    if ! fail_closed_unverified_service "INSTALL_EXIT_${exit_code}"; then
      echo "치명 상태: 설치 종료 중 readiness 미증명 서비스를 완전히 중지하지 못했습니다." >&2
      exit_code=70
    fi
  fi
  if [[ "${MAINTENANCE_TRANSITION_STARTED:-false}" == "true" ]]; then
    echo "유지보수 정지 설치가 중간 종료되어 구 릴리스 artifact를 정지 상태로 복구합니다." >&2
    if ! rollback_previous_release_stopped "INSTALL_EXIT_${exit_code}"; then
      echo "치명 상태: 구 릴리스 artifact를 정지 상태로 복구하지 못했습니다." >&2
      exit_code=71
    fi
  fi
  release_install_lock
  exit "$exit_code"
}
handle_install_signal() {
  local exit_code="$1"
  trap '' HUP INT TERM
  exit "$exit_code"
}
trap 'handle_install_exit $?' EXIT
trap 'handle_install_signal 129' HUP
trap 'handle_install_signal 130' INT
trap 'handle_install_signal 143' TERM

write_text_file_atomic() {
  local target="$1"
  local payload="$2"
  local temporary="$target.$$.tmp"
  if [[ -L "$target" || ( -e "$target" && ! -f "$target" ) || \
    -e "$temporary" || -L "$temporary" ]]; then
    echo "원자 증거 파일 경로가 안전한 regular file 계약이 아닙니다: $target" >&2
    return 1
  fi
  if ! (umask 077; printf '%s\n' "$payload" > "$temporary"); then
    if [[ -e "$temporary" || -L "$temporary" ]]; then
      /bin/unlink "$temporary" 2>/dev/null || true
    fi
    return 1
  fi
  if ! /bin/mv -f "$temporary" "$target"; then
    if [[ -e "$temporary" || -L "$temporary" ]]; then
      /bin/unlink "$temporary" 2>/dev/null || true
    fi
    return 1
  fi
  return 0
}
SOURCE_PYTHON="$SOURCE_PROJECT_DIR/.venv/bin/python"
SOURCE_PYTHON_BASE="$(PYTHONNOUSERSITE=1 "$SOURCE_PYTHON" -P -c 'import sys; print(sys.base_prefix)')"
PYTHON_BINARY="$(PYTHONNOUSERSITE=1 "$SOURCE_PYTHON" -P -c 'import sys; print(f"python{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ ! -x "$PYTHON_BASE/bin/$PYTHON_BINARY" ]]; then
  ditto "$SOURCE_PYTHON_BASE" "$PYTHON_BASE"
fi
if [[ ! -x "$RUNTIME_VENV/bin/python" ]]; then
  ditto "$SOURCE_PROJECT_DIR/.venv" "$RUNTIME_VENV"
fi
if [[ -L "$RUNTIME_VENV/bin/python" ]]; then
  unlink "$RUNTIME_VENV/bin/python"
  ln -s "$PYTHON_BASE/bin/$PYTHON_BINARY" "$RUNTIME_VENV/bin/python"
fi
PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 "$SOURCE_PYTHON" -P -c \
  'import pathlib,sys; path=pathlib.Path(sys.argv[1]); rows=path.read_text().splitlines(); path.write_text("\n".join((f"home = {sys.argv[2]}" if row.startswith("home = ") else row) for row in rows) + "\n")' \
  "$RUNTIME_VENV/pyvenv.cfg" "$PYTHON_BASE/bin"
PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  "$RUNTIME_VENV/bin/python" -P -c "import duckdb, fastapi, uvicorn"

write_release_integrity_anchor() {
  local release_path="$1"
  local verifier_release_path="$2"
  if [[ -L "$TRUSTED_RUNNER_SCRIPT" || ! -f "$TRUSTED_RUNNER_SCRIPT" ]]; then
    echo "외부 신뢰 verifier가 regular file이 아닙니다: $TRUSTED_RUNNER_SCRIPT" >&2
    return 1
  fi
  PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYTHONPATH="$verifier_release_path" \
    "$RUNTIME_VENV/bin/python" -P - \
    "$release_path" "$verifier_release_path" "$RELEASE_INTEGRITY_ANCHOR" \
    "$TRUSTED_RUNNER_SCRIPT" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from scripts.stage_macos_release import _verify_release_tree


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


release_path = Path(sys.argv[1]).resolve(strict=True)
verifier_release_path = Path(sys.argv[2]).resolve(strict=True)
anchor_path = Path(sys.argv[3])
trusted_runner = Path(sys.argv[4]).resolve(strict=True)
manifest = _verify_release_tree(release_path)
verifier_manifest = _verify_release_tree(verifier_release_path)
manifest_path = release_path / "release-manifest.json"
verifier_manifest_path = verifier_release_path / "release-manifest.json"
source_runner = (
    verifier_release_path / "scripts" / "run_macos_service.sh"
).resolve(strict=True)
source_runner_sha = sha256(source_runner)
if sha256(trusted_runner) != source_runner_sha:
    raise RuntimeError("외부 verifier가 검증 출처 릴리스 runner와 다릅니다.")
anchor = {
    "schema_version": 2,
    "release_path": str(release_path),
    "release_commit": manifest["commit"],
    "manifest_sha256": sha256(manifest_path),
    "launcher_path": str(trusted_runner),
    "launcher_sha256": source_runner_sha,
    "launcher_source_release_path": str(verifier_release_path),
    "launcher_source_commit": verifier_manifest["commit"],
    "launcher_source_manifest_sha256": sha256(verifier_manifest_path),
    "paper_only": True,
    "real_orders_enabled": False,
}
anchor_path.parent.mkdir(parents=True, exist_ok=True)
temporary = anchor_path.with_name(f".{anchor_path.name}.{uuid4().hex}.tmp")
try:
    temporary.write_text(
        json.dumps(anchor, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    os.replace(temporary, anchor_path)
finally:
    temporary.unlink(missing_ok=True)
PY
}

install_trusted_runner_from_release() {
  local release_path="$1"
  local release_runner="$release_path/scripts/run_macos_service.sh"
  local trusted_runner_temp="$SUPPORT_DIR/.run_macos_service.$$.tmp"
  if [[ -L "$release_runner" || ! -f "$release_runner" ]]; then
    echo "검증된 불변 릴리스 runner가 regular file이 아닙니다: $release_runner" >&2
    return 1
  fi
  if ! ditto "$release_runner" "$trusted_runner_temp" || \
    ! cmp -s "$release_runner" "$trusted_runner_temp" || \
    ! chmod 700 "$trusted_runner_temp" || \
    ! mv -f "$trusted_runner_temp" "$TRUSTED_RUNNER_SCRIPT"; then
    rm -f "$trusted_runner_temp"
    echo "불변 릴리스에서 외부 신뢰 verifier를 원자적으로 준비하지 못했습니다." >&2
    return 1
  fi
}

launchctl_command() {
  /bin/launchctl "$@"
}

verify_stopped_process_absence_exact() {
  local phase="$1"
  local launchctl_output=""
  local launchctl_status=0
  local expected="Bad request.
Could not find service \"$LABEL\" in domain for user gui: $USER_ID"
  local lsof_probe_output="$SUPPORT_DIR/latest-install-${phase}-lsof-probe.txt"
  local listener_output="$SUPPORT_DIR/latest-install-${phase}-listener.txt"
  local listener_error="$SUPPORT_DIR/latest-install-${phase}-listener-error.txt"
  if launchctl_output="$(launchctl_command print "$SERVICE_TARGET" 2>&1)"; then
    launchctl_status=0
  else
    launchctl_status=$?
  fi
  if (( launchctl_status != 113 )) || [[ "$launchctl_output" != "$expected" ]]; then
    echo "LaunchAgent $phase 부재 응답이 exact 계약과 다릅니다." >&2
    return 1
  fi
  if ! /usr/sbin/lsof -nP -p "$$" > "$lsof_probe_output" 2>&1 || \
    [[ ! -s "$lsof_probe_output" ]]; then
    echo "lsof 자체 진단이 실패해 $phase 프로세스 부재를 확정할 수 없습니다." >&2
    return 1
  fi
  local listener_status=0
  if /usr/sbin/lsof -nP -F0pfnDi -iTCP:8870 -sTCP:LISTEN \
    > "$listener_output" 2> "$listener_error"; then
    listener_status=0
  else
    listener_status=$?
  fi
  if (( listener_status != 1 )) || [[ -s "$listener_output" || -s "$listener_error" ]]; then
    echo "TCP 8870 listener가 남았거나 lsof 진단이 실패했습니다: $phase" >&2
    return 1
  fi
  local ledger_path="$ACTIVE_LEDGER_DIR/run-ledger.sqlite3"
  if [[ -L "$ledger_path" || ! -f "$ledger_path" ]]; then
    echo "active ledger가 regular file이 아닙니다: $ledger_path" >&2
    return 1
  fi
  local ledger_candidate=""
  for ledger_candidate in "$ledger_path" "$ledger_path-wal" "$ledger_path-shm"; do
    if [[ ! -e "$ledger_candidate" && ! -L "$ledger_candidate" ]]; then
      continue
    fi
    if [[ -L "$ledger_candidate" || ! -f "$ledger_candidate" ]]; then
      echo "active ledger 계열 경로가 regular file이 아닙니다: $ledger_candidate" >&2
      return 1
    fi
    local ledger_output="$SUPPORT_DIR/latest-install-${phase}-${ledger_candidate:t}-owner.txt"
    local ledger_error="$SUPPORT_DIR/latest-install-${phase}-${ledger_candidate:t}-owner-error.txt"
    local ledger_status=0
    if /usr/sbin/lsof -nP -F0pfnDi "$ledger_candidate" \
      > "$ledger_output" 2> "$ledger_error"; then
      ledger_status=0
    else
      ledger_status=$?
    fi
    if (( ledger_status != 1 )) || [[ -s "$ledger_output" || -s "$ledger_error" ]]; then
      echo "active ledger 보유 프로세스가 남았거나 lsof 진단이 실패했습니다: $ledger_candidate" >&2
      return 1
    fi
  done
  if launchctl_output="$(launchctl_command print "$SERVICE_TARGET" 2>&1)"; then
    launchctl_status=0
  else
    launchctl_status=$?
  fi
  if (( launchctl_status != 113 )) || [[ "$launchctl_output" != "$expected" ]]; then
    echo "LaunchAgent $phase 최종 부재 응답이 exact 계약과 다릅니다." >&2
    return 1
  fi
}

verify_maintenance_stopped_evidence() {
  local phase="$1"
  if ! verify_stopped_process_absence_exact "${phase}-before"; then
    return 1
  fi
  if [[ ! -f "$PREFLIGHT_DASHBOARD" || -L "$PREFLIGHT_DASHBOARD" || \
    ! -f "$PREFLIGHT_IDENTITY" || -L "$PREFLIGHT_IDENTITY" ]]; then
    echo "유지보수 정지 전 dashboard·identity 증거가 regular file로 남아 있지 않습니다." >&2
    return 1
  fi
  if ! PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
    PYTHONPATH="$PREVIOUS_RELEASE" "$RUNTIME_VENV/bin/python" -P - \
    "$RUNTIME_ROOT" "$PREVIOUS_RELEASE" "$PREFLIGHT_DASHBOARD" \
    "$PREFLIGHT_IDENTITY" "$RELEASE_INTEGRITY_ANCHOR" \
    "$RUNTIME_ROOT/current-deployment.json" "$MAINTENANCE_STOPPED_EVIDENCE" <<'PY'
import hashlib
import json
import math
import os
import sqlite3
import stat
import sys
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from scripts.stage_macos_release import _verify_release_tree


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def read_object(path: Path, label: str) -> dict[str, object]:
    require(not path.is_symlink() and path.is_file(), f"{label}가 regular file이 아닙니다.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(payload, dict), f"{label}가 JSON object가 아닙니다.")
    return payload


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def non_negative_int(value: object, label: str) -> int:
    require(type(value) is int and value >= 0, f"{label}가 0 이상 int가 아닙니다: {value!r}")
    return int(value)


def finite_number(value: object, label: str) -> float:
    require(
        type(value) in (int, float) and math.isfinite(value),
        f"{label}가 유한한 숫자가 아닙니다: {value!r}",
    )
    return float(value)


runtime_root, previous_release, dashboard_path, identity_path, anchor_path, deployment_path, output_path = map(
    Path, sys.argv[1:8]
)
require(not runtime_root.is_symlink(), "runtime root symlink는 허용하지 않습니다.")
runtime_root = runtime_root.resolve(strict=True)
previous_release = previous_release.resolve(strict=True)
require(previous_release.parent == runtime_root / "releases", "기존 릴리스가 runtime releases direct child가 아닙니다.")
current_pointer = runtime_root / "current"
require(current_pointer.is_symlink(), "current 포인터가 symlink가 아닙니다.")
require(current_pointer.resolve(strict=True) == previous_release, "current 포인터가 예상 구 릴리스와 다릅니다.")
manifest = _verify_release_tree(previous_release)
require(manifest.get("schema_version") == 2, "유지보수 정지 설치는 검증된 schema v2 릴리스만 허용합니다.")
commit = manifest.get("commit")
require(commit == previous_release.name, "구 릴리스 commit·directory가 다릅니다.")
for field, expected in (
    ("paper_only", True),
    ("real_orders_enabled", False),
    ("auth_required", False),
    ("private_api_enabled", False),
    ("wallet_paths_enabled", False),
):
    require(manifest.get(field) is expected, f"구 릴리스 manifest {field}가 안전값이 아닙니다.")

anchor = read_object(anchor_path, "current release anchor")
require(anchor.get("schema_version") == 2, "current release anchor schema가 2가 아닙니다.")
require(anchor.get("release_path") == str(previous_release), "anchor release_path가 current와 다릅니다.")
require(anchor.get("release_commit") == commit, "anchor release_commit이 current와 다릅니다.")
require(anchor.get("manifest_sha256") == sha256(previous_release / "release-manifest.json"), "anchor manifest checksum이 다릅니다.")
trusted_runner = runtime_root / "support" / "run_macos_service.sh"
source_runner = previous_release / "scripts" / "run_macos_service.sh"
require(not trusted_runner.is_symlink() and trusted_runner.is_file(), "trusted runner가 regular file이 아닙니다.")
require(not source_runner.is_symlink() and source_runner.is_file(), "구 릴리스 runner가 regular file이 아닙니다.")
require(anchor.get("launcher_path") == str(trusted_runner), "anchor launcher_path가 trusted runner와 다릅니다.")
require(anchor.get("launcher_source_release_path") == str(previous_release), "anchor launcher source release가 current와 다릅니다.")
require(anchor.get("launcher_source_commit") == commit, "anchor launcher source commit이 current와 다릅니다.")
require(anchor.get("launcher_sha256") == sha256(trusted_runner) == sha256(source_runner), "anchor·trusted·source runner checksum이 다릅니다.")
require(anchor.get("launcher_source_manifest_sha256") == sha256(previous_release / "release-manifest.json"), "anchor launcher source manifest checksum이 다릅니다.")
require(anchor.get("paper_only") is True, "anchor PAPER only가 True가 아닙니다.")
require(anchor.get("real_orders_enabled") is False, "anchor real order가 비활성이 아닙니다.")
deployment = read_object(deployment_path, "current deployment")
require(deployment.get("new_state") == str(previous_release), "deployment new_state가 current와 다릅니다.")
require(deployment.get("release_commit") == commit, "deployment release_commit이 current와 다릅니다.")
require(deployment.get("paper_only") is True, "deployment PAPER only가 True가 아닙니다.")
require(deployment.get("real_orders_enabled") is False, "deployment real order가 비활성이 아닙니다.")

dashboard = read_object(dashboard_path, "maintenance dashboard")
identity = read_object(identity_path, "maintenance identity")
status = dashboard.get("status")
system = dashboard.get("system")
risk = dashboard.get("risk")
operation = dashboard.get("operation_status")
intent = dashboard.get("paper_entry_intent")
for value, label in (
    (status, "dashboard status"),
    (system, "dashboard system"),
    (risk, "dashboard risk"),
    (operation, "dashboard operation_status"),
    (intent, "dashboard paper_entry_intent"),
):
    require(isinstance(value, dict), f"{label}가 object가 아닙니다.")
run_id = status.get("run_id")
revision = non_negative_int(intent.get("revision"), "dashboard pause revision")
require(isinstance(run_id, str) and bool(run_id) and "\t" not in run_id, "dashboard run_id가 올바르지 않습니다.")
require(identity.get("run_id") == run_id, "identity run_id가 dashboard와 다릅니다.")
require(identity.get("pause_state") == "USER_PAUSED", "identity pause_state가 USER_PAUSED가 아닙니다.")
require(identity.get("pause_revision") == revision, "identity pause revision이 dashboard와 다릅니다.")
require(identity.get("release_commit") == commit, "identity release commit이 current와 다릅니다.")
require(status.get("market_data_state") == "LIVE", "정지 전 market data가 LIVE가 아닙니다.")
require(status.get("execution_state") == "PAPER", "정지 전 execution이 PAPER가 아닙니다.")
require(status.get("real_orders_enabled") is False, "정지 전 real order가 비활성이 아닙니다.")
require(status.get("auth_required") is False, "정지 전 auth가 비활성이 아닙니다.")
require(system.get("release_commit") == commit, "dashboard release commit이 current와 다릅니다.")
require(system.get("release_isolated") is True, "dashboard release_isolated가 True가 아닙니다.")
require(risk.get("paper_only") is True, "dashboard PAPER only가 True가 아닙니다.")
require(dashboard.get("paused") is True, "정지 전 PAPER entry가 수동 일시정지가 아닙니다.")
require(operation.get("state") == "MANUALLY_PAUSED", "정지 전 operation이 MANUALLY_PAUSED가 아닙니다.")
require(operation.get("market_observation_active") is True, "정지 전 시장 관찰이 활성이 아닙니다.")
require(operation.get("paper_entry_active") is False, "정지 전 PAPER entry가 비활성이 아닙니다.")
require(operation.get("automatic_recovery") is False, "수동 일시정지에서 automatic recovery가 활성입니다.")
require(intent.get("state") == "USER_PAUSED", "정지 전 intent가 USER_PAUSED가 아닙니다.")
require(intent.get("manual_pause_requested") is True, "정지 전 manual pause 의도가 True가 아닙니다.")
require(dashboard.get("position") is None, "정지 전 main position이 flat이 아닙니다.")
require(dashboard.get("focus_positions") == [], "정지 전 focus position이 flat이 아닙니다.")
require(dashboard.get("league_positions") == [], "정지 전 league position이 flat이 아닙니다.")
for field in (
    "main_pending_entry_count",
    "league_pending_entry_count",
    "total_pending_entry_count",
    "total_open_position_count",
):
    require(non_negative_int(dashboard.get(field), f"dashboard {field}") == 0, f"dashboard {field}가 0이 아닙니다.")
require(dashboard.get("paper_portfolio_flat") is True, "dashboard paper portfolio가 flat이 아닙니다.")
for field, maximum in (("lag_p95_ms", 500.0), ("trade_lag_p95_ms", 1000.0)):
    require(0 <= finite_number(system.get(field), field) <= maximum, f"dashboard {field}가 임계를 넘었습니다.")
queue_depth = non_negative_int(system.get("queue_depth"), "dashboard queue_depth")
require(queue_depth <= 64, "정지 전 queue depth가 64를 넘었습니다.")
require(system.get("queue_overload_active") is False, "정지 전 queue overload가 활성입니다.")
require(system.get("critical_lag_active") is False, "정지 전 critical lag가 활성입니다.")
require(system.get("persistence_fault_active") is False, "정지 전 persistence fault가 활성입니다.")
require(system.get("persistence_last_error") == "NONE", "정지 전 persistence error가 남아 있습니다.")
baseline_fields = (
    "queue_overload_drop_count",
    "critical_lag_incident_count",
    "persistence_buffer_dropped",
    "persistence_fault_count",
)
baseline = {field: non_negative_int(system.get(field), f"dashboard {field}") for field in baseline_fields}
baseline["queue_depth"] = queue_depth

ledger_path = runtime_root / "active-ledger" / "run-ledger.sqlite3"
require(not ledger_path.is_symlink() and ledger_path.is_file(), "active ledger가 regular file이 아닙니다.")
for suffix in ("-wal", "-shm"):
    sidecar = Path(f"{ledger_path}{suffix}")
    require(not sidecar.exists() and not sidecar.is_symlink(), f"정지된 active ledger에 {suffix} sidecar가 남아 있습니다.")
metadata = ledger_path.stat()
require(stat.S_ISREG(metadata.st_mode), "active ledger가 regular file이 아닙니다.")
uri = f"file:{quote(str(ledger_path), safe='/')}?mode=ro&immutable=1&cache=private"
connection = sqlite3.connect(uri, uri=True, timeout=1.0, isolation_level=None)
try:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    require(connection.execute("PRAGMA query_only").fetchone()[0] == 1, "SQLite query_only가 활성이 아닙니다.")
    checks: dict[str, str] = {}
    for table in ("runs", "positions", "paper_orders", "trades", "transitions"):
        schema = connection.execute(
            "SELECT type FROM sqlite_schema WHERE name = ? AND tbl_name = ?",
            (table, table),
        ).fetchall()
        require([row[0] for row in schema] == ["table"], f"critical table {table}가 exact table이 아닙니다.")
        rows = connection.execute(f"PRAGMA quick_check('{table}')").fetchall()
        require([row[0] for row in rows] == ["ok"], f"critical table {table} quick_check가 ok가 아닙니다.")
        checks[table] = "ok"
    open_runs = connection.execute(
        "SELECT run_id, mode, venue FROM runs WHERE finalized_ts_ms IS NULL ORDER BY started_ts_ms DESC LIMIT 2"
    ).fetchall()
    require(len(open_runs) == 1, "active ledger open Run이 정확히 하나가 아닙니다.")
    require(open_runs[0]["run_id"] == run_id, "active ledger Run이 dashboard와 다릅니다.")
    require(open_runs[0]["mode"] == "LIVE_SHADOW_PAPER", "active ledger Run mode가 LIVE_SHADOW_PAPER가 아닙니다.")
    setting_rows = connection.execute(
        "SELECT value_json, updated_ts_ms FROM app_settings WHERE setting_key = 'paper_entry_user_intent' LIMIT 2"
    ).fetchall()
    require(len(setting_rows) == 1, "active ledger pause setting이 정확히 하나가 아닙니다.")
    setting = json.loads(setting_rows[0]["value_json"])
    require(isinstance(setting, dict), "active ledger pause setting이 object가 아닙니다.")
    require(setting.get("run_id") == run_id, "active ledger pause Run이 dashboard와 다릅니다.")
    require(setting.get("manual_pause_requested") is True, "active ledger manual pause가 True가 아닙니다.")
    require(setting.get("revision") == revision, "active ledger pause revision이 dashboard와 다릅니다.")
    dashboard_intent_ts = non_negative_int(intent.get("updated_ts_ms"), "dashboard intent updated_ts_ms")
    require(
        non_negative_int(setting_rows[0]["updated_ts_ms"], "active ledger pause updated_ts_ms") >= dashboard_intent_ts,
        "active ledger pause setting이 dashboard 증거보다 오래됐습니다.",
    )
    snapshot = connection.execute(
        "SELECT lifecycle_state, payload_json FROM snapshots WHERE run_id = ? ORDER BY snapshot_id DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    require(snapshot is not None and snapshot["lifecycle_state"] == "SCANNING", "active ledger 최신 snapshot이 SCANNING이 아닙니다.")
    snapshot_payload = json.loads(snapshot["payload_json"])
    require(snapshot_payload.get("open_position") is None, "active ledger snapshot main position이 flat이 아닙니다.")
    portfolio = snapshot_payload.get("portfolio")
    require(isinstance(portfolio, dict) and portfolio.get("run_id") == run_id, "active ledger snapshot portfolio Run이 다릅니다.")
    accounts = portfolio.get("accounts")
    require(isinstance(accounts, list) and bool(accounts), "active ledger snapshot account가 비어 있습니다.")
    for account in accounts:
        require(isinstance(account, dict), "active ledger snapshot account가 object가 아닙니다.")
        require(account.get("pending_entries") == {}, "active ledger snapshot에 pending entry가 남아 있습니다.")
        require(account.get("positions") == {}, "active ledger snapshot에 open position이 남아 있습니다.")
    open_position_rows = connection.execute(
        "SELECT COUNT(*) FROM positions WHERE run_id = ? AND lifecycle_state != 'CLOSED'",
        (run_id,),
    ).fetchone()[0]
    pending_order_rows = connection.execute(
        "SELECT COUNT(*) FROM paper_orders WHERE run_id = ? AND status NOT IN ('FILLED', 'REJECTED', 'FINALIZED')",
        (run_id,),
    ).fetchone()[0]
    require(open_position_rows == 0, "active ledger positions에 open row가 남아 있습니다.")
    require(pending_order_rows == 0, "active ledger paper_orders에 pending row가 남아 있습니다.")
    user_version = connection.execute("PRAGMA user_version").fetchone()[0]
finally:
    connection.close()

evidence = {
    "schema_version": 1,
    "status": "PASS",
    "mode": "MAINTENANCE_STOPPED",
    "expected_current_release": str(previous_release),
    "expected_current_commit": commit,
    "expected_run_id": run_id,
    "expected_pause_state": "USER_PAUSED",
    "expected_pause_revision": revision,
    "dashboard_intent_updated_ts_ms": dashboard_intent_ts,
    "baseline": baseline,
    "ledger": {
        "path": str(ledger_path),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "size": metadata.st_size,
        "mtime_ns": metadata.st_mtime_ns,
        "user_version": user_version,
        "open_run_count": 1,
        "open_position_count": 0,
        "pending_order_count": 0,
        "critical_quick_check": checks,
        "wal_absent": True,
        "shm_absent": True,
    },
    "paper_only": True,
    "real_orders_enabled": False,
    "auth_required": False,
}
output_path.parent.mkdir(parents=True, exist_ok=True)
require(not output_path.is_symlink(), "maintenance stopped 증거 경로 symlink는 허용하지 않습니다.")
temporary = output_path.with_name(f".{output_path.name}.{uuid4().hex}.tmp")
try:
    temporary.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, output_path)
finally:
    temporary.unlink(missing_ok=True)
PY
  then
    echo "유지보수 정지 dashboard·current commit·Run·pause revision·offline ledger 결합 검증이 실패했습니다." >&2
    return 1
  fi
  if ! verify_stopped_process_absence_exact "${phase}-after"; then
    return 1
  fi
}

HAD_CURRENT="false"
HAD_JOB="false"
PREVIOUS_RELEASE=""
PREVIOUS_RELEASE_COMMIT=""
EXPECTED_RUN_ID=""
EXPECTED_PAUSE_STATE=""
EXPECTED_PAUSE_REVISION=""
LEGACY_RUNTIME_SAFETY_FIELDS_MISSING="false"
LEGACY_RUNTIME_COMPATIBILITY="false"
EXPECTED_LEGACY_SERVICE_PID=""
EXPECTED_LEGACY_SNAPSHOT_ID=""
EXPECTED_LEGACY_RECOVERY_AUDIT_ID=""
VERIFIER_RELEASE=""
if [[ -L "$CURRENT_POINTER" ]]; then
  HAD_CURRENT="true"
  PREVIOUS_RELEASE="$(cd "$CURRENT_POINTER" && pwd -P)"
fi
if launchctl print "$SERVICE_TARGET" >/dev/null 2>&1; then
  HAD_JOB="true"
fi
FIRST_INSTALL="false"
FIRST_INSTALL_LABEL_WAS_DISABLED="false"
if [[ "$MAINTENANCE_STOPPED" == "true" ]]; then
  if [[ "$HAD_CURRENT" != "true" || "$HAD_JOB" != "false" ]]; then
    echo "--maintenance-stopped는 current가 있고 LaunchAgent가 완전히 중지된 상태에서만 허용합니다." >&2
    exit 4
  fi
  if ! maintenance_disabled_services="$(launchctl_command print-disabled "gui/$USER_ID")" || \
    printf '%s\n' "$maintenance_disabled_services" | \
      /usr/bin/grep -Fq "\"$LABEL\" => disabled"; then
    echo "--maintenance-stopped는 기존 자동 시작 label이 enabled인 유지보수 정지 상태만 허용합니다." >&2
    exit 4
  fi
  if ! verify_maintenance_stopped_evidence "initial-maintenance"; then
    echo "유지보수 정지 최초 증거 검증이 실패해 릴리스를 준비하지 않습니다." >&2
    exit 4
  fi
  if ! IFS=$'\t' read -r EXPECTED_RUN_ID EXPECTED_PAUSE_STATE \
    EXPECTED_PAUSE_REVISION PREVIOUS_RELEASE_COMMIT <<< \
    "$(PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
      "$RUNTIME_VENV/bin/python" -P -c \
      'import json,sys; p=json.load(open(sys.argv[1], encoding="utf-8")); print(p["expected_run_id"], p["expected_pause_state"], p["expected_pause_revision"], p["expected_current_commit"], sep="\t")' \
      "$MAINTENANCE_STOPPED_EVIDENCE")"; then
    echo "유지보수 정지 예상 Run·pause·commit을 읽지 못했습니다." >&2
    exit 4
  fi
elif [[ "$HAD_CURRENT" == "false" && "$HAD_JOB" == "false" ]]; then
  FIRST_INSTALL="true"
  if ! disabled_services="$(/bin/launchctl print-disabled "gui/$USER_ID")"; then
    echo "최초 설치 전 LaunchAgent disabled 상태를 확인하지 못했습니다." >&2
    exit 4
  fi
  if printf '%s\n' "$disabled_services" | \
    /usr/bin/grep -Fq "\"$LABEL\" => disabled"; then
    FIRST_INSTALL_LABEL_WAS_DISABLED="true"
  fi
  for stale_artifact in \
    "$TARGET_PLIST" \
    "$TRUSTED_RUNNER_SCRIPT" \
    "$RELEASE_INTEGRITY_ANCHOR" \
    "$RUNTIME_ROOT/current-deployment.json"; do
    if [[ -e "$stale_artifact" || -L "$stale_artifact" ]]; then
      echo "최초 설치에 속하지 않는 잔류 artifact가 있습니다: $stale_artifact" >&2
      exit 4
    fi
  done
else
  if [[ "$HAD_CURRENT" != "true" || "$HAD_JOB" != "true" ]]; then
    echo "current 포인터와 LaunchAgent가 모두 있지 않아 최초 설치로 간주할 수 없습니다." >&2
    exit 4
  fi
  if ! curl -fsS --max-time 3 http://127.0.0.1:8870/api/dashboard > "$PREFLIGHT_DASHBOARD"; then
    echo "기존 PAPER 서비스의 dashboard preflight를 저장하지 못했습니다." >&2
    exit 4
  fi
  if ! PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
    PYTHONPATH="$SOURCE_PROJECT_DIR" "$RUNTIME_VENV/bin/python" -P - \
    "$PREFLIGHT_DASHBOARD" "$PREFLIGHT_IDENTITY" "$RUNTIME_ROOT" \
    "$PREVIOUS_RELEASE" <<'PY'
import json
import math
import sys
from pathlib import Path

from scripts.stage_macos_release import (
    _read_release_manifest,
    _verify_release_tree,
    legacy_runtime_safety_fields_missing,
)
from scripts.verify_compatibility_runtime_preflight import verify_legacy_runtime_preflight

dashboard_path, identity_path, runtime_root, previous_release = map(Path, sys.argv[1:5])
payload = json.loads(dashboard_path.read_text(encoding="utf-8"))
status = payload["status"]
system = payload["system"]
risk = payload["risk"]
operation = payload["operation_status"]
intent = payload["paper_entry_intent"]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def require_non_negative_count(field: str) -> int:
    value = payload.get(field)
    require(
        type(value) is int and value >= 0,
        f"dashboard {field}가 0 이상의 int가 아닙니다: {value!r}",
    )
    return value


def require_flat_paper_portfolio() -> None:
    main_pending = require_non_negative_count("main_pending_entry_count")
    league_pending = require_non_negative_count("league_pending_entry_count")
    total_pending = require_non_negative_count("total_pending_entry_count")
    total_open = require_non_negative_count("total_open_position_count")
    require(
        total_pending == main_pending + league_pending,
        "dashboard total pending이 main+league와 다릅니다.",
    )
    require(main_pending == 0, "main pending entry가 남아 있습니다.")
    require(league_pending == 0, "league pending entry가 남아 있습니다.")
    require(total_pending == 0, "total pending entry가 남아 있습니다.")
    require(total_open == 0, "open PAPER position이 남아 있습니다.")
    require(
        payload.get("paper_portfolio_flat") is True,
        "paper_portfolio_flat이 명시적 True가 아닙니다.",
    )
    require(payload.get("position") is None, "main position이 flat이 아닙니다.")
    require(payload.get("focus_positions") == [], "focus position이 flat이 아닙니다.")
    require(payload.get("league_positions") == [], "league position이 flat이 아닙니다.")


def require_runtime_health() -> None:
    for field, maximum in (
        ("lag_p95_ms", 500.0),
        ("trade_lag_p95_ms", 1000.0),
        ("persistence_flush_last_ms", 20000.0),
    ):
        value = system.get(field)
        require(
            type(value) in (int, float)
            and math.isfinite(value)
            and 0 <= value <= maximum,
            f"dashboard {field}가 유한한 0..{maximum} 숫자가 아닙니다: {value!r}",
        )
    flush_count = system.get("persistence_flush_count")
    require(
        type(flush_count) is int and flush_count >= 4,
        f"persistence_flush_count가 4 이상의 int가 아닙니다: {flush_count!r}",
    )
    fault_count = system.get("persistence_fault_count")
    recovery_count = system.get("persistence_recovery_count")
    dropped_count = system.get("persistence_buffer_dropped")
    require(
        type(fault_count) is int and fault_count >= 0,
        f"persistence_fault_count가 0 이상의 int가 아닙니다: {fault_count!r}",
    )
    require(
        type(recovery_count) is int and recovery_count == fault_count,
        "persistence_recovery_count가 fault 누적과 다릅니다: "
        f"fault={fault_count!r}, recovery={recovery_count!r}",
    )
    require(
        type(dropped_count) is int and dropped_count >= 0,
        f"persistence_buffer_dropped가 0 이상의 int가 아닙니다: {dropped_count!r}",
    )
    require(system.get("persistence_fault_active") is False, "persistence fault가 활성 상태입니다.")
    require(
        system.get("persistence_fault_recoverable") is False,
        "persistence recovery가 아직 진행 중입니다.",
    )
    require(system.get("persistence_last_error") == "NONE", "persistence 현재 오류가 남아 있습니다.")
    if fault_count > 0:
        require(recovery_count > 0, "persistence fault 복구 이력이 없습니다.")
        recovered_ts = system.get("persistence_last_recovered_ts_ms")
        completed_ts = system.get("persistence_flush_last_completed_ts_ms")
        require(
            type(recovered_ts) is int and recovered_ts > 0,
            f"마지막 persistence 복구 시각이 올바르지 않습니다: {recovered_ts!r}",
        )
        require(
            type(completed_ts) is int and completed_ts >= recovered_ts,
            "마지막 persistence 복구 뒤 성공 flush가 증명되지 않았습니다: "
            f"recovered={recovered_ts!r}, flushed={completed_ts!r}",
        )
    require(system.get("persistence_worker_warmed") is True, "persistence worker가 warmed 상태가 아닙니다.")
    require(system.get("storage_entry_allowed") is True, "storage entry가 허용 상태가 아닙니다.")


original_manifest = _read_release_manifest(previous_release)
original_schema = original_manifest.get("schema_version")
legacy_compatibility = original_schema == 1 or (
    original_schema == 2
    and original_manifest.get("legacy_schema_version") == 1
    and original_manifest.get("legacy_source_commit_verified") is True
    and original_manifest.get("legacy_frontend_manifest_verified") is True
)
legacy_missing_fields = legacy_runtime_safety_fields_missing(original_manifest, system)
require(system["release_commit"] == original_manifest.get("commit"), "release commit이 다릅니다.")
require(system["release_isolated"] is True, "release_isolated가 True가 아닙니다.")
require(risk["paper_only"] is True, "PAPER only가 명시적 True가 아닙니다.")
require(status["market_data_state"] == "LIVE", "market data가 LIVE가 아닙니다.")
require(status["execution_state"] == "PAPER", "execution state가 PAPER가 아닙니다.")
require(status["real_orders_enabled"] is False, "real order가 비활성이 아닙니다.")
require(status["auth_required"] is False, "auth가 비활성이 아닙니다.")
require(payload["paused"] is True, "PAPER entry가 일시정지되지 않았습니다.")
require(operation["state"] == "MANUALLY_PAUSED", "수동 일시정지 상태가 아닙니다.")
require(operation["market_observation_active"] is True, "시장 관찰이 활성이 아닙니다.")
require(operation["paper_entry_active"] is False, "PAPER entry가 비활성이 아닙니다.")
require(operation["automatic_recovery"] is False, "수동 일시정지에서 자동 복구가 활성입니다.")
require(intent["state"] == "USER_PAUSED", "entry intent가 USER_PAUSED가 아닙니다.")
require(intent["manual_pause_requested"] is True, "manual pause 의도가 True가 아닙니다.")
require_runtime_health()
run_id = status.get("run_id")
revision = intent.get("revision")
require(isinstance(run_id, str) and bool(run_id) and "\t" not in run_id, "run_id가 올바르지 않습니다.")
require(type(revision) is int and revision >= 0, "pause revision이 0 이상의 int가 아닙니다.")
legacy_evidence = None
if legacy_compatibility:
    legacy_evidence = verify_legacy_runtime_preflight(
        payload,
        ledger_path=runtime_root / "active-ledger" / "run-ledger.sqlite3",
        runtime_root=runtime_root,
        manifest=original_manifest,
        expected_run_id=run_id,
        expected_pause_revision=revision,
    )
else:
    require(
        system.get("funding_readiness") == "NOT_READY",
        "funding readiness가 NOT_READY가 아닙니다.",
    )
    require_flat_paper_portfolio()
if legacy_compatibility:
    manifest = original_manifest
else:
    manifest = _verify_release_tree(previous_release)
    require(manifest["schema_version"] == 2, "rollback manifest가 schema v2가 아닙니다.")
require(system["release_commit"] == manifest["commit"], "dashboard와 rollback commit이 다릅니다.")
require(system["release_isolated"] is True, "rollback release가 isolated 상태가 아닙니다.")
identity_path.write_text(
    json.dumps(
        {
            "run_id": run_id,
            "pause_state": intent["state"],
            "pause_revision": revision,
            "release_commit": manifest["commit"],
            "legacy_runtime_safety_fields_missing": bool(legacy_missing_fields),
            "legacy_runtime_safety_missing_fields": list(legacy_missing_fields),
            "legacy_runtime_compatibility": legacy_compatibility,
            "legacy_runtime_evidence": legacy_evidence,
            "legacy_service_pid": (
                legacy_evidence["process_binding_after"]["service_pid"]
                if legacy_evidence is not None
                else 0
            ),
            "legacy_snapshot_id": (
                legacy_evidence["ledger"]["snapshot_id"]
                if legacy_evidence is not None
                else 0
            ),
            "legacy_recovery_audit_id": (
                legacy_evidence["ledger"]["recovery_audit_id"]
                if legacy_evidence is not None
                else 0
            ),
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY
  then
    echo "기존 서비스가 LIVE/PAPER 수동 일시정지·flat·0-order·0-auth 또는 v2 rollback 무결성 계약을 충족하지 않습니다." >&2
    exit 4
  fi
  IFS=$'\t' read -r EXPECTED_RUN_ID EXPECTED_PAUSE_STATE EXPECTED_PAUSE_REVISION \
    PREVIOUS_RELEASE_COMMIT LEGACY_RUNTIME_SAFETY_FIELDS_MISSING \
    LEGACY_RUNTIME_COMPATIBILITY EXPECTED_LEGACY_SERVICE_PID \
    EXPECTED_LEGACY_SNAPSHOT_ID EXPECTED_LEGACY_RECOVERY_AUDIT_ID <<< \
    "$(PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
      "$RUNTIME_VENV/bin/python" -P -c \
      'import json,sys; p=json.load(open(sys.argv[1], encoding="utf-8")); print(p["run_id"], p["pause_state"], p["pause_revision"], p["release_commit"], str(p["legacy_runtime_safety_fields_missing"]).lower(), str(p["legacy_runtime_compatibility"]).lower(), p["legacy_service_pid"], p["legacy_snapshot_id"], p["legacy_recovery_audit_id"], sep="\t")' \
      "$PREFLIGHT_IDENTITY")"
fi

verify_legacy_runtime_contract_file() {
  local dashboard_file="$1"
  local evidence_file="$2"
  local require_same_pid="${3:-false}"
  local minimum_snapshot_id="${4:-$EXPECTED_LEGACY_SNAPSHOT_ID}"
  local minimum_recovery_audit_id="${5:-$EXPECTED_LEGACY_RECOVERY_AUDIT_ID}"
  if [[ "$LEGACY_RUNTIME_COMPATIBILITY" != "true" ]]; then
    return 0
  fi
  local -a arguments=(
    --dashboard "$dashboard_file"
    --ledger "$RUNTIME_ROOT/active-ledger/run-ledger.sqlite3"
    --runtime-root "$RUNTIME_ROOT"
    --manifest "$PREVIOUS_RELEASE/release-manifest.json"
    --expected-run-id "$EXPECTED_RUN_ID"
    --expected-pause-revision "$EXPECTED_PAUSE_REVISION"
    --minimum-snapshot-id "$minimum_snapshot_id"
    --minimum-recovery-audit-id "$minimum_recovery_audit_id"
  )
  if [[ "$require_same_pid" == "true" ]]; then
    arguments+=(--expected-service-pid "$EXPECTED_LEGACY_SERVICE_PID")
  fi
  if ! PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
    PYTHONPATH="$VERIFIER_RELEASE" "$RUNTIME_VENV/bin/python" -P \
    "$VERIFIER_RELEASE/scripts/verify_compatibility_runtime_preflight.py" \
    "${arguments[@]}" > "$evidence_file"; then
    echo "legacy dashboard·원장·LaunchAgent 동등 안전 검증이 실패했습니다." >&2
    return 1
  fi
}

verify_legacy_offline_after_stop() {
  if [[ "$LEGACY_RUNTIME_COMPATIBILITY" != "true" ]]; then
    return 0
  fi
  local dashboard_file="$SUPPORT_DIR/latest-install-prestop-bracket-dashboard.json"
  local bracket_evidence="$SUPPORT_DIR/latest-install-prestop-bracket-legacy-evidence.json"
  local offline_evidence="$SUPPORT_DIR/latest-install-poststop-legacy-evidence.json"
  local minimum_snapshot_id=""
  local minimum_recovery_audit_id=""
  if [[ ! -f "$dashboard_file" || ! -f "$bracket_evidence" ]] || \
    ! IFS=$'\t' read -r minimum_snapshot_id minimum_recovery_audit_id <<< \
    "$(PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
      "$RUNTIME_VENV/bin/python" -P -c \
      'import json,sys; ledger=json.load(open(sys.argv[1], encoding="utf-8"))["ledger"]; print(ledger["snapshot_id"], ledger["recovery_audit_id"], sep="\t")' \
      "$bracket_evidence")"; then
    echo "post-stop legacy 검증의 bracket 증거를 읽지 못했습니다." >&2
    return 1
  fi
  if ! PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
    PYTHONPATH="$VERIFIER_RELEASE" "$RUNTIME_VENV/bin/python" -P \
    "$VERIFIER_RELEASE/scripts/verify_compatibility_runtime_preflight.py" \
    --dashboard "$dashboard_file" \
    --ledger "$RUNTIME_ROOT/active-ledger/run-ledger.sqlite3" \
    --runtime-root "$RUNTIME_ROOT" \
    --manifest "$PREVIOUS_RELEASE/release-manifest.json" \
    --expected-run-id "$EXPECTED_RUN_ID" \
    --expected-pause-revision "$EXPECTED_PAUSE_REVISION" \
    --minimum-snapshot-id "$minimum_snapshot_id" \
    --minimum-recovery-audit-id "$minimum_recovery_audit_id" \
    --require-stopped > "$offline_evidence"; then
    echo "writer 종료 뒤 legacy 원장 flat 재검증이 실패했습니다." >&2
    return 1
  fi
}

abort_before_transition() {
  local failure_reason="$1"
  local original_exit="$2"
  echo "실행 서비스 전환 전에 설치 준비가 실패했습니다: $failure_reason" >&2
  exit "$original_exit"
}

STAGE_RESULT="$SUPPORT_DIR/latest-release-stage.json"
if ! PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  "$SOURCE_PROJECT_DIR/.venv/bin/python" -P \
  "$SOURCE_PROJECT_DIR/scripts/stage_macos_release.py" \
  --source-root "$SOURCE_PROJECT_DIR" \
  --runtime-root "$RUNTIME_ROOT" \
  --market-archive "$MARKET_ARCHIVE_PATH" \
  --active-ledger-dir "$RUNTIME_ROOT/active-ledger" > "$STAGE_RESULT"; then
  abort_before_transition "STAGE_FAILED" 4
fi
if ! PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  "$RUNTIME_VENV/bin/python" -P -c \
  'import json,sys
payload=json.loads(open(sys.argv[1], encoding="utf-8").read())
if payload.get("status") != "STAGED":
    raise RuntimeError("stage 결과가 STAGED가 아닙니다.")' \
  "$STAGE_RESULT"; then
  abort_before_transition "STAGE_RESULT_INVALID" 4
fi
if ! ROLLBACK_RELEASE="$(PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  "$RUNTIME_VENV/bin/python" -P -c \
  'import json,sys; value=json.load(open(sys.argv[1], encoding="utf-8"))["release"].get("rollback_release"); print(value or "")' \
  "$STAGE_RESULT")"; then
  abort_before_transition "ROLLBACK_RELEASE_PARSE_FAILED" 4
fi
if [[ "$FIRST_INSTALL" == "true" ]]; then
  if [[ -n "$ROLLBACK_RELEASE" ]]; then
    echo "최초 설치인데 예상하지 못한 rollback_release가 기록됐습니다." >&2
    abort_before_transition "UNEXPECTED_FIRST_INSTALL_ROLLBACK" 4
  fi
elif [[ "$ROLLBACK_RELEASE" != "$PREVIOUS_RELEASE" ]]; then
  echo "stage 결과의 rollback_release가 preflight 대상과 다릅니다." >&2
  abort_before_transition "ROLLBACK_RELEASE_MISMATCH" 4
fi
if ! PROJECT_DIR="$(PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  "$RUNTIME_VENV/bin/python" -P -c \
  'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["release"]["release_path"])' \
  "$STAGE_RESULT")"; then
  abort_before_transition "STAGED_RELEASE_PARSE_FAILED" 4
fi
if [[ ! -f "$PROJECT_DIR/release-manifest.json" ]]; then
  echo "불변 릴리스 준비에 실패했습니다." >&2
  abort_before_transition "RELEASE_MANIFEST_MISSING" 1
fi
RELEASE_RUNNER_SCRIPT="$PROJECT_DIR/scripts/run_macos_service.sh"
if [[ ! -f "$RELEASE_RUNNER_SCRIPT" ]]; then
  echo "불변 PAPER 서비스 실행기가 없습니다: $RELEASE_RUNNER_SCRIPT" >&2
  abort_before_transition "RUNNER_MISSING" 1
fi
if ! VERIFIER_RELEASE="$(cd "$PROJECT_DIR" && pwd -P)"; then
  abort_before_transition "VERIFIER_RELEASE_RESOLVE_FAILED" 1
fi

if ! EXPECTED_RELEASE_COMMIT="$(PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  "$RUNTIME_VENV/bin/python" -P -c \
  'import json,sys; print(json.loads(open(sys.argv[1], encoding="utf-8").read())["commit"])' \
  "$PROJECT_DIR/release-manifest.json")"; then
  abort_before_transition "RELEASE_COMMIT_PARSE_FAILED" 1
fi
if ! ACTIVATION_PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd -P)"; then
  abort_before_transition "ACTIVATION_PATH_RESOLVE_FAILED" 1
fi

verify_staged_source_commit_still_final() {
  local source_status=""
  local source_commit=""
  if ! source_status="$(git -C "$SOURCE_PROJECT_DIR" status --porcelain --untracked-files=all)" || \
    [[ -n "$source_status" ]]; then
    echo "stage 후 source worktree가 clean 상태가 아닙니다." >&2
    return 1
  fi
  if ! source_commit="$(git -C "$SOURCE_PROJECT_DIR" rev-parse HEAD)" || \
    [[ "$source_commit" != "$EXPECTED_RELEASE_COMMIT" ]]; then
    echo "stage 후 source HEAD가 준비된 최종 릴리스 commit과 다릅니다." >&2
    return 1
  fi
}

if [[ "$PREPARE_ONLY" == "true" ]]; then
  echo "PASS: 불변 PAPER 릴리스 STAGED 완료 · 실행 서비스와 current 유지"
  echo "현재 서비스·current·외부 runner·anchor·LaunchAgent plist는 변경하지 않았습니다."
  echo "준비된 릴리스: $ACTIVATION_PROJECT_DIR"
  exit 0
fi

stop_loaded_service() {
  local service_pid=""
  local service_snapshot=""
  if service_snapshot="$(launchctl print "$SERVICE_TARGET" 2>/dev/null)"; then
    service_pid="$(printf '%s\n' "$service_snapshot" | awk '$1 == "pid" && $2 == "=" { print $3; exit }')"
    if ! launchctl bootout "$SERVICE_TARGET"; then
      echo "LaunchAgent 안전 종료 요청이 실패했습니다." >&2
      return 1
    fi
  fi
  if [[ -n "$service_pid" ]]; then
    for shutdown_wait in {1..60}; do
      if ! kill -0 "$service_pid" 2>/dev/null; then
        break
      fi
      sleep 1
    done
    if kill -0 "$service_pid" 2>/dev/null; then
      echo "PAPER 서비스가 60초 안에 안전 종료되지 않았습니다: PID $service_pid" >&2
      return 1
    fi
  fi
}

bootstrap_launch_agent() {
  local bootstrap_succeeded="false"
  for attempt in 1 2 3; do
    if launchctl bootstrap "gui/$USER_ID" "$TARGET_PLIST"; then
      bootstrap_succeeded="true"
      break
    fi
    if (( attempt < 3 )); then
      echo "LaunchAgent 등록이 아직 정리 중입니다. ${attempt}/3 재시도합니다." >&2
      launchctl bootout "$SERVICE_TARGET" >/dev/null 2>&1 || true
      sleep 1
    fi
  done
  if [[ "$bootstrap_succeeded" != "true" ]]; then
    echo "LaunchAgent 등록이 3회 연속 실패했습니다: $TARGET_PLIST" >&2
    return 1
  fi
}

bootstrap_launch_agent_once() {
  if ! launchctl_command bootstrap "gui/$USER_ID" "$TARGET_PLIST"; then
    echo "유지보수 정지 설치의 새 LaunchAgent 1회 등록이 실패했습니다: $TARGET_PLIST" >&2
    return 1
  fi
}

verify_service_fully_stopped() {
  verify_launch_agent_absent_exact() {
    local phase="$1"
    local output=""
    local launchctl_status=0
    local expected="Bad request.
Could not find service \"$LABEL\" in domain for user gui: $USER_ID"
    if output="$(/bin/launchctl print "$SERVICE_TARGET" 2>&1)"; then
      launchctl_status=0
    else
      launchctl_status=$?
    fi
    if (( launchctl_status != 113 )) || [[ "$output" != "$expected" ]]; then
      echo "LaunchAgent $phase 부재 응답이 exact 계약과 다릅니다." >&2
      return 1
    fi
  }
  if ! verify_launch_agent_absent_exact "초기"; then
    return 1
  fi
  local lsof_probe_output="$SUPPORT_DIR/latest-install-poststop-lsof-probe.txt"
  local listener_output="$SUPPORT_DIR/latest-install-poststop-listener.txt"
  if ! /usr/sbin/lsof -nP -p "$$" > "$lsof_probe_output" 2>&1 || \
    [[ ! -s "$lsof_probe_output" ]]; then
    echo "lsof 자체 진단이 실패해 listener 부재를 확정할 수 없습니다." >&2
    return 1
  fi
  if /usr/sbin/lsof -nP -iTCP:8870 -sTCP:LISTEN > "$listener_output" 2>&1; then
    echo "TCP 8870 listener가 bootout 뒤에도 남아 있습니다." >&2
    return 1
  fi
  if [[ -s "$listener_output" ]]; then
    echo "TCP 8870 listener 부재를 확정하지 못했습니다." >&2
    return 1
  fi
  local ledger_path="$ACTIVE_LEDGER_DIR/run-ledger.sqlite3"
  local ledger_candidate=""
  for ledger_candidate in "$ledger_path" "$ledger_path-wal" "$ledger_path-shm"; do
    if [[ ! -e "$ledger_candidate" && ! -L "$ledger_candidate" ]]; then
      continue
    fi
    if [[ -L "$ledger_candidate" || ! -f "$ledger_candidate" ]]; then
      echo "active ledger 계열 경로가 regular file이 아닙니다: $ledger_candidate" >&2
      return 1
    fi
    local ledger_output="$SUPPORT_DIR/latest-install-poststop-${ledger_candidate:t}-owner.txt"
    local ledger_error="$SUPPORT_DIR/latest-install-poststop-${ledger_candidate:t}-owner-error.txt"
    local ledger_status=0
    if /usr/sbin/lsof -nP -F0pfnDi "$ledger_candidate" \
      > "$ledger_output" 2> "$ledger_error"; then
      ledger_status=0
    else
      ledger_status=$?
    fi
    if (( ledger_status != 1 )) || [[ -s "$ledger_output" || -s "$ledger_error" ]]; then
      echo "active ledger 보유 프로세스가 bootout 뒤에도 남았거나 lsof 진단이 실패했습니다." >&2
      return 1
    fi
  done
  if ! verify_launch_agent_absent_exact "최종"; then
    return 1
  fi
}

write_launch_agent_plist() {
  local runner_script="$1"
  local verifier_template="$VERIFIER_RELEASE/packaging/macos/$LABEL.plist"
  if [[ -L "$verifier_template" || ! -f "$verifier_template" ]]; then
    echo "불변 verifier 릴리스의 LaunchAgent template이 regular file이 아닙니다: $verifier_template" >&2
    return 1
  fi
  if ! PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
    "$RUNTIME_VENV/bin/python" -P - \
    "$verifier_template" "$TARGET_PLIST" "$runner_script" \
    "$SERVICE_LOG" "$ERROR_LOG" <<'PY'
import os
from pathlib import Path
from sys import argv
from uuid import uuid4
from xml.sax.saxutils import escape

source, target = map(Path, argv[1:3])
payload = source.read_text(encoding="utf-8")
for placeholder, value in zip(
    ("__RUNNER_SCRIPT__", "__SERVICE_LOG__", "__ERROR_LOG__"),
    argv[3:],
    strict=True,
):
    payload = payload.replace(placeholder, escape(value))
temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
try:
    temporary.write_text(payload, encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, target)
finally:
    temporary.unlink(missing_ok=True)
PY
  then
    return 1
  fi
  xattr -d com.apple.provenance "$TARGET_PLIST" 2>/dev/null || true
  chmod 600 "$TARGET_PLIST" && plutil -lint "$TARGET_PLIST"
}

prepare_previous_release_for_rollback() {
  if [[ "$FIRST_INSTALL" == "true" ]]; then
    return 0
  fi
  PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYTHONPATH="$VERIFIER_RELEASE" \
    "$RUNTIME_VENV/bin/python" -P - "$RUNTIME_ROOT" "$PREVIOUS_RELEASE" <<'PY'
import sys
from pathlib import Path

from scripts.stage_macos_release import (
    _read_release_manifest,
    _verify_release_tree,
    migrate_legacy_release_manifest,
)

runtime_root = Path(sys.argv[1])
previous_release = Path(sys.argv[2])
manifest = _read_release_manifest(previous_release)
if manifest.get("schema_version") == 1:
    manifest = migrate_legacy_release_manifest(runtime_root, previous_release)
verified = _verify_release_tree(previous_release)
if verified != manifest or manifest.get("schema_version") != 2:
    raise RuntimeError("rollback release가 검증된 schema v2 tree가 아닙니다.")
PY
}

activate_staged_release() {
  PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYTHONPATH="$VERIFIER_RELEASE" \
    "$RUNTIME_VENV/bin/python" -P - \
    "$RUNTIME_ROOT" "$VERIFIER_RELEASE" "$PREVIOUS_RELEASE" "$FIRST_INSTALL" \
    > "$SUPPORT_DIR/latest-release-activation.json" <<'PY'
import json
import sys
from pathlib import Path

from scripts.stage_macos_release import activate_release, current_release

runtime_root = Path(sys.argv[1])
staged_release = Path(sys.argv[2])
previous_release = sys.argv[3]
first_install = sys.argv[4] == "true"
result = activate_release(
    runtime_root,
    staged_release,
    actor="CODEX_DEPLOY",
    reason="V6_VERIFIED_POSTSTOP_ACTIVATION",
)
if current_release(runtime_root) != staged_release.resolve(strict=True):
    raise RuntimeError("activation 뒤 current 포인터가 staged release와 다릅니다.")
rollback = result.get("rollback_release")
if first_install:
    if rollback is not None:
        raise RuntimeError("최초 설치 activation에 rollback release가 생겼습니다.")
elif rollback != previous_release:
    raise RuntimeError("activation rollback release가 preflight release와 다릅니다.")
print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
PY
}

install_transition_artifacts() {
  if ! install_trusted_runner_from_release "$VERIFIER_RELEASE"; then
    return 1
  fi
  if ! write_release_integrity_anchor "$VERIFIER_RELEASE" "$VERIFIER_RELEASE"; then
    return 1
  fi
  if ! write_launch_agent_plist "$TRUSTED_RUNNER_SCRIPT"; then
    return 1
  fi
  chmod 700 "$TRUSTED_RUNNER_SCRIPT" && \
    chmod 755 "$VERIFIER_RELEASE/scripts/run_macos_service.sh"
}

prepare_maintenance_artifact_backup() {
  if [[ "$MAINTENANCE_STOPPED" != "true" ]]; then
    return 0
  fi
  if [[ -e "$MAINTENANCE_ARTIFACT_BACKUP" || -L "$MAINTENANCE_ARTIFACT_BACKUP" ]]; then
    echo "유지보수 정지 artifact backup 경로가 이미 있습니다: $MAINTENANCE_ARTIFACT_BACKUP" >&2
    return 1
  fi
  if ! mkdir "$MAINTENANCE_ARTIFACT_BACKUP" || ! chmod 700 "$MAINTENANCE_ARTIFACT_BACKUP"; then
    echo "유지보수 정지 artifact backup 디렉터리를 만들지 못했습니다." >&2
    return 1
  fi
  local artifact=""
  local artifact_name=""
  for artifact in \
    "$TARGET_PLIST" \
    "$TRUSTED_RUNNER_SCRIPT" \
    "$RELEASE_INTEGRITY_ANCHOR" \
    "$RUNTIME_ROOT/current-deployment.json"; do
    if [[ -L "$artifact" || ! -f "$artifact" ]]; then
      echo "구 릴리스 artifact가 regular file이 아닙니다: $artifact" >&2
      return 1
    fi
    artifact_name="${artifact:t}"
    if ! ditto "$artifact" "$MAINTENANCE_ARTIFACT_BACKUP/$artifact_name" || \
      ! cmp -s "$artifact" "$MAINTENANCE_ARTIFACT_BACKUP/$artifact_name"; then
      echo "구 릴리스 artifact backup이 실패했습니다: $artifact" >&2
      return 1
    fi
  done
}

restore_maintenance_artifacts() {
  if [[ ! -d "$MAINTENANCE_ARTIFACT_BACKUP" || -L "$MAINTENANCE_ARTIFACT_BACKUP" ]]; then
    echo "유지보수 정지 artifact backup이 canonical directory가 아닙니다." >&2
    return 1
  fi
  local artifact=""
  local artifact_name=""
  local backup=""
  local temporary=""
  for artifact in \
    "$TARGET_PLIST" \
    "$TRUSTED_RUNNER_SCRIPT" \
    "$RELEASE_INTEGRITY_ANCHOR" \
    "$RUNTIME_ROOT/current-deployment.json"; do
    artifact_name="${artifact:t}"
    backup="$MAINTENANCE_ARTIFACT_BACKUP/$artifact_name"
    temporary="${artifact}.$$.maintenance-restore"
    if [[ -L "$backup" || ! -f "$backup" || -L "$artifact" || \
      ( -e "$artifact" && ! -f "$artifact" ) || -e "$temporary" || -L "$temporary" ]]; then
      echo "유지보수 정지 artifact 복구 경로가 regular file 계약이 아닙니다: $artifact" >&2
      return 1
    fi
    if ! ditto "$backup" "$temporary" || ! cmp -s "$backup" "$temporary" || \
      ! /bin/mv -f "$temporary" "$artifact" || ! cmp -s "$backup" "$artifact"; then
      if [[ -e "$temporary" || -L "$temporary" ]]; then
        /bin/unlink "$temporary" 2>/dev/null || true
      fi
      echo "유지보수 정지 artifact를 원자적으로 복구하지 못했습니다: $artifact" >&2
      return 1
    fi
  done
}

cleanup_maintenance_artifact_backup() {
  if [[ ! -e "$MAINTENANCE_ARTIFACT_BACKUP" && ! -L "$MAINTENANCE_ARTIFACT_BACKUP" ]]; then
    return 0
  fi
  if [[ ! -d "$MAINTENANCE_ARTIFACT_BACKUP" || -L "$MAINTENANCE_ARTIFACT_BACKUP" ]]; then
    echo "정리할 유지보수 artifact backup이 canonical directory가 아닙니다." >&2
    return 1
  fi
  local artifact_name=""
  for artifact_name in \
    "$LABEL.plist" \
    "run_macos_service.sh" \
    "current-release-integrity.json" \
    "current-deployment.json"; do
    if [[ -e "$MAINTENANCE_ARTIFACT_BACKUP/$artifact_name" || \
      -L "$MAINTENANCE_ARTIFACT_BACKUP/$artifact_name" ]]; then
      if [[ -L "$MAINTENANCE_ARTIFACT_BACKUP/$artifact_name" || \
        ! -f "$MAINTENANCE_ARTIFACT_BACKUP/$artifact_name" ]] || \
        ! /bin/unlink "$MAINTENANCE_ARTIFACT_BACKUP/$artifact_name"; then
        echo "유지보수 artifact backup 파일을 정리하지 못했습니다: $artifact_name" >&2
        return 1
      fi
    fi
  done
  if ! rmdir "$MAINTENANCE_ARTIFACT_BACKUP"; then
    echo "유지보수 artifact backup 디렉터리가 비어 있지 않습니다." >&2
    return 1
  fi
}

activate_previous_release_stopped() {
  PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYTHONPATH="$VERIFIER_RELEASE" \
    "$RUNTIME_VENV/bin/python" -P - \
    "$RUNTIME_ROOT" "$PREVIOUS_RELEASE" "$1" > "$ROLLBACK_RESULT" <<'PY'
import json
import sys
from pathlib import Path

from scripts.stage_macos_release import activate_release, current_release

runtime_root = Path(sys.argv[1])
previous_release = Path(sys.argv[2]).resolve(strict=True)
failure_reason = sys.argv[3]
result = activate_release(
    runtime_root,
    previous_release,
    actor="CODEX_DEPLOY_ROLLBACK_STOPPED",
    reason=f"MAINTENANCE_STOPPED_INSTALL_FAILURE_{failure_reason}",
)
if current_release(runtime_root) != previous_release:
    raise RuntimeError("정지 rollback 뒤 current가 구 릴리스와 다릅니다.")
print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
PY
}

rollback_previous_release_stopped() {
  local failure_reason="$1"
  echo "유지보수 정지 설치 실패로 구 릴리스 artifact를 중지 상태로 복구합니다: $failure_reason" >&2
  if ! stop_loaded_service; then
    echo "정지 rollback 전 새 릴리스 종료가 실패했습니다." >&2
    return 1
  fi
  launchctl_command bootout "$SERVICE_TARGET" >/dev/null 2>&1 || true
  if ! verify_stopped_process_absence_exact "maintenance-rollback-before"; then
    echo "정지 rollback 전 LaunchAgent·listener·ledger holder 부재를 확정하지 못했습니다." >&2
    return 1
  fi
  UNVERIFIED_SERVICE_MAY_BE_RUNNING="false"
  if [[ -z "$ROLLBACK_RELEASE" || "$ROLLBACK_RELEASE" != "$PREVIOUS_RELEASE" ]]; then
    echo "정지 rollback 릴리스가 예상 구 릴리스와 다릅니다." >&2
    return 1
  fi
  if ! activate_previous_release_stopped "$failure_reason"; then
    echo "구 릴리스 current 포인터를 복구하지 못했습니다." >&2
    return 1
  fi
  if ! restore_maintenance_artifacts; then
    return 1
  fi
  if [[ ! -L "$CURRENT_POINTER" || \
    "$(cd "$CURRENT_POINTER" 2>/dev/null && pwd -P)" != "$PREVIOUS_RELEASE" ]]; then
    echo "정지 rollback 후 current 포인터가 구 릴리스와 다릅니다." >&2
    return 1
  fi
  if ! verify_stopped_process_absence_exact "maintenance-rollback-after"; then
    echo "구 릴리스 artifact 복구 후 정지 상태가 깨졌습니다." >&2
    return 1
  fi
  MAINTENANCE_TRANSITION_STARTED="false"
  if ! cleanup_maintenance_artifact_backup; then
    return 1
  fi
  echo "구 릴리스 artifact를 복구했고 구 LaunchAgent는 재시작하지 않았습니다." >&2
}

dashboard_matches_install_contract() {
  local expected_commit="$1"
  local preserve_identity="$2"
  local allow_legacy_missing="$3"
  PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
    "$RUNTIME_VENV/bin/python" -P -c \
    'import json,math,sys
payload=json.load(sys.stdin)
expected_commit, preserve_identity, allow_legacy_missing, expected_run, expected_pause_state, expected_revision=sys.argv[1:]
status=payload["status"]
system=payload["system"]
risk=payload["risk"]
operation=payload["operation_status"]
intent=payload["paper_entry_intent"]

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def require_count(field):
    value=payload.get(field)
    require(type(value) is int and value >= 0, f"{field}가 0 이상의 int가 아닙니다: {value!r}")
    return value

def require_runtime_health():
    for field, maximum in (("lag_p95_ms", 500.0), ("trade_lag_p95_ms", 1000.0), ("persistence_flush_last_ms", 20000.0)):
        value=system.get(field)
        require(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= maximum, f"{field}가 유한한 0..{maximum} 숫자가 아닙니다: {value!r}")
    flush_count=system.get("persistence_flush_count")
    require(type(flush_count) is int and flush_count >= 4, f"persistence_flush_count가 4 이상의 int가 아닙니다: {flush_count!r}")
    fault_count=system.get("persistence_fault_count")
    recovery_count=system.get("persistence_recovery_count")
    dropped_count=system.get("persistence_buffer_dropped")
    require(type(fault_count) is int and fault_count >= 0, f"persistence_fault_count가 0 이상의 int가 아닙니다: {fault_count!r}")
    require(type(recovery_count) is int and recovery_count == fault_count, f"persistence_recovery_count가 fault 누적과 다릅니다: fault={fault_count!r}, recovery={recovery_count!r}")
    require(type(dropped_count) is int and dropped_count >= 0, f"persistence_buffer_dropped가 0 이상의 int가 아닙니다: {dropped_count!r}")
    require(system.get("persistence_fault_active") is False, "persistence fault가 활성 상태입니다.")
    require(system.get("persistence_fault_recoverable") is False, "persistence recovery가 아직 진행 중입니다.")
    require(system.get("persistence_last_error") == "NONE", "persistence 현재 오류가 남아 있습니다.")
    if fault_count > 0:
        require(recovery_count > 0, "persistence fault 복구 이력이 없습니다.")
        recovered_ts=system.get("persistence_last_recovered_ts_ms")
        completed_ts=system.get("persistence_flush_last_completed_ts_ms")
        require(type(recovered_ts) is int and recovered_ts > 0, f"마지막 persistence 복구 시각이 올바르지 않습니다: {recovered_ts!r}")
        require(type(completed_ts) is int and completed_ts >= recovered_ts, f"마지막 persistence 복구 뒤 성공 flush가 증명되지 않았습니다: recovered={recovered_ts!r}, flushed={completed_ts!r}")
    require(system.get("persistence_worker_warmed") is True, "persistence worker가 warmed 상태가 아닙니다.")
    require(system.get("storage_entry_allowed") is True, "storage entry가 허용 상태가 아닙니다.")

flat_fields=("main_pending_entry_count", "league_pending_entry_count", "total_pending_entry_count", "total_open_position_count", "paper_portfolio_flat")
reported_flat_fields=[field for field in flat_fields if field in payload]
if reported_flat_fields or allow_legacy_missing != "true":
    require(len(reported_flat_fields) == len(flat_fields), "dashboard flat 집계 필드가 일부만 보고됐습니다.")
    main_pending=require_count("main_pending_entry_count")
    league_pending=require_count("league_pending_entry_count")
    total_pending=require_count("total_pending_entry_count")
    total_open=require_count("total_open_position_count")
    require(total_pending == main_pending + league_pending, "total pending이 main+league와 다릅니다.")
    require(main_pending == 0, "main pending entry가 남아 있습니다.")
    require(league_pending == 0, "league pending entry가 남아 있습니다.")
    require(total_pending == 0, "total pending entry가 남아 있습니다.")
    require(total_open == 0, "open PAPER position이 남아 있습니다.")
    require(payload.get("paper_portfolio_flat") is True, "paper_portfolio_flat이 명시적 True가 아닙니다.")
require(system["release_commit"] == expected_commit, "release commit이 다릅니다.")
require(system["release_isolated"] is True, "release_isolated가 True가 아닙니다.")
require(risk["paper_only"] is True, "PAPER only가 명시적 True가 아닙니다.")
if "funding_readiness" not in system:
    require(allow_legacy_missing == "true", "funding readiness가 누락됐습니다.")
else:
    require(system["funding_readiness"] == "NOT_READY", "funding readiness가 NOT_READY가 아닙니다.")
require(status["market_data_state"] == "LIVE", "market data가 LIVE가 아닙니다.")
require(status["execution_state"] == "PAPER", "execution state가 PAPER가 아닙니다.")
require(status["real_orders_enabled"] is False, "real order가 비활성이 아닙니다.")
require(status["auth_required"] is False, "auth가 비활성이 아닙니다.")
require(system["auth_headers"] is False, "auth header가 비활성이 아닙니다.")
for field in ("private_api_enabled", "api_key_enabled", "wallet_enabled", "runtime_ai_order_decision_enabled"):
    if field not in system:
        require(allow_legacy_missing == "true", f"{field}가 누락됐습니다.")
    else:
        require(system[field] is False, f"{field}가 비활성이 아닙니다.")
require(operation["market_observation_active"] is True, "시장 관찰이 활성이 아닙니다.")
require(payload.get("position") is None, "main position이 flat이 아닙니다.")
require(payload.get("focus_positions") == [], "focus position이 flat이 아닙니다.")
require(payload.get("league_positions") == [], "league position이 flat이 아닙니다.")
if preserve_identity == "true":
    require(status["run_id"] == expected_run, "run_id가 다릅니다.")
    require(payload["paused"] is True, "PAPER entry가 일시정지 상태가 아닙니다.")
    require(operation["state"] == "MANUALLY_PAUSED", "수동 일시정지 상태가 아닙니다.")
    require(operation["paper_entry_active"] is False, "PAPER entry가 비활성이 아닙니다.")
    require(operation["automatic_recovery"] is False, "수동 일시정지에서 자동 복구가 활성입니다.")
    require(intent["state"] == expected_pause_state, "pause state가 다릅니다.")
    require(intent["manual_pause_requested"] is True, "manual pause 의도가 True가 아닙니다.")
    require(type(intent.get("revision")) is int, "pause revision이 int가 아닙니다.")
    require(intent["revision"] == int(expected_revision), "pause revision이 다릅니다.")
else:
    require(operation["automatic_recovery"] is True, "최초 설치 자동 복구가 활성이 아닙니다.")
require_runtime_health()' \
    "$expected_commit" "$preserve_identity" "$allow_legacy_missing" "$EXPECTED_RUN_ID" \
    "$EXPECTED_PAUSE_STATE" "$EXPECTED_PAUSE_REVISION"
}

verify_persistence_counters_unchanged() {
  local earlier_dashboard="$1"
  local later_dashboard="$2"
  PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
    "$RUNTIME_VENV/bin/python" -P -c \
    'import json,sys
earlier=json.load(open(sys.argv[1], encoding="utf-8"))["system"]
later=json.load(open(sys.argv[2], encoding="utf-8"))["system"]
for field in ("persistence_fault_count", "persistence_recovery_count", "persistence_buffer_dropped", "persistence_last_recovered_ts_ms"):
    if earlier.get(field) != later.get(field):
        raise RuntimeError(f"설치 검증 중 {field}가 증가하거나 변경됐습니다: {earlier.get(field)!r} -> {later.get(field)!r}")' \
    "$earlier_dashboard" "$later_dashboard"
}

maintenance_postflight_contract() {
  local earlier_dashboard="$1"
  local later_dashboard="$2"
  PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
    "$RUNTIME_VENV/bin/python" -P -c \
    'import json,math,sys
evidence=json.load(open(sys.argv[1], encoding="utf-8"))
earlier=json.load(open(sys.argv[2], encoding="utf-8"))["system"]
later=json.load(open(sys.argv[3], encoding="utf-8"))["system"]
baseline=evidence["baseline"]

def require(condition, message):
    if not condition:
        raise RuntimeError(message)

def count(payload, field):
    value=payload.get(field)
    require(type(value) is int and value >= 0, f"{field}가 0 이상 int가 아닙니다: {value!r}")
    return value

for label,payload in (("first", earlier), ("final", later)):
    queue_depth=count(payload, "queue_depth")
    require(queue_depth <= 64, f"{label} postflight queue depth가 64를 넘었습니다: {queue_depth}")
    require(payload.get("queue_overload_active") is False, f"{label} postflight queue overload가 활성입니다.")
    require(payload.get("critical_lag_active") is False, f"{label} postflight critical lag가 활성입니다.")
    require(payload.get("persistence_fault_active") is False, f"{label} postflight persistence fault가 활성입니다.")
    for field,maximum in (("lag_p95_ms",500.0),("trade_lag_p95_ms",1000.0)):
        value=payload.get(field)
        require(type(value) in (int,float) and math.isfinite(value) and 0 <= value <= maximum, f"{label} postflight {field}가 임계를 넘었습니다: {value!r}")
for field in ("queue_overload_drop_count", "critical_lag_incident_count", "persistence_buffer_dropped", "persistence_fault_count"):
    before=count(earlier, field)
    after=count(later, field)
    require(before <= int(baseline[field]), f"postflight 최초 {field}가 정지 전 baseline보다 커졌습니다: {baseline[field]!r} -> {before!r}")
    require(after == before, f"postflight 관찰 중 {field}가 변경됐습니다: {before!r} -> {after!r}")' \
    "$MAINTENANCE_STOPPED_EVIDENCE" "$earlier_dashboard" "$later_dashboard"
}

verify_maintenance_postflight_stable() {
  local first_payload=""
  local final_payload=""
  if ! first_payload="$(curl -fsS --max-time 3 http://127.0.0.1:8870/api/dashboard)" || \
    ! write_text_file_atomic "$MAINTENANCE_POSTFLIGHT_FIRST" "$first_payload" || \
    ! dashboard_matches_install_contract "$EXPECTED_RELEASE_COMMIT" "true" "false" \
      < "$MAINTENANCE_POSTFLIGHT_FIRST"; then
    echo "유지보수 설치 첫 postflight dashboard가 동일 Run·pause·PAPER 계약을 충족하지 않습니다." >&2
    return 1
  fi
  sleep 5
  if ! final_payload="$(curl -fsS --max-time 3 http://127.0.0.1:8870/api/dashboard)" || \
    ! write_text_file_atomic "$MAINTENANCE_POSTFLIGHT_FINAL" "$final_payload" || \
    ! dashboard_matches_install_contract "$EXPECTED_RELEASE_COMMIT" "true" "false" \
      < "$MAINTENANCE_POSTFLIGHT_FINAL"; then
    echo "유지보수 설치 최종 postflight dashboard가 동일 Run·pause·PAPER 계약을 충족하지 않습니다." >&2
    return 1
  fi
  if ! maintenance_postflight_contract \
    "$MAINTENANCE_POSTFLIGHT_FIRST" "$MAINTENANCE_POSTFLIGHT_FINAL"; then
    echo "postflight queue·critical lag·drop·fault 안정성 계약을 충족하지 않습니다." >&2
    return 1
  fi
}

verify_loaded_service_unchanged_before_stop() {
  if [[ "$FIRST_INSTALL" == "true" ]]; then
    return 0
  fi
  if [[ "$MAINTENANCE_STOPPED" == "true" ]]; then
    verify_maintenance_stopped_evidence "pre-transition-maintenance"
    return $?
  fi
  local prestop_dashboard="$SUPPORT_DIR/latest-install-prestop-dashboard.json"
  if ! curl -fsS --max-time 3 http://127.0.0.1:8870/api/dashboard > "$prestop_dashboard"; then
    echo "서비스 중지 직전 기존 PAPER dashboard를 다시 확인하지 못했습니다." >&2
    return 1
  fi
  if ! dashboard_matches_install_contract "$PREVIOUS_RELEASE_COMMIT" "true" \
    "$LEGACY_RUNTIME_COMPATIBILITY" < "$prestop_dashboard"; then
    echo "서비스 중지 직전 Run·pause revision·flat·PAPER 안전 상태가 preflight와 달라졌습니다." >&2
    return 1
  fi
  if ! verify_persistence_counters_unchanged \
    "$PREFLIGHT_DASHBOARD" "$prestop_dashboard"; then
    echo "설치 preflight 이후 persistence fault 또는 drop 누적치가 달라졌습니다." >&2
    return 1
  fi
  local prestop_legacy_evidence="$SUPPORT_DIR/latest-install-prestop-legacy-evidence.json"
  if ! verify_legacy_runtime_contract_file \
    "$prestop_dashboard" "$prestop_legacy_evidence" "true"; then
    echo "서비스 중지 직전 legacy 원장·프로세스 증명이 달라졌습니다." >&2
    return 1
  fi
  if [[ "$LEGACY_RUNTIME_COMPATIBILITY" == "true" ]]; then
    local bracket_snapshot_id=""
    local bracket_recovery_audit_id=""
    if ! IFS=$'\t' read -r bracket_snapshot_id bracket_recovery_audit_id <<< \
      "$(PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
        "$RUNTIME_VENV/bin/python" -P -c \
      'import json,sys; ledger=json.load(open(sys.argv[1], encoding="utf-8"))["ledger"]; print(ledger["snapshot_id"], ledger["recovery_audit_id"], sep="\t")' \
      "$prestop_legacy_evidence")"; then
      echo "중지 직전 첫 legacy snapshot/audit ID를 읽지 못했습니다." >&2
      return 1
    fi
    local bracket_dashboard="$SUPPORT_DIR/latest-install-prestop-bracket-dashboard.json"
    local bracket_evidence="$SUPPORT_DIR/latest-install-prestop-bracket-legacy-evidence.json"
    if ! curl -fsS --max-time 3 http://127.0.0.1:8870/api/dashboard > "$bracket_dashboard" || \
      ! dashboard_matches_install_contract "$PREVIOUS_RELEASE_COMMIT" "true" \
        "$LEGACY_RUNTIME_COMPATIBILITY" < "$bracket_dashboard" || \
      ! verify_persistence_counters_unchanged \
        "$prestop_dashboard" "$bracket_dashboard" || \
      ! verify_legacy_runtime_contract_file \
        "$bracket_dashboard" "$bracket_evidence" "true" \
        "$bracket_snapshot_id" "$bracket_recovery_audit_id"; then
      echo "서비스 중지 직전 legacy dashboard·원장 bracket 재검증이 실패했습니다." >&2
      return 1
    fi
    if ! IFS=$'\t' read -r EXPECTED_LEGACY_SNAPSHOT_ID \
      EXPECTED_LEGACY_RECOVERY_AUDIT_ID <<< \
      "$(PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
        "$RUNTIME_VENV/bin/python" -P -c \
        'import json,sys; ledger=json.load(open(sys.argv[1], encoding="utf-8"))["ledger"]; print(ledger["snapshot_id"], ledger["recovery_audit_id"], sep="\t")' \
        "$bracket_evidence")"; then
      echo "중지 직전 최종 legacy snapshot/audit ID를 읽지 못했습니다." >&2
      return 1
    fi
  fi
}

cleanup_failed_first_install() {
  if [[ "$FIRST_INSTALL" != "true" ]]; then
    return 1
  fi
  if ! verify_service_fully_stopped; then
    echo "최초 설치 실패 artifact 정리 전 서비스 부재를 확정하지 못했습니다." >&2
    return 1
  fi
  if [[ -e "$CURRENT_POINTER" || -L "$CURRENT_POINTER" ]]; then
    if [[ ! -L "$CURRENT_POINTER" ]] || \
      [[ "$(cd "$CURRENT_POINTER" 2>/dev/null && pwd -P)" != "$VERIFIER_RELEASE" ]]; then
      echo "최초 설치 실패 current 포인터가 staged verifier와 다릅니다." >&2
      return 1
    fi
  fi
  for generated_artifact in \
    "$TARGET_PLIST" \
    "$TRUSTED_RUNNER_SCRIPT" \
    "$RELEASE_INTEGRITY_ANCHOR" \
    "$RUNTIME_ROOT/current-deployment.json"; do
    if [[ -L "$generated_artifact" ]] || \
      [[ -e "$generated_artifact" && ! -f "$generated_artifact" ]]; then
      echo "최초 설치 실패 artifact가 regular file이 아닙니다: $generated_artifact" >&2
      return 1
    fi
  done
  if [[ -f "$RUNTIME_ROOT/current-deployment.json" ]]; then
    if ! PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
      "$RUNTIME_VENV/bin/python" -P -c \
      'import json,sys
from pathlib import Path
payload=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("previous_state") != "NONE" or payload.get("new_state") != sys.argv[2]:
    raise RuntimeError("first-install deployment가 staged verifier 전환이 아닙니다.")' \
      "$RUNTIME_ROOT/current-deployment.json" "$VERIFIER_RELEASE"; then
      echo "최초 설치 deployment 상태를 안전하게 정리할 수 없습니다." >&2
      return 1
    fi
  fi
  for generated_artifact in \
    "$TARGET_PLIST" \
    "$TRUSTED_RUNNER_SCRIPT" \
    "$RELEASE_INTEGRITY_ANCHOR" \
    "$RUNTIME_ROOT/current-deployment.json" \
    "$CURRENT_POINTER"; do
    if [[ -e "$generated_artifact" || -L "$generated_artifact" ]]; then
      if ! /bin/unlink "$generated_artifact"; then
        echo "최초 설치 실패 artifact를 제거하지 못했습니다: $generated_artifact" >&2
        return 1
      fi
    fi
  done
  if [[ "$FIRST_INSTALL_LABEL_WAS_DISABLED" == "true" ]]; then
    if ! /bin/launchctl disable "$SERVICE_TARGET"; then
      echo "최초 설치 전 LaunchAgent disabled 상태를 복구하지 못했습니다." >&2
      return 1
    fi
  else
    if ! /bin/launchctl enable "$SERVICE_TARGET"; then
      echo "최초 설치 전 LaunchAgent enabled 상태를 복구하지 못했습니다." >&2
      return 1
    fi
  fi
  for generated_artifact in \
    "$TARGET_PLIST" \
    "$TRUSTED_RUNNER_SCRIPT" \
    "$RELEASE_INTEGRITY_ANCHOR" \
    "$RUNTIME_ROOT/current-deployment.json" \
    "$CURRENT_POINTER"; do
    if [[ -e "$generated_artifact" || -L "$generated_artifact" ]]; then
      echo "최초 설치 실패 artifact가 정리 뒤에도 남아 있습니다: $generated_artifact" >&2
      return 1
    fi
  done
  if ! verify_service_fully_stopped; then
    return 1
  fi
}

fail_closed_unverified_service() {
  local failure_reason="$1"
  local stop_request_failed="false"
  echo "readiness 미증명 서비스를 fail-closed 종료합니다: $failure_reason" >&2
  if ! stop_loaded_service; then
    echo "readiness 미증명 서비스의 첫 안전 종료 요청이 실패해 bootout을 재시도합니다." >&2
    stop_request_failed="true"
  fi
  /bin/launchctl bootout "$SERVICE_TARGET" >/dev/null 2>&1 || true
  if ! verify_service_fully_stopped; then
    echo "readiness 미증명 서비스의 LaunchAgent·listener 소멸을 확정하지 못했습니다." >&2
    return 1
  fi
  UNVERIFIED_SERVICE_MAY_BE_RUNNING="false"
  if [[ "$stop_request_failed" == "true" ]]; then
    echo "첫 종료 요청은 실패했지만 재시도 뒤 완전 중지 상태를 확인했습니다." >&2
  else
    echo "readiness 미증명 서비스의 완전 중지 상태를 확인했습니다." >&2
  fi
  return 0
}

rollback_previous_release() {
  local failure_reason="$1"
  if [[ "$MAINTENANCE_STOPPED" == "true" ]]; then
    rollback_previous_release_stopped "$failure_reason"
    return $?
  fi
  echo "새 릴리스 활성화 실패를 감지해 이전 검증 릴리스로 rollback을 시도합니다: $failure_reason" >&2
  if ! stop_loaded_service; then
    echo "rollback 전 실패 서비스를 안전 종료하지 못했습니다." >&2
    return 1
  fi
  if ! verify_service_fully_stopped; then
    echo "rollback 전 LaunchAgent·listener 소멸을 확정하지 못했습니다." >&2
    return 1
  fi
  UNVERIFIED_SERVICE_MAY_BE_RUNNING="false"
  if [[ "$FIRST_INSTALL" == "true" ]]; then
    if cleanup_failed_first_install; then
      echo "최초 설치 실패를 NONE 실행 상태로 fail-closed 복구했습니다." >&2
      return 0
    fi
    echo "최초 설치 실패를 NONE 상태로 복구하지 못했습니다." >&2
    return 1
  fi
  if [[ -z "$ROLLBACK_RELEASE" || "$ROLLBACK_RELEASE" != "$PREVIOUS_RELEASE" ]]; then
    echo "검증된 rollback_release가 없거나 preflight 릴리스와 다릅니다." >&2
    return 1
  fi
  if ! PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
    PYTHONPATH="$VERIFIER_RELEASE" "$RUNTIME_VENV/bin/python" -P - \
    "$RUNTIME_ROOT" "$ROLLBACK_RELEASE" "$failure_reason" > "$ROLLBACK_RESULT" <<'PY'
import json
import sys
from pathlib import Path

from scripts.stage_macos_release import activate_release

runtime_root = Path(sys.argv[1])
rollback_release = Path(sys.argv[2])
failure_reason = sys.argv[3]
result = activate_release(
    runtime_root,
    rollback_release,
    actor="CODEX_DEPLOY_ROLLBACK",
    reason=f"INSTALL_FAILURE_{failure_reason}",
)
print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
PY
  then
    echo "v2 전체 tree 무결성 검증 또는 rollback 포인터 복구가 실패했습니다." >&2
    return 1
  fi
  if [[ -z "$VERIFIER_RELEASE" ]] || \
    ! install_trusted_runner_from_release "$VERIFIER_RELEASE"; then
    echo "rollback 검증 출처 릴리스 runner 복구가 실패했습니다." >&2
    return 1
  fi
  if ! write_release_integrity_anchor "$ROLLBACK_RELEASE" "$VERIFIER_RELEASE"; then
    echo "rollback 릴리스의 외부 무결성 anchor 복구가 실패했습니다." >&2
    return 1
  fi
  if ! write_launch_agent_plist "$TRUSTED_RUNNER_SCRIPT"; then
    echo "rollback LaunchAgent plist 복구가 실패했습니다." >&2
    return 1
  fi
  UNVERIFIED_SERVICE_MAY_BE_RUNNING="true"
  if ! bootstrap_launch_agent; then
    echo "이전 릴리스 LaunchAgent 재등록이 실패했습니다." >&2
    if ! fail_closed_unverified_service "ROLLBACK_BOOTSTRAP_FAILED"; then
      echo "rollback bootstrap 실패 뒤 fail-closed 종료도 실패했습니다." >&2
    fi
    return 1
  fi
  if ! launchctl enable "$SERVICE_TARGET" || ! launchctl kickstart "$SERVICE_TARGET"; then
    echo "이전 릴리스 LaunchAgent 시작이 실패했습니다." >&2
    if ! fail_closed_unverified_service "ROLLBACK_KICKSTART_FAILED"; then
      echo "rollback kickstart 실패 뒤 fail-closed 종료도 실패했습니다." >&2
    fi
    return 1
  fi
  local rollback_ready="false"
  local rollback_dashboard_file="$SUPPORT_DIR/latest-install-rollback-dashboard.json"
  local rollback_legacy_evidence="$SUPPORT_DIR/latest-install-rollback-legacy-evidence.json"
  for rollback_readiness_wait in {1..180}; do
    if rollback_dashboard="$(curl -fsS --max-time 2 http://127.0.0.1:8870/api/dashboard 2>/dev/null)"; then
      if ! write_text_file_atomic "$rollback_dashboard_file" "$rollback_dashboard"; then
        echo "rollback dashboard 증거를 원자적으로 저장하지 못했습니다." >&2
        if ! fail_closed_unverified_service "ROLLBACK_EVIDENCE_WRITE_FAILED"; then
          echo "rollback 증거 실패 뒤 fail-closed 종료도 실패했습니다." >&2
        fi
        return 1
      fi
      if dashboard_matches_install_contract "$PREVIOUS_RELEASE_COMMIT" "true" \
        "$LEGACY_RUNTIME_COMPATIBILITY" < "$rollback_dashboard_file" 2>/dev/null && \
        verify_legacy_runtime_contract_file \
          "$rollback_dashboard_file" "$rollback_legacy_evidence" "false"; then
        rollback_ready="true"
        break
      fi
    fi
    sleep 1
  done
  if [[ "$rollback_ready" != "true" ]]; then
    echo "이전 릴리스를 복구했지만 기존 Run·pause revision·flat readiness를 확인하지 못했습니다." >&2
    if ! fail_closed_unverified_service "ROLLBACK_READINESS_FAILED"; then
      echo "rollback readiness 실패 뒤 fail-closed 종료도 실패했습니다." >&2
    fi
    return 1
  fi
  UNVERIFIED_SERVICE_MAY_BE_RUNNING="false"
  echo "이전 v2 릴리스와 기존 PAPER Run의 일시정지·flat 상태를 복구했습니다." >&2
}

if ! verify_loaded_service_unchanged_before_stop; then
  abort_before_transition "PRESTOP_CONTRACT_CHANGED" 4
fi
if ! stop_loaded_service; then
  echo "기존 서비스 종료가 완전히 확인되지 않아 어떤 릴리스도 전환하지 않았습니다." >&2
  exit 7
fi
if ! verify_service_fully_stopped; then
  echo "기존 LaunchAgent·listener 소멸을 확인하지 못해 어떤 릴리스도 전환하지 않았습니다." >&2
  exit 7
fi
if ! verify_legacy_offline_after_stop; then
  echo "writer 종료 뒤 원장 무결성·flat을 증명하지 못해 서비스를 재시작하지 않습니다." >&2
  exit 7
fi
if ! prepare_previous_release_for_rollback; then
  echo "기존 릴리스 전체 tree를 rollback 대상으로 검증하지 못해 서비스를 재시작하지 않습니다." >&2
  exit 7
fi
if ! verify_staged_source_commit_still_final; then
  echo "stage 이후 source가 변경되어 최종 commit을 전환하지 않습니다." >&2
  exit 7
fi
if [[ "$MAINTENANCE_STOPPED" == "true" ]]; then
  if ! prepare_maintenance_artifact_backup; then
    cleanup_maintenance_artifact_backup >/dev/null 2>&1 || true
    echo "구 릴리스 artifact backup을 완전히 준비하지 못해 전환하지 않습니다." >&2
    exit 7
  fi
  if ! verify_stopped_process_absence_exact "maintenance-immediate-preactivation"; then
    cleanup_maintenance_artifact_backup >/dev/null 2>&1 || true
    echo "활성화 직전 LaunchAgent·listener·ledger holder 부재를 확정하지 못해 전환하지 않습니다." >&2
    exit 7
  fi
  MAINTENANCE_TRANSITION_STARTED="true"
fi
if ! activate_staged_release; then
  if rollback_previous_release "POSTSTOP_ACTIVATION_FAILED"; then
    exit 5
  fi
  exit 7
fi
PROJECT_DIR="$RUNTIME_ROOT/current"
if ! install_transition_artifacts; then
  if rollback_previous_release "TRANSITION_ARTIFACT_INSTALL_FAILED"; then
    exit 5
  fi
  exit 7
fi
if [[ "$MAINTENANCE_STOPPED" == "true" ]] && \
  ! verify_stopped_process_absence_exact "maintenance-immediate-prebootstrap"; then
  if rollback_previous_release "PREBOOTSTRAP_ABSENCE_FAILED"; then
    exit 5
  fi
  exit 7
fi
UNVERIFIED_SERVICE_MAY_BE_RUNNING="true"
if [[ "$MAINTENANCE_STOPPED" == "true" ]]; then
  if ! launchctl_command enable "$SERVICE_TARGET" || ! bootstrap_launch_agent_once; then
    if rollback_previous_release "SINGLE_BOOTSTRAP_FAILED"; then
      exit 5
    fi
    exit 7
  fi
else
  if ! bootstrap_launch_agent; then
    if rollback_previous_release "BOOTSTRAP_FAILED"; then
      exit 5
    fi
    exit 7
  fi
  if ! launchctl enable "$SERVICE_TARGET" || ! launchctl kickstart "$SERVICE_TARGET"; then
    if rollback_previous_release "KICKSTART_FAILED"; then
      exit 5
    fi
    exit 7
  fi
fi

PRESERVE_EXISTING_IDENTITY="true"
if [[ "$FIRST_INSTALL" == "true" ]]; then
  PRESERVE_EXISTING_IDENTITY="false"
fi
service_ready="false"
for readiness_wait in {1..180}; do
  if dashboard_payload="$(curl -fsS --max-time 2 http://127.0.0.1:8870/api/dashboard 2>/dev/null)"; then
    if printf '%s' "$dashboard_payload" | \
      dashboard_matches_install_contract "$EXPECTED_RELEASE_COMMIT" \
        "$PRESERVE_EXISTING_IDENTITY" "false" 2>/dev/null; then
      service_ready="true"
      break
    fi
  fi
  sleep 1
done
if [[ "$service_ready" != "true" ]]; then
  echo "PAPER 서비스가 180초 안에 동일 Run·pause revision·flat LIVE 준비 상태가 되지 않았습니다." >&2
  echo "로그를 확인하세요: $LOG_DIR" >&2
  if rollback_previous_release "READINESS_FAILED"; then
    exit 6
  fi
  exit 7
fi
if [[ "$MAINTENANCE_STOPPED" == "true" ]] && \
  ! verify_maintenance_postflight_stable; then
  if rollback_previous_release "POSTFLIGHT_STABILITY_FAILED"; then
    exit 6
  fi
  exit 7
fi
UNVERIFIED_SERVICE_MAY_BE_RUNNING="false"
if [[ "$MAINTENANCE_STOPPED" == "true" ]]; then
  MAINTENANCE_TRANSITION_STARTED="false"
  if ! cleanup_maintenance_artifact_backup; then
    echo "경고: 새 릴리스는 검증되었지만 임시 유지보수 artifact backup 정리가 실패했습니다." >&2
  fi
fi

PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYTHONPATH="$VERIFIER_RELEASE" \
  "$RUNTIME_VENV/bin/python" -P "$VERIFIER_RELEASE/scripts/stage_macos_release.py" \
  --runtime-root "$RUNTIME_ROOT" \
  --prune-only > "$SUPPORT_DIR/latest-release-prune.json"
PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
  "$RUNTIME_VENV/bin/python" -P -c \
  'import json,sys
payload=json.loads(open(sys.argv[1], encoding="utf-8").read())
if payload.get("status") != "PASS":
    raise RuntimeError("release prune 결과가 PASS가 아닙니다.")' \
  "$SUPPORT_DIR/latest-release-prune.json"

echo "PASS: 자동 실행 서비스 설치 및 안전한 LIVE 준비 완료"
echo "주소: http://127.0.0.1:8870/"
echo "로그: $LOG_DIR"
echo "릴리스: $(cd "$PROJECT_DIR" && pwd -P)"
