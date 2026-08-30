# 장기 LIVE_PUBLIC cut과 공개봉 워밍업을 100후보 V2 시간순 데이터셋으로 동결한다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.research.strategy100_dataset_v2 import (
    build_strategy_100_dataset_v2_manifest,
)


def _read_manifest(path: Path) -> tuple[dict[str, Any], bytes]:
    payload = path.read_bytes()
    return json.loads(payload), payload


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") == rendered:
            return
        raise FileExistsError(f"기존 100후보 V2 데이터셋을 덮어쓰지 않습니다: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(rendered, encoding="utf-8")
    os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trial-manifest",
        type=Path,
        default=Path("evidence/STRATEGY_100_TRIAL_MANIFEST_V2.json"),
    )
    parser.add_argument("--live-public-cut", type=Path, required=True)
    parser.add_argument(
        "--warmup-manifest",
        type=Path,
        default=Path("evidence/STRATEGY_100_WARMUP_MANIFEST_V2.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evidence/STRATEGY_100_DATASET_MANIFEST_V2.json"),
    )
    parser.add_argument("--train-hours", type=int, default=20)
    parser.add_argument("--validation-hours-each", type=int, default=40)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    trial, trial_bytes = _read_manifest(args.trial_manifest)
    cut, cut_bytes = _read_manifest(args.live_public_cut)
    warmup, warmup_bytes = _read_manifest(args.warmup_manifest)
    manifest = build_strategy_100_dataset_v2_manifest(
        trial_manifest=trial,
        trial_manifest_path=args.trial_manifest.as_posix(),
        trial_manifest_file_sha256=hashlib.sha256(trial_bytes).hexdigest(),
        live_public_cut=cut,
        live_public_cut_path=args.live_public_cut.as_posix(),
        live_public_cut_file_sha256=hashlib.sha256(cut_bytes).hexdigest(),
        warmup_manifest=warmup,
        warmup_manifest_path=args.warmup_manifest.as_posix(),
        warmup_manifest_file_sha256=hashlib.sha256(warmup_bytes).hexdigest(),
        train_hours=args.train_hours,
        validation_hours_each=args.validation_hours_each,
        generated_ts_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )
    _atomic_write(args.output, manifest)
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "output": args.output.as_posix(),
                "logical_run_count": len(manifest["runs"]),
                "archive_file_count": manifest["archive_verification"]["file_count"],
                "manifest_sha256": manifest["manifest_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
