#!/bin/zsh
# macOS 로그인 세션에서 외장 저장소의 localhost 서버를 고정 포트로 계속 실행한다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
EXTERNAL_LAUNCHER="false"
if [[ "$SCRIPT_DIR" == */support && -L "$SCRIPT_DIR/../current" ]]; then
  EXTERNAL_LAUNCHER="true"
  if [[ -n "${ROBOM_RUNTIME_ROOT:-}" || -n "${ROBOM_RUNTIME_PYTHON:-}" || \
    -n "${ROBOM_RELEASE_INTEGRITY_ANCHOR:-}" || \
    -n "${ROBOM_ACTIVE_LEDGER_DIR:-}" || -n "${ROBOM_DB_PATH:-}" || \
    -n "${ROBOM_MARKET_ARCHIVE_PATH:-}" || -n "${ROBOM_MODE:-}" ]]; then
    echo "외부 신뢰 runner의 런타임·원장·검증 경로 override는 허용하지 않습니다." >&2
    exit 75
  fi
  RUNTIME_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
  PROJECT_DIR="$(cd "$RUNTIME_ROOT/current" && pwd -P)"
else
  PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"
fi
PROJECT_VOLUME_NAME="${PROJECT_DIR#/Volumes/}"
PROJECT_VOLUME_NAME="${PROJECT_VOLUME_NAME%%/*}"
PROJECT_MOUNT="/Volumes/$PROJECT_VOLUME_NAME"
if [[ "$EXTERNAL_LAUNCHER" != "true" && "$PROJECT_DIR" != /Volumes/*/* && \
  -z "${ROBOM_RUNTIME_ROOT:-}" ]]; then
  echo "외장 APFS 실행 릴리스를 찾지 못했습니다: $PROJECT_DIR" >&2
  exit 75
fi
RUNTIME_ROOT="${RUNTIME_ROOT:-${ROBOM_RUNTIME_ROOT:-$PROJECT_MOUNT/05_RUNTIME/ROBOM_FlowScalper}}"
SUPPORT_DIR="$RUNTIME_ROOT/support"
CACHE_DIR="$RUNTIME_ROOT/cache"
TMP_DIR="$RUNTIME_ROOT/tmp"
LOG_DIR="$RUNTIME_ROOT/logs"
if [[ "$EXTERNAL_LAUNCHER" == "true" ]]; then
  RUNTIME_PYTHON="$SUPPORT_DIR/runtime-venv/bin/python"
else
  RUNTIME_PYTHON="${ROBOM_RUNTIME_PYTHON:-$SUPPORT_DIR/runtime-venv/bin/python}"
fi
DEFAULT_ACTIVE_LEDGER_DIR="$RUNTIME_ROOT/active-ledger"
if [[ "$EXTERNAL_LAUNCHER" == "true" ]]; then
  ACTIVE_LEDGER_DIR="$DEFAULT_ACTIVE_LEDGER_DIR"
else
  ACTIVE_LEDGER_DIR="${ROBOM_ACTIVE_LEDGER_DIR:-$DEFAULT_ACTIVE_LEDGER_DIR}"
fi
RELEASE_MANIFEST="$PROJECT_DIR/release-manifest.json"
if [[ "$EXTERNAL_LAUNCHER" == "true" ]]; then
  RELEASE_INTEGRITY_ANCHOR="$SUPPORT_DIR/current-release-integrity.json"
else
  RELEASE_INTEGRITY_ANCHOR="${ROBOM_RELEASE_INTEGRITY_ANCHOR:-$SUPPORT_DIR/current-release-integrity.json}"
fi

if [[ ! -f "$PROJECT_DIR/frontend/dist/index.html" ]]; then
  echo "불변 실행 릴리스의 프론트엔드가 준비되지 않았습니다: $PROJECT_DIR" >&2
  exit 75
fi
if [[ ! -f "$RELEASE_MANIFEST" ]]; then
  echo "불변 실행 릴리스 manifest가 없습니다: $RELEASE_MANIFEST" >&2
  exit 75
fi
if [[ ! -x "$RUNTIME_PYTHON" ]]; then
  echo "외장 Python 실행환경이 없습니다: $RUNTIME_PYTHON" >&2
  exit 75
fi
if ! PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYTHONPATH="" \
  "$RUNTIME_PYTHON" -I -P - "$PROJECT_DIR" "$RELEASE_MANIFEST" \
  "$RELEASE_INTEGRITY_ANCHOR" "$SCRIPT_DIR/run_macos_service.sh" \
  "$EXTERNAL_LAUNCHER" <<'PY'
import hashlib
import json
import sys
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def strict_json_object(path: Path, label: str) -> dict[str, object]:
    def reject_constant(value: str) -> object:
        raise RuntimeError(f"{label}에 비표준 숫자가 있습니다: {value}")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            require(key not in result, f"{label}에 중복 JSON key가 있습니다: {key}")
            result[key] = value
        return result

    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
    )
    require(isinstance(value, dict), f"{label}가 JSON object가 아닙니다.")
    return value


def verify_release_tree(
    release: Path,
    *,
    expected_commit: object,
    expected_manifest_sha256: object,
    label: str,
) -> tuple[str, str, dict[str, object]]:
    require(not release.is_symlink(), f"{label} root symlink는 허용하지 않습니다.")
    resolved = release.resolve(strict=True)
    require(resolved == release.absolute(), f"{label} canonical 경로가 다릅니다.")
    require(resolved.parent == releases_root, f"{label}가 releases direct child가 아닙니다.")
    manifest_file = resolved / "release-manifest.json"
    require(
        not manifest_file.is_symlink() and manifest_file.is_file(),
        f"{label} manifest가 regular file이 아닙니다.",
    )
    manifest_digest = sha256(manifest_file)
    require(
        expected_manifest_sha256 == manifest_digest,
        f"{label} manifest SHA-256이 anchor와 다릅니다.",
    )
    manifest = strict_json_object(manifest_file, f"{label} manifest")
    require(
        type(manifest.get("schema_version")) is int
        and manifest.get("schema_version") == 2,
        f"{label} manifest가 schema v2가 아닙니다.",
    )
    commit = manifest.get("commit")
    require(
        isinstance(commit, str)
        and len(commit) == 40
        and all(character in "0123456789abcdef" for character in commit),
        f"{label} commit 형식이 올바르지 않습니다.",
    )
    require(commit == expected_commit, f"{label} commit이 anchor와 다릅니다.")
    require(
        resolved.name == commit and manifest.get("release_id") == commit,
        f"{label} 디렉터리·release_id·commit이 다릅니다.",
    )
    require(manifest.get("release_path") == str(resolved), f"{label} release_path가 다릅니다.")
    for field, expected in {
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "private_api_enabled": False,
        "wallet_paths_enabled": False,
    }.items():
        require(manifest.get(field) is expected, f"{label} PAPER 안전 불변조건이 다릅니다: {field}")
    expected_count = manifest.get("file_count")
    expected_files = manifest.get("files")
    require(
        type(expected_count) is int and expected_count >= 0,
        f"{label} file_count가 올바르지 않습니다.",
    )
    require(isinstance(expected_files, dict), f"{label} files가 JSON object가 아닙니다.")
    normalized_expected: dict[str, str] = {}
    for raw_path, raw_digest in expected_files.items():
        require(
            isinstance(raw_path, str)
            and bool(raw_path)
            and raw_path != "release-manifest.json",
            f"{label} 파일 경로가 올바르지 않습니다.",
        )
        relative = Path(raw_path)
        require(
            not relative.is_absolute()
            and ".." not in relative.parts
            and relative.as_posix() == raw_path,
            f"{label} 파일 경로가 안전하지 않습니다: {raw_path}",
        )
        require(
            isinstance(raw_digest, str)
            and len(raw_digest) == 64
            and all(character in "0123456789abcdef" for character in raw_digest),
            f"{label} SHA-256이 올바르지 않습니다: {raw_path}",
        )
        normalized_expected[raw_path] = raw_digest
    require(expected_count == len(normalized_expected), f"{label} file_count가 다릅니다.")
    actual_files: dict[str, str] = {}
    for path in sorted(resolved.rglob("*")):
        relative = path.relative_to(resolved).as_posix()
        require(not path.is_symlink(), f"{label} tree link는 허용하지 않습니다: {relative}")
        if path.is_dir():
            continue
        require(path.is_file(), f"{label} tree regular file이 아닙니다: {relative}")
        if relative != "release-manifest.json":
            actual_files[relative] = sha256(path)
    require(actual_files == normalized_expected, f"{label} v2 전체 tree SHA-256이 다릅니다.")
    require(len(actual_files) == expected_count, f"{label} tree 파일 수가 다릅니다.")
    return commit, manifest_digest, manifest


release_path_argument = Path(sys.argv[1])
manifest_path = Path(sys.argv[2])
anchor_path = Path(sys.argv[3])
launcher_path_argument = Path(sys.argv[4])
external_launcher = sys.argv[5] == "true"
require(not release_path_argument.is_symlink(), "실행 릴리스 symlink는 허용하지 않습니다.")
release_path = release_path_argument.resolve(strict=True)
runtime_root = release_path.parent.parent
releases_path = runtime_root / "releases"
require(
    runtime_root.resolve(strict=True) == runtime_root.absolute()
    and not releases_path.is_symlink(),
    "런타임 또는 releases root가 canonical regular 경로가 아닙니다.",
)
releases_root = releases_path.resolve(strict=True)
launcher_path = launcher_path_argument.resolve(strict=True)
require(
    not launcher_path_argument.is_symlink() and launcher_path_argument.is_file(),
    "외부 verifier가 regular file이 아닙니다.",
)
if external_launcher:
    require(
        launcher_path == runtime_root / "support" / "run_macos_service.sh",
        "외부 verifier가 canonical support 경로가 아닙니다.",
    )
require(
    not anchor_path.is_symlink() and anchor_path.is_file(),
    "외부 릴리스 무결성 anchor가 regular file이 아닙니다.",
)
anchor = strict_json_object(anchor_path, "외부 릴리스 무결성 anchor")
expected_anchor_fields = {
    "schema_version",
    "release_path",
    "release_commit",
    "manifest_sha256",
    "launcher_path",
    "launcher_sha256",
    "launcher_source_release_path",
    "launcher_source_commit",
    "launcher_source_manifest_sha256",
    "paper_only",
    "real_orders_enabled",
}
require(set(anchor) == expected_anchor_fields, "외부 릴리스 무결성 anchor field가 다릅니다.")
require(
    type(anchor.get("schema_version")) is int and anchor.get("schema_version") == 2,
    "외부 릴리스 무결성 anchor schema가 2가 아닙니다.",
)
require(anchor.get("release_path") == str(release_path), "anchor와 실행 릴리스 경로가 다릅니다.")
require(anchor.get("launcher_path") == str(launcher_path), "anchor와 외부 verifier 경로가 다릅니다.")
require(anchor.get("paper_only") is True, "anchor paper_only가 True가 아닙니다.")
require(anchor.get("real_orders_enabled") is False, "anchor real_orders_enabled가 False가 아닙니다.")
require(manifest_path.absolute() == release_path / "release-manifest.json", "manifest 인자 경로가 실행 릴리스와 다릅니다.")
target_commit, target_manifest_sha, target_manifest = verify_release_tree(
    release_path,
    expected_commit=anchor.get("release_commit"),
    expected_manifest_sha256=anchor.get("manifest_sha256"),
    label="실행 릴리스",
)
source_path_value = anchor.get("launcher_source_release_path")
require(isinstance(source_path_value, str) and bool(source_path_value), "launcher source 경로가 없습니다.")
source_path = Path(source_path_value)
source_commit, source_manifest_sha, _source_manifest = verify_release_tree(
    source_path,
    expected_commit=anchor.get("launcher_source_commit"),
    expected_manifest_sha256=anchor.get("launcher_source_manifest_sha256"),
    label="launcher source 릴리스",
)
source_runner = source_path / "scripts" / "run_macos_service.sh"
require(
    not source_runner.is_symlink() and source_runner.is_file(),
    "launcher source runner가 regular file이 아닙니다.",
)
launcher_sha = sha256(launcher_path)
source_runner_sha = sha256(source_runner)
require(
    anchor.get("launcher_sha256") == launcher_sha == source_runner_sha,
    "외부 verifier와 launcher source runner SHA-256이 다릅니다.",
)
active_ledger_argument = runtime_root / "active-ledger"
require(not active_ledger_argument.is_symlink(), "active ledger root symlink는 허용하지 않습니다.")
active_ledger = active_ledger_argument.resolve(strict=True)
require(
    active_ledger == active_ledger_argument.absolute()
    and active_ledger.parent == runtime_root
    and active_ledger.is_dir(),
    "active ledger가 canonical runtime direct child 디렉터리가 아닙니다.",
)
require(
    target_manifest.get("active_ledger_dir") == str(active_ledger),
    "실행 릴리스 manifest active ledger가 canonical 경로와 다릅니다.",
)
require(
    sha256(release_path / "release-manifest.json") == target_manifest_sha
    and sha256(source_path / "release-manifest.json") == source_manifest_sha
    and sha256(launcher_path) == launcher_sha
    and sha256(source_runner) == source_runner_sha,
    "릴리스 또는 verifier가 검증 중 바뀌었습니다.",
)
commit = target_commit
PY
then
  echo "실행 릴리스 v2 전체 tree SHA-256 무결성 검증에 실패했습니다: $PROJECT_DIR" >&2
  exit 75
fi
MANIFEST_VALUE='import json,sys; print(json.loads(open(sys.argv[1], encoding="utf-8").read())[sys.argv[2]])'
RELEASE_COMMIT="$(PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYTHONPATH="" \
  "$RUNTIME_PYTHON" -I -P -c "$MANIFEST_VALUE" "$RELEASE_MANIFEST" commit)"
MANIFEST_MARKET_ARCHIVE="$(PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYTHONPATH="" \
  "$RUNTIME_PYTHON" -I -P -c "$MANIFEST_VALUE" "$RELEASE_MANIFEST" market_archive_path)"
MANIFEST_ACTIVE_LEDGER="$(PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYTHONPATH="" \
  "$RUNTIME_PYTHON" -I -P -c "$MANIFEST_VALUE" "$RELEASE_MANIFEST" active_ledger_dir)"
verify_active_ledger_binding() {
  local resolved_ledger=""
  if [[ -L "$DEFAULT_ACTIVE_LEDGER_DIR" || ! -d "$DEFAULT_ACTIVE_LEDGER_DIR" ]] || \
    ! resolved_ledger="$(cd "$DEFAULT_ACTIVE_LEDGER_DIR" && pwd -P)" || \
    [[ "$resolved_ledger" != "$DEFAULT_ACTIVE_LEDGER_DIR" ]]; then
    echo "active ledger가 canonical runtime direct child 디렉터리가 아닙니다." >&2
    return 1
  fi
  local ledger_file=""
  for ledger_file in \
    "$DEFAULT_ACTIVE_LEDGER_DIR/run-ledger.sqlite3" \
    "$DEFAULT_ACTIVE_LEDGER_DIR/run-ledger.sqlite3-wal" \
    "$DEFAULT_ACTIVE_LEDGER_DIR/run-ledger.sqlite3-shm" \
    "$DEFAULT_ACTIVE_LEDGER_DIR/run-ledger.sqlite3-journal"; do
    if [[ -L "$ledger_file" || ( -e "$ledger_file" && ! -f "$ledger_file" ) ]]; then
      echo "active ledger DB·sidecar가 canonical regular file이 아닙니다: $ledger_file" >&2
      return 1
    fi
  done
}
if [[ "$EXTERNAL_LAUNCHER" == "true" ]]; then
  if [[ "$MANIFEST_ACTIVE_LEDGER" != "$DEFAULT_ACTIVE_LEDGER_DIR" ]]; then
    echo "manifest active ledger가 외부 신뢰 runner의 canonical 경로와 다릅니다." >&2
    exit 75
  fi
  if ! verify_active_ledger_binding; then
    exit 75
  fi
  ACTIVE_LEDGER_DIR="$DEFAULT_ACTIVE_LEDGER_DIR"
  MARKET_ARCHIVE_PATH="$MANIFEST_MARKET_ARCHIVE"
else
  ACTIVE_LEDGER_DIR="${ROBOM_ACTIVE_LEDGER_DIR:-$MANIFEST_ACTIVE_LEDGER}"
  MARKET_ARCHIVE_PATH="${ROBOM_MARKET_ARCHIVE_PATH:-$MANIFEST_MARKET_ARCHIVE}"
fi
[[ -d "$MARKET_ARCHIVE_PATH" ]] || { echo "공개시장 archive가 없습니다: $MARKET_ARCHIVE_PATH" >&2; exit 75; }

cd "$PROJECT_DIR"
umask 077
mkdir -p "$ACTIVE_LEDGER_DIR" "$CACHE_DIR/python" "$CACHE_DIR/xdg" "$CACHE_DIR/uv" "$TMP_DIR" "$LOG_DIR"
MAX_LOG_BYTES=10485760
for log_file in "$LOG_DIR/service.log" "$LOG_DIR/service-error.log"; do
  if [[ -L "$log_file" ]]; then
    echo "서비스 로그가 심볼릭 링크여서 시작을 거부합니다: $log_file" >&2
    exit 75
  fi
  if [[ -f "$log_file" ]] && (( $(/usr/bin/stat -f %z "$log_file") >= MAX_LOG_BYTES )); then
    /bin/cp -p "$log_file" "$log_file.previous"
    : > "$log_file"
  fi
done
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export PYTHONPYCACHEPREFIX="$CACHE_DIR/python"
export XDG_CACHE_HOME="$CACHE_DIR/xdg"
export UV_CACHE_DIR="$CACHE_DIR/uv"
export TMPDIR="$TMP_DIR/"
export PYTHONUNBUFFERED=1
export PYTHONNOUSERSITE=1
export PYTHONPATH="$PROJECT_DIR"
BACKEND_PACKAGE_ROOT="$("$RUNTIME_PYTHON" -P -c 'from pathlib import Path; import backend; print(Path(backend.__file__).resolve().parent)')"
if [[ "$BACKEND_PACKAGE_ROOT" != "$PROJECT_DIR/backend" ]]; then
  echo "backend가 불변 실행 릴리스 밖에서 로드됐습니다: $BACKEND_PACKAGE_ROOT" >&2
  exit 75
fi
export ROBOM_HOST="127.0.0.1"
export ROBOM_PORT="8870"
export ROBOM_OPEN_BROWSER="false"
export ROBOM_RELOAD="false"
export ROBOM_RELEASE_COMMIT="$RELEASE_COMMIT"
export ROBOM_RELEASE_ISOLATED="true"
if [[ "$EXTERNAL_LAUNCHER" == "true" ]]; then
  export ROBOM_DB_PATH="$ACTIVE_LEDGER_DIR/run-ledger.sqlite3"
else
  export ROBOM_DB_PATH="${ROBOM_DB_PATH:-$ACTIVE_LEDGER_DIR/run-ledger.sqlite3}"
fi
if [[ "$EXTERNAL_LAUNCHER" == "true" ]] && ! verify_active_ledger_binding; then
  exit 75
fi
"$RUNTIME_PYTHON" -P "$PROJECT_DIR/scripts/recover_oversized_wal.py" \
  --source "$ROBOM_DB_PATH" \
  --snapshot-root "$RUNTIME_ROOT/maintenance-snapshots" \
  --output "$SUPPORT_DIR/startup-ledger-recovery.json" \
  --max-wal-bytes 67108864
if [[ "$EXTERNAL_LAUNCHER" == "true" ]] && ! verify_active_ledger_binding; then
  exit 75
fi
if [[ "$EXTERNAL_LAUNCHER" == "true" ]]; then
  export ROBOM_MODE="$("$RUNTIME_PYTHON" -P scripts/select_service_mode.py "$ROBOM_DB_PATH")"
else
  export ROBOM_MODE="${ROBOM_MODE:-$("$RUNTIME_PYTHON" -P scripts/select_service_mode.py "$ROBOM_DB_PATH")}"
fi
export ROBOM_MARKET_ARCHIVE_PATH="$MARKET_ARCHIVE_PATH"
export ROBOM_MIN_FREE_BYTES="5368709120"
export ROBOM_MIN_FREE_RATIO="0.04"

if [[ "$EXTERNAL_LAUNCHER" == "true" ]] && ! verify_active_ledger_binding; then
  exit 75
fi
exec "$RUNTIME_PYTHON" -P "$PROJECT_DIR/scripts/run_server.py"
