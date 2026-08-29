# 비용회수형 E06 PAPER 후보 묶음을 원본 100후보와 분리해 사전등록한다.

"""비용회수형 E06 PAPER 후보 묶음을 원본 100후보와 분리해 사전등록한다."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from backend.app.build_identity import git_commit
from backend.app.research.cost_covered_exit_variants import (
    cost_covered_exit_variant_manifest,
)

DEFAULT_OUTPUT = Path("evidence/COST_COVERED_EXIT_VARIANT_MANIFEST.json")
DEFAULT_PARENT_MANIFEST = Path("evidence/STRATEGY_100_TRIAL_MANIFEST.json")
VARIANT_BOUND_SOURCE_FILES = (
    Path("backend/app/research/candidate_registry.py"),
    Path("backend/app/research/cost_covered_exit_variants.py"),
    Path("backend/app/research/alpha_evaluators.py"),
    Path("backend/app/research/alpha_features.py"),
    Path("backend/app/research/screening.py"),
    Path("backend/app/research/secondary_reports.py"),
    Path("backend/app/research/execution.py"),
    Path("backend/app/research/instrument_metadata.py"),
    Path("backend/app/market_data/timeframes.py"),
    Path("backend/app/market_data/candles.py"),
    Path("backend/app/features/engine.py"),
    Path("backend/app/regime/classifier.py"),
    Path("backend/app/costing/models.py"),
    Path("backend/app/risk/manager.py"),
    Path("backend/app/positions/manager.py"),
    Path("backend/app/execution/models.py"),
    Path("backend/app/execution/trailing.py"),
    Path("backend/app/execution/simulator.py"),
    Path("backend/app/execution/portfolio.py"),
    Path("backend/app/strategies/shadow.py"),
    Path("backend/app/candidates/plans.py"),
    Path("backend/app/research/dataset_freeze.py"),
    Path("scripts/export_cost_covered_exit_variant_manifest.py"),
    Path("scripts/research_intraday_candidates.py"),
    Path("scripts/research_strategy_100_candidates.py"),
    Path("scripts/run_live_safe_strategy_screening.py"),
)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _manifest_checksum(manifest: dict[str, object]) -> str:
    material = dict(manifest)
    material.pop("manifest_sha256", None)
    return hashlib.sha256(_canonical_json(material).encode()).hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--parent-trial-manifest",
        type=Path,
        default=DEFAULT_PARENT_MANIFEST,
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    parent_bytes = args.parent_trial_manifest.read_bytes()
    parent: dict[str, object] = json.loads(parent_bytes)
    parent_sha = _manifest_checksum(parent)
    if (
        parent.get("manifest_sha256") != parent_sha
        or parent.get("trial_count") != 100
        or parent.get("paper_only") is not True
        or parent.get("real_orders_enabled") is not False
        or parent.get("private_api_enabled") is not False
    ):
        raise ValueError("원본 100후보 manifest의 checksum 또는 PAPER 경계가 잘못됐습니다.")
    source_checksums = {
        path.as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in VARIANT_BOUND_SOURCE_FILES
    }
    source_bundle_hash = hashlib.sha256(_canonical_json(source_checksums).encode()).hexdigest()
    report = cost_covered_exit_variant_manifest(
        code_version=f"{git_commit()}+research-bundle-sha256:{source_bundle_hash}",
        generated_ts_utc=datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        source_checksums=source_checksums,
        parent_trial_manifest={
            "path": args.parent_trial_manifest.as_posix(),
            "manifest_sha256": parent_sha,
            "file_sha256": hashlib.sha256(parent_bytes).hexdigest(),
        },
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": args.output.as_posix(),
                "batch_id": report["batch_id"],
                "trial_count": report["trial_count"],
                "manifest_sha256": report["manifest_sha256"],
                "paper_only": report["paper_only"],
                "real_orders_enabled": report["real_orders_enabled"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
