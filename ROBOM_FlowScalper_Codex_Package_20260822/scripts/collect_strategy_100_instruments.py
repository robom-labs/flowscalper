# Binance USD-M 공개 instrument 필터 snapshot을 100후보 연구증거로 저장한다.

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from backend.app.research import build_binance_instrument_manifest

ENDPOINT = "https://fapi.binance.com/fapi/v1/exchangeInfo"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evidence/STRATEGY_100_INSTRUMENTS.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    response = httpx.get(ENDPOINT, timeout=20.0)
    response.raise_for_status()
    source_bytes = response.content
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("exchangeInfo 응답이 object가 아닙니다.")
    now = datetime.now(UTC)
    manifest = build_binance_instrument_manifest(
        payload,
        source_bytes_sha256=hashlib.sha256(source_bytes).hexdigest(),
        collected_ts_ms=int(now.timestamp() * 1_000),
        generated_ts_utc=now.isoformat().replace("+00:00", "Z"),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": args.output.as_posix(),
                "status": manifest["status"],
                "instrument_count": manifest["instrument_count"],
                "manifest_sha256": manifest["manifest_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
