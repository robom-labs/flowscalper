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
RUNTIME_VENV="$SUPPORT_DIR/runtime-venv"
PYTHON_BASE="$SUPPORT_DIR/python-base"
SERVICE_LOG="$LOG_DIR/service.log"
ERROR_LOG="$LOG_DIR/service-error.log"
MARKET_ARCHIVE_PATH="${ROBOM_MARKET_ARCHIVE_PATH:-$SOURCE_PROJECT_DIR/data/market-parquet-v6}"
CURRENT_POINTER="$RUNTIME_ROOT/current"
PREFLIGHT_DASHBOARD="$SUPPORT_DIR/latest-install-preflight.json"
PREFLIGHT_IDENTITY="$SUPPORT_DIR/latest-install-preflight-identity.json"
ROLLBACK_RESULT="$SUPPORT_DIR/latest-install-rollback.json"

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

mkdir -p "$LAUNCH_AGENT_DIR" "$SUPPORT_DIR" "$LOG_DIR" "$CACHE_DIR"
SOURCE_PYTHON="$SOURCE_PROJECT_DIR/.venv/bin/python"
SOURCE_PYTHON_BASE="$($SOURCE_PYTHON -c 'import sys; print(sys.base_prefix)')"
PYTHON_BINARY="$($SOURCE_PYTHON -c 'import sys; print(f"python{sys.version_info.major}.{sys.version_info.minor}")')"
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
"$SOURCE_PYTHON" -c \
  'import pathlib,sys; path=pathlib.Path(sys.argv[1]); rows=path.read_text().splitlines(); path.write_text("\n".join((f"home = {sys.argv[2]}" if row.startswith("home = ") else row) for row in rows) + "\n")' \
  "$RUNTIME_VENV/pyvenv.cfg" "$PYTHON_BASE/bin"
"$RUNTIME_VENV/bin/python" -c "import duckdb, fastapi, uvicorn"

HAD_CURRENT="false"
HAD_JOB="false"
PREVIOUS_RELEASE=""
PREVIOUS_RELEASE_COMMIT=""
EXPECTED_RUN_ID=""
EXPECTED_PAUSE_STATE=""
EXPECTED_PAUSE_REVISION=""
LEGACY_RUNTIME_SAFETY_FIELDS_MISSING="false"
if [[ -L "$CURRENT_POINTER" ]]; then
  HAD_CURRENT="true"
  PREVIOUS_RELEASE="$(cd "$CURRENT_POINTER" && pwd -P)"
fi
if launchctl print "$SERVICE_TARGET" >/dev/null 2>&1; then
  HAD_JOB="true"
fi
FIRST_INSTALL="false"
if [[ "$HAD_CURRENT" == "false" && "$HAD_JOB" == "false" ]]; then
  FIRST_INSTALL="true"
else
  if [[ "$HAD_CURRENT" != "true" || "$HAD_JOB" != "true" ]]; then
    echo "current 포인터와 LaunchAgent가 모두 있지 않아 최초 설치로 간주할 수 없습니다." >&2
    exit 4
  fi
  if ! curl -fsS --max-time 3 http://127.0.0.1:8870/api/dashboard > "$PREFLIGHT_DASHBOARD"; then
    echo "기존 PAPER 서비스의 dashboard preflight를 저장하지 못했습니다." >&2
    exit 4
  fi
  if ! PYTHONPATH="$SOURCE_PROJECT_DIR" "$RUNTIME_VENV/bin/python" - \
    "$PREFLIGHT_DASHBOARD" "$PREFLIGHT_IDENTITY" "$RUNTIME_ROOT" \
    "$PREVIOUS_RELEASE" <<'PY'
import json
import sys
from pathlib import Path

from scripts.stage_macos_release import (
    _read_release_manifest,
    _verify_release_tree,
    legacy_runtime_safety_fields_missing,
    migrate_legacy_release_manifest,
)

