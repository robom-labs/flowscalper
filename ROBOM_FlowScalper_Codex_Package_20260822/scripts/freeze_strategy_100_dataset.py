# 기존 공개시장 archive를 재검증해 100후보 공통 데이터셋으로 동결한다.

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.research import build_strategy_100_dataset_manifest
from scripts.research_intraday_candidates import _dataset_slice


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--archive",
        type=Path,
        default=Path("data/market-parquet-v6/venue=BINANCE_USDM"),
    )
    parser.add_argument(
        "--source-evidence",
        type=Path,
        default=Path("evidence/WAVE34_INTRADAY_RESEARCH.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evidence/STRATEGY_100_DATASET_MANIFEST.json"),
    )
    parser.add_argument(
        "--trial-manifest",
        type=Path,
        default=Path("evidence/STRATEGY_100_TRIAL_MANIFEST.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_bytes = args.source_evidence.read_bytes()
    source: dict[str, Any] = json.loads(source_bytes)
    trial_manifest_bytes = args.trial_manifest.read_bytes()
    trial_manifest: dict[str, Any] = json.loads(trial_manifest_bytes)
    source_manifest = source.get("manifest")
    if not isinstance(source_manifest, dict):
        raise ValueError("원본 연구증거 manifest가 필요합니다.")
    run_ids = source_manifest.get("run_ids")
    if not isinstance(run_ids, list) or not all(isinstance(value, str) for value in run_ids):
        raise ValueError("원본 연구증거 Run 목록이 잘못됐습니다.")
    slices = tuple(_dataset_slice(run_id, args.archive / f"run={run_id}") for run_id in run_ids)
    output = build_strategy_100_dataset_manifest(
        source,
        slices,
        source_evidence_path=args.source_evidence.as_posix(),
        source_evidence_sha256=hashlib.sha256(source_bytes).hexdigest(),
        trial_manifest=trial_manifest,
        trial_manifest_path=args.trial_manifest.as_posix(),
        trial_manifest_file_sha256=hashlib.sha256(trial_manifest_bytes).hexdigest(),
        generated_ts_utc=datetime.now(UTC).isoformat(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "output": args.output.as_posix(),
                "status": output["status"],
                "run_count": output["archive_verification"]["run_count"],
                "manifest_sha256": output["manifest_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
