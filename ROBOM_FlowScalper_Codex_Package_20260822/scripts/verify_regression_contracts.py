# 해결된 결함과 필수 회귀검사의 연결이 업그레이드 중 사라지지 않았는지 검증한다.
"""누적 결함 회귀계약의 구조와 실제 test anchor를 fail-closed로 확인한다."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

_CONTRACT_ID = re.compile(r"^[A-Z][A-Z0-9_]+$")
_ALLOWED_ANCHOR_PREFIXES = (
    "backend/tests/",
    "frontend/tests/",
    "frontend/e2e/",
    "scripts/",
    "Makefile",
)
_REQUIRED_COMMANDS = {
    "make regression-contracts",
    "make test",
    "make lint",
    "make typecheck",
    "make build",
    "make e2e",
    "make security-scan",
    "make repo-hygiene",
}


def _object(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label}는 JSON object여야 합니다.")
    return value


def _nonempty_string(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}는 비어 있지 않은 문자열이어야 합니다.")
    return value.strip()


def _safe_project_file(project_root: Path, relative_path: str) -> Path:
    if not relative_path.startswith(_ALLOWED_ANCHOR_PREFIXES):
        raise ValueError(f"허용되지 않은 회귀 anchor 경로입니다: {relative_path}")
    candidate = (project_root / relative_path).resolve()
    try:
        candidate.relative_to(project_root)
    except ValueError as caught:
        raise ValueError(f"프로젝트 밖 회귀 anchor 경로입니다: {relative_path}") from caught
    if not candidate.is_file():
        raise ValueError(f"회귀 anchor 파일이 없습니다: {relative_path}")
    return candidate


def validate_regression_contracts(
    manifest_path: Path,
    *,
    project_root: Path,
) -> dict[str, object]:
    """계약 ID, 보존정책, 실행명령과 test anchor를 결정적으로 검증한다."""

    root = project_root.resolve(strict=True)
    payload = _object(json.loads(manifest_path.read_text(encoding="utf-8")), label="manifest")
    if payload.get("schema_version") != 1:
        raise ValueError("지원하지 않는 회귀계약 schema_version입니다.")

    policy = _object(payload.get("policy"), label="policy")
    required_policy = {
        "resolved_defects_are_append_only": True,
        "minimum_unique_opportunities_before_ranking": 30,
        "maximum_survivor_watchlist_size": 10,
        "unproven_candidates_must_not_fill_watchlist": True,
        "watchlist_selection_does_not_promote": True,
        "historical_trade_and_decision_records_must_be_preserved": True,
        "frozen_research_cuts_must_be_retention_pinned": True,
        "parameter_variants_must_not_mutate_frozen_trials": True,
        "heavy_research_must_yield_to_live_runtime": True,
        "replacement_must_remove_obsolete_code_copy_css_and_tests": True,
        "profitability_status_without_all_gates": "NOT_PROVEN",
    }
    mismatched_policy = {
        key: {"expected": expected, "actual": policy.get(key)}
        for key, expected in required_policy.items()
        if policy.get(key) != expected
    }
    if mismatched_policy:
        raise ValueError(f"누적 회귀정책이 바뀌었습니다: {mismatched_policy}")

    commands = payload.get("required_commands")
    if not isinstance(commands, list) or not all(isinstance(row, str) for row in commands):
        raise ValueError("required_commands는 문자열 목록이어야 합니다.")
    missing_commands = sorted(_REQUIRED_COMMANDS.difference(commands))
    if missing_commands:
        raise ValueError(f"필수 회귀 실행명령이 빠졌습니다: {missing_commands}")

    contracts = payload.get("contracts")
    if not isinstance(contracts, list) or not contracts:
        raise ValueError("회귀계약은 한 건 이상이어야 합니다.")

    contract_ids: set[str] = set()
    anchor_paths: set[str] = set()
    anchor_count = 0
    token_count = 0
    for index, raw_contract in enumerate(contracts):
        contract = _object(raw_contract, label=f"contracts[{index}]")
        contract_id = _nonempty_string(
            contract.get("contract_id"), label=f"contracts[{index}].contract_id"
        )
        if not _CONTRACT_ID.fullmatch(contract_id):
            raise ValueError(f"회귀계약 ID 형식이 잘못됐습니다: {contract_id}")
        if contract_id in contract_ids:
            raise ValueError(f"중복 회귀계약 ID입니다: {contract_id}")
        contract_ids.add(contract_id)
        _nonempty_string(contract.get("symptom_ko"), label=f"{contract_id}.symptom_ko")
        _nonempty_string(contract.get("fixed_by"), label=f"{contract_id}.fixed_by")

        anchors = contract.get("test_anchors")
        if not isinstance(anchors, list) or not anchors:
            raise ValueError(f"{contract_id}에 test anchor가 없습니다.")
        executable_test_anchor = False
        for anchor_index, raw_anchor in enumerate(anchors):
            anchor = _object(raw_anchor, label=f"{contract_id}.test_anchors[{anchor_index}]")
            relative_path = _nonempty_string(
                anchor.get("path"), label=f"{contract_id}.test_anchors[{anchor_index}].path"
            )
            source_path = _safe_project_file(root, relative_path)
            source = source_path.read_text(encoding="utf-8")
            tokens = anchor.get("contains")
            if not isinstance(tokens, list) or not tokens:
                raise ValueError(f"{contract_id}의 {relative_path} anchor token이 없습니다.")
            for raw_token in tokens:
                token = _nonempty_string(raw_token, label=f"{contract_id}.{relative_path}.contains")
                if token not in source:
                    raise ValueError(
                        f"해결된 결함의 회귀 anchor가 사라졌습니다: "
                        f"{contract_id} -> {relative_path} -> {token}"
                    )
                token_count += 1
            if relative_path.startswith(("backend/tests/", "frontend/tests/", "frontend/e2e/")):
                executable_test_anchor = True
            anchor_paths.add(relative_path)
            anchor_count += 1
        if not executable_test_anchor:
            raise ValueError(f"{contract_id}에 실행 가능한 test anchor가 없습니다.")

    return {
        "schema": "flowscalper.regression_contracts_verification.v1",
        "status": "PASS",
        "contract_count": len(contract_ids),
        "anchor_count": anchor_count,
        "anchor_file_count": len(anchor_paths),
        "anchor_token_count": token_count,
        "minimum_unique_opportunities_before_ranking": 30,
        "maximum_survivor_watchlist_size": 10,
        "unproven_candidates_must_not_fill_watchlist": True,
        "watchlist_selection_does_not_promote": True,
        "profitability_status_without_all_gates": "NOT_PROVEN",
        "paper_only": True,
        "real_orders_enabled": False,
    }


def parse_arguments() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="해결된 결함과 회귀검사의 누적 연결을 검증합니다."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=project_root / "config" / "regression_contracts.json",
    )
    parser.add_argument("--project-root", type=Path, default=project_root)
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    report = validate_regression_contracts(
        arguments.manifest.resolve(strict=True),
        project_root=arguments.project_root,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