dashboard_path, identity_path, runtime_root, previous_release = map(Path, sys.argv[1:5])
payload = json.loads(dashboard_path.read_text(encoding="utf-8"))
status = payload["status"]
system = payload["system"]
operation = payload["operation_status"]
intent = payload["paper_entry_intent"]
original_manifest = _read_release_manifest(previous_release)
legacy_missing_fields = legacy_runtime_safety_fields_missing(original_manifest, system)
assert system["release_commit"] == original_manifest.get("commit")
assert system["release_isolated"] is True
assert status["market_data_state"] == "LIVE"
assert status["execution_state"] == "PAPER"
assert status["real_orders_enabled"] is False
assert status["auth_required"] is False
assert payload["paused"] is True
assert operation["state"] == "MANUALLY_PAUSED"
assert operation["market_observation_active"] is True
assert operation["paper_entry_active"] is False
assert operation["automatic_recovery"] is False
assert intent["state"] == "USER_PAUSED"
assert intent["manual_pause_requested"] is True
assert payload.get("position") is None
assert payload.get("focus_positions") == []
assert payload.get("league_positions") == []
run_id = status.get("run_id")
revision = intent.get("revision")
assert isinstance(run_id, str) and run_id and "\t" not in run_id
assert type(revision) is int and revision >= 0
manifest = migrate_legacy_release_manifest(runtime_root, previous_release)
assert _verify_release_tree(previous_release) == manifest
assert manifest["schema_version"] == 2
assert system["release_commit"] == manifest["commit"]
assert system["release_isolated"] is True
identity_path.write_text(
    json.dumps(
        {
            "run_id": run_id,
            "pause_state": intent["state"],
            "pause_revision": revision,
            "release_commit": manifest["commit"],
            "legacy_runtime_safety_fields_missing": bool(legacy_missing_fields),
            "legacy_runtime_safety_missing_fields": list(legacy_missing_fields),
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
    PREVIOUS_RELEASE_COMMIT LEGACY_RUNTIME_SAFETY_FIELDS_MISSING <<< \
    "$("$RUNTIME_VENV/bin/python" -c \
      'import json,sys; p=json.load(open(sys.argv[1], encoding="utf-8")); print(p["run_id"], p["pause_state"], p["pause_revision"], p["release_commit"], str(p["legacy_runtime_safety_fields_missing"]).lower(), sep="\t")' \
      "$PREFLIGHT_IDENTITY")"
fi

restore_previous_release_before_restart() {
  local failure_reason="$1"
  if [[ "$FIRST_INSTALL" == "true" ]]; then
    echo "최초 설치는 복구할 이전 릴리스가 없습니다: $failure_reason" >&2
    return 1
  fi
  if ! PYTHONPATH="$SOURCE_PROJECT_DIR" "$RUNTIME_VENV/bin/python" - \
    "$RUNTIME_ROOT" "$PREVIOUS_RELEASE" "$failure_reason" \
    > "$SUPPORT_DIR/latest-install-prestart-rollback.json" <<'PY'
import json
import sys
from pathlib import Path

from scripts.stage_macos_release import activate_release, current_release

runtime_root = Path(sys.argv[1])
previous_release = Path(sys.argv[2])
result = activate_release(
    runtime_root,
    previous_release,
    actor="CODEX_DEPLOY_PRESTART_ROLLBACK",
    reason=f"INSTALL_PRESTART_FAILURE_{sys.argv[3]}",
)
assert current_release(runtime_root) == previous_release.resolve(strict=True)
print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
PY
  then
    echo "서비스 재시작 전 이전 v2 포인터 복구가 실패했습니다: $failure_reason" >&2
    return 1
  fi
  if ! launchctl print "$SERVICE_TARGET" >/dev/null 2>&1; then
    echo "이전 포인터는 복구했지만 기존 LaunchAgent job이 없습니다." >&2
    return 1
  fi
  local previous_service_ready="false"
  for previous_readiness_wait in {1..30}; do
    if previous_dashboard="$(curl -fsS --max-time 2 http://127.0.0.1:8870/api/dashboard 2>/dev/null)"; then
      if printf '%s' "$previous_dashboard" | "$RUNTIME_VENV/bin/python" -c \
        'import json,sys
payload=json.load(sys.stdin)
expected_commit, expected_run, expected_state, expected_revision, allow_missing=sys.argv[1:]
status=payload["status"]
system=payload["system"]
operation=payload["operation_status"]
intent=payload["paper_entry_intent"]
assert system["release_commit"] == expected_commit
assert status["run_id"] == expected_run
assert status["market_data_state"] == "LIVE"
assert status["execution_state"] == "PAPER"
assert status["real_orders_enabled"] is False
assert status["auth_required"] is False
assert system["auth_headers"] is False
for field in ("private_api_enabled", "api_key_enabled", "wallet_enabled", "runtime_ai_order_decision_enabled"):
    if field not in system:
        assert allow_missing == "true"
    else:
        assert system[field] is False
assert payload["paused"] is True
assert operation["state"] == "MANUALLY_PAUSED"
assert operation["market_observation_active"] is True
assert operation["paper_entry_active"] is False
assert intent["state"] == expected_state
assert intent["manual_pause_requested"] is True
assert intent["revision"] == int(expected_revision)
assert payload.get("position") is None
assert payload.get("focus_positions") == []
assert payload.get("league_positions") == []' \
        "$PREVIOUS_RELEASE_COMMIT" "$EXPECTED_RUN_ID" "$EXPECTED_PAUSE_STATE" \
        "$EXPECTED_PAUSE_REVISION" "$LEGACY_RUNTIME_SAFETY_FIELDS_MISSING" \
        2>/dev/null; then
        previous_service_ready="true"
        break
      fi
    fi
    sleep 1
  done
  if [[ "$previous_service_ready" != "true" ]]; then
    echo "이전 포인터를 복구했지만 기존 Run·pause revision·flat 서비스를 확인하지 못했습니다." >&2
    return 1
  fi
  echo "서비스 재시작 전 실패를 감지해 이전 v2 포인터와 기존 PAPER 서비스를 확인했습니다." >&2
}

abort_activated_install() {
  local failure_reason="$1"
  local original_exit="$2"
  if restore_previous_release_before_restart "$failure_reason"; then
    exit "$original_exit"
  fi
  exit 7
}

STAGE_RESULT="$SUPPORT_DIR/latest-release-stage.json"
if ! "$SOURCE_PROJECT_DIR/.venv/bin/python" "$SOURCE_PROJECT_DIR/scripts/stage_macos_release.py" \
  --source-root "$SOURCE_PROJECT_DIR" \
  --runtime-root "$RUNTIME_ROOT" \
  --market-archive "$MARKET_ARCHIVE_PATH" \
  --active-ledger-dir "$RUNTIME_ROOT/active-ledger" \
  --activate > "$STAGE_RESULT"; then
  abort_activated_install "STAGE_ACTIVATION_FAILED" 4
fi
if ! "$RUNTIME_VENV/bin/python" -c \
  'import json,sys; payload=json.loads(open(sys.argv[1], encoding="utf-8").read()); assert payload["status"] == "ACTIVATED"' \
  "$STAGE_RESULT"; then
  abort_activated_install "STAGE_RESULT_INVALID" 4
fi
if ! ROLLBACK_RELEASE="$("$RUNTIME_VENV/bin/python" -c \
  'import json,sys; value=json.load(open(sys.argv[1], encoding="utf-8"))["deployment"].get("rollback_release"); print(value or "")' \
  "$STAGE_RESULT")"; then
  abort_activated_install "ROLLBACK_RELEASE_PARSE_FAILED" 4
fi
if [[ "$FIRST_INSTALL" == "true" ]]; then
  if [[ -n "$ROLLBACK_RELEASE" ]]; then
    echo "최초 설치인데 예상하지 못한 rollback_release가 기록됐습니다." >&2
    abort_activated_install "UNEXPECTED_FIRST_INSTALL_ROLLBACK" 4
  fi
elif [[ "$ROLLBACK_RELEASE" != "$PREVIOUS_RELEASE" ]]; then
  echo "stage 결과의 rollback_release가 preflight 대상과 다릅니다." >&2
  abort_activated_install "ROLLBACK_RELEASE_MISMATCH" 4
fi
PROJECT_DIR="$RUNTIME_ROOT/current"
if [[ ! -f "$PROJECT_DIR/release-manifest.json" ]]; then
  echo "불변 릴리스 준비에 실패했습니다." >&2
  abort_activated_install "RELEASE_MANIFEST_MISSING" 1
fi
RUNNER_SCRIPT="$PROJECT_DIR/scripts/run_macos_service.sh"
if [[ ! -f "$RUNNER_SCRIPT" ]]; then
  echo "불변 PAPER 서비스 실행기가 없습니다: $RUNNER_SCRIPT" >&2
  abort_activated_install "RUNNER_MISSING" 1
fi
if ! "$SOURCE_PYTHON" - "$TEMPLATE_PLIST" "$TARGET_PLIST" "$RUNNER_SCRIPT" "$SERVICE_LOG" "$ERROR_LOG" <<'PY'
from pathlib import Path
from sys import argv
from xml.sax.saxutils import escape

source, target = map(Path, argv[1:3])
payload = source.read_text(encoding="utf-8")
for placeholder, value in zip(
    ("__RUNNER_SCRIPT__", "__SERVICE_LOG__", "__ERROR_LOG__"),
    argv[3:],
    strict=True,
):
    payload = payload.replace(placeholder, escape(value))
target.write_text(payload, encoding="utf-8")
PY
then
  abort_activated_install "PLIST_WRITE_FAILED" 1
fi
xattr -d com.apple.provenance "$TARGET_PLIST" 2>/dev/null || true
if ! chmod 600 "$TARGET_PLIST" || \
  ! chmod 755 "$(cd "$PROJECT_DIR" && pwd -P)/scripts/run_macos_service.sh"; then
  abort_activated_install "INSTALL_PERMISSION_FAILED" 1
fi
if ! plutil -lint "$TARGET_PLIST"; then
  abort_activated_install "PLIST_VALIDATION_FAILED" 1
fi

if ! EXPECTED_RELEASE_COMMIT="$("$RUNTIME_VENV/bin/python" -c \
  'import json,sys; print(json.loads(open(sys.argv[1], encoding="utf-8").read())["commit"])' \
  "$PROJECT_DIR/release-manifest.json")"; then
  abort_activated_install "RELEASE_COMMIT_PARSE_FAILED" 1
fi
if ! ACTIVATION_PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd -P)"; then
  abort_activated_install "ACTIVATION_PATH_RESOLVE_FAILED" 1
fi

if [[ "$PREPARE_ONLY" == "true" ]]; then
  if [[ "$FIRST_INSTALL" != "true" ]]; then
    if ! restore_previous_release_before_restart "PREPARE_ONLY_KEEP_LOADED_RELEASE_CURRENT"; then
      echo "prepare-only 중 실행 서비스의 v2 current 포인터와 readiness를 유지하지 못했습니다." >&2
      exit 7
    fi
  fi
  echo "PASS: 불변 PAPER 릴리스와 LaunchAgent 준비 완료 · 실행 서비스 유지"
  echo "현재 서비스는 원장 유지관리 또는 명시적 설치 전까지 재시작하지 않았습니다."
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

dashboard_matches_install_contract() {
  local expected_commit="$1"
  local preserve_identity="$2"
  local allow_legacy_missing="$3"
  "$RUNTIME_VENV/bin/python" -c \
    'import json,sys
payload=json.load(sys.stdin)
expected_commit, preserve_identity, allow_legacy_missing, expected_run, expected_pause_state, expected_revision=sys.argv[1:]
status=payload["status"]
system=payload["system"]
operation=payload["operation_status"]
intent=payload["paper_entry_intent"]
assert system["release_commit"] == expected_commit
assert system["release_isolated"] is True
assert status["market_data_state"] == "LIVE"
assert status["execution_state"] == "PAPER"
assert status["real_orders_enabled"] is False
assert status["auth_required"] is False
assert system["auth_headers"] is False
for field in ("private_api_enabled", "api_key_enabled", "wallet_enabled", "runtime_ai_order_decision_enabled"):
    if field not in system:
        assert allow_legacy_missing == "true"
    else:
        assert system[field] is False
assert operation["market_observation_active"] is True
assert payload.get("position") is None
assert payload.get("focus_positions") == []
assert payload.get("league_positions") == []
if preserve_identity == "true":
    assert status["run_id"] == expected_run
    assert payload["paused"] is True
    assert operation["state"] == "MANUALLY_PAUSED"
    assert operation["paper_entry_active"] is False
    assert operation["automatic_recovery"] is False
    assert intent["state"] == expected_pause_state
    assert intent["manual_pause_requested"] is True
    assert intent["revision"] == int(expected_revision)
else:
    assert operation["automatic_recovery"] is True
assert float(system["lag_p95_ms"]) <= 500.0
assert float(system["trade_lag_p95_ms"]) <= 1000.0
assert system["persistence_worker_warmed"] is True
assert int(system["persistence_flush_count"]) >= 4
assert float(system["persistence_flush_last_ms"]) <= 20000.0
assert int(system["persistence_fault_count"]) == 0
assert int(system["persistence_buffer_dropped"]) == 0
assert system["storage_entry_allowed"] is True' \
    "$expected_commit" "$preserve_identity" "$allow_legacy_missing" "$EXPECTED_RUN_ID" \
    "$EXPECTED_PAUSE_STATE" "$EXPECTED_PAUSE_REVISION"
}

rollback_previous_release() {
  local failure_reason="$1"
  echo "새 릴리스 활성화 실패를 감지해 이전 검증 릴리스로 rollback을 시도합니다: $failure_reason" >&2
  if ! stop_loaded_service; then
    echo "rollback 전 실패 서비스를 안전 종료하지 못했습니다." >&2
    return 1
  fi
  if [[ "$FIRST_INSTALL" == "true" ]]; then
    echo "최초 설치는 이전 릴리스가 없어 자동 rollback을 수행할 수 없습니다." >&2
    return 1
  fi
  if [[ -z "$ROLLBACK_RELEASE" || "$ROLLBACK_RELEASE" != "$PREVIOUS_RELEASE" ]]; then
    echo "검증된 rollback_release가 없거나 preflight 릴리스와 다릅니다." >&2
    return 1
  fi
  if ! PYTHONPATH="$ACTIVATION_PROJECT_DIR" "$RUNTIME_VENV/bin/python" - \
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
  if ! bootstrap_launch_agent; then
    echo "이전 릴리스 LaunchAgent 재등록이 실패했습니다." >&2
    return 1
  fi
  if ! launchctl enable "$SERVICE_TARGET" || ! launchctl kickstart "$SERVICE_TARGET"; then
    echo "이전 릴리스 LaunchAgent 시작이 실패했습니다." >&2
    return 1
  fi
  local rollback_ready="false"
  for rollback_readiness_wait in {1..180}; do
    if rollback_dashboard="$(curl -fsS --max-time 2 http://127.0.0.1:8870/api/dashboard 2>/dev/null)"; then
      if printf '%s' "$rollback_dashboard" | \
        dashboard_matches_install_contract "$PREVIOUS_RELEASE_COMMIT" "true" \
          "$LEGACY_RUNTIME_SAFETY_FIELDS_MISSING" 2>/dev/null; then
        rollback_ready="true"
        break
      fi
    fi
    sleep 1
  done
  if [[ "$rollback_ready" != "true" ]]; then
    echo "이전 릴리스를 복구했지만 기존 Run·pause revision·flat readiness를 확인하지 못했습니다." >&2
    return 1
  fi
  echo "이전 v2 릴리스와 기존 PAPER Run의 일시정지·flat 상태를 복구했습니다." >&2
}

if ! stop_loaded_service; then
  if restore_previous_release_before_restart "SHUTDOWN_FAILED"; then
    exit 5
  fi
  exit 7
fi
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

"$SOURCE_PROJECT_DIR/.venv/bin/python" "$SOURCE_PROJECT_DIR/scripts/stage_macos_release.py" \
  --runtime-root "$RUNTIME_ROOT" \
  --prune-only > "$SUPPORT_DIR/latest-release-prune.json"
"$RUNTIME_VENV/bin/python" -c \
  'import json,sys; payload=json.loads(open(sys.argv[1], encoding="utf-8").read()); assert payload["status"] == "PASS"' \
  "$SUPPORT_DIR/latest-release-prune.json"

echo "PASS: 자동 실행 서비스 설치 및 안전한 LIVE 준비 완료"
echo "주소: http://127.0.0.1:8870/"
echo "로그: $LOG_DIR"
echo "릴리스: $(cd "$PROJECT_DIR" && pwd -P)"
