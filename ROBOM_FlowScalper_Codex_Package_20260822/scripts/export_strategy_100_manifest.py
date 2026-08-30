# PAPER 전용 20×5 연구후보 사전등록을 기계판독 JSON으로 내보낸다.

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from backend.app.build_identity import git_commit
from backend.app.research import trial_manifest

DEFAULT_OUTPUT = Path("evidence/STRATEGY_100_TRIAL_MANIFEST.json")
BOUND_SOURCE_FILES = (
    Path("backend/app/research/candidate_registry.py"),
    Path("backend/app/research/alpha_evaluators.py"),
    Path("backend/app/research/alpha_features.py"),
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
    Path("backend/app/research/screening.py"),
    Path("backend/app/research/secondary_reports.py"),
    Path("backend/app/research/execution.py"),
    Path("backend/app/research/instrument_metadata.py"),
    Path("backend/app/research/strategy100_dataset_v2.py"),
    Path("backend/app/research/strategy100_warmup.py"),
    Path("scripts/export_strategy_100_manifest.py"),
    Path("scripts/freeze_strategy_100_dataset_v2.py"),
    Path("scripts/freeze_strategy_100_warmup.py"),
    Path("scripts/research_intraday_candidates.py"),
    Path("scripts/research_strategy_100_candidates.py"),
    Path("scripts/benchmark_strategy_100_candidates.py"),
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    source_checksums = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in BOUND_SOURCE_FILES
    }
    checksum_material = json.dumps(
        source_checksums,
        sort_keys=True,
        separators=(",", ":"),
    )
    source_bundle_hash = hashlib.sha256(checksum_material.encode()).hexdigest()
    code_version = f"{git_commit()}+research-bundle-sha256:{source_bundle_hash}"
    report = trial_manifest(
        code_version=code_version,
        generated_ts_utc=datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        source_checksums=source_checksums,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "trial_count": report["trial_count"],
                "screening_eligible_count": report["screening_eligible_count"],
                "blocked_count": report["blocked_count"],
                "manifest_sha256": report["manifest_sha256"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
