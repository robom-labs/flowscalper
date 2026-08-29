# 해결된 결함 목록이 실제 회귀검사와 계속 연결되는지 검증한다.

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.verify_regression_contracts import validate_regression_contracts

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_repository_regression_contracts_keep_every_required_anchor() -> None:
    report = validate_regression_contracts(
        PROJECT_ROOT / "config" / "regression_contracts.json",
        project_root=PROJECT_ROOT,
    )

    assert report["status"] == "PASS"
    assert report["contract_count"] >= 10
    assert report["minimum_unique_opportunities_before_ranking"] == 30
    assert report["maximum_survivor_watchlist_size"] == 10
    assert report["unproven_candidates_must_not_fill_watchlist"] is True
    assert report["watchlist_selection_does_not_promote"] is True
    assert report["profitability_status_without_all_gates"] == "NOT_PROVEN"
    assert report["real_orders_enabled"] is False


def test_regression_contracts_fail_when_a_required_anchor_disappears(tmp_path: Path) -> None:
    manifest = json.loads(
        (PROJECT_ROOT / "config" / "regression_contracts.json").read_text(encoding="utf-8")
    )
    manifest["contracts"][0]["test_anchors"][0]["contains"][0] = (
        "test_removed_regression_anchor"
    )
    changed = tmp_path / "regression-contracts.json"
    changed.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="회귀 anchor가 사라졌습니다"):
        validate_regression_contracts(changed, project_root=PROJECT_ROOT)


def test_regression_contracts_fail_when_ranking_sample_is_weakened(tmp_path: Path) -> None:
    manifest = json.loads(
        (PROJECT_ROOT / "config" / "regression_contracts.json").read_text(encoding="utf-8")
    )
    manifest["policy"]["minimum_unique_opportunities_before_ranking"] = 1
    changed = tmp_path / "regression-contracts.json"
    changed.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="누적 회귀정책이 바뀌었습니다"):
        validate_regression_contracts(changed, project_root=PROJECT_ROOT)
