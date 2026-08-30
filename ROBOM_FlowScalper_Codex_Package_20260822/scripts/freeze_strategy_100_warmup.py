# Binance 공개 완성봉을 자격증명 없이 받아 100후보 V2 워밍업으로 동결한다.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from backend.app.research.strategy100_dataset_v2 import manifest_checksum

BINANCE_FUTURES_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
INTERVAL_MS = 300_000
DEFAULT_SYMBOLS = (
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "DOTUSDT",
    "LTCUSDT",
    "BCHUSDT",
    "TRXUSDT",
    "ETCUSDT",
    "ATOMUSDT",
    "NEARUSDT",
    "AAVEUSDT",
    "FILUSDT",
    "UNIUSDT",
    "SUIUSDT",
    "ARBUSDT",
    "OPUSDT",
    "INJUSDT",
    "FETUSDT",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() == content:
            return
        raise FileExistsError(f"기존 공개봉 워밍업 파일을 덮어쓰지 않습니다: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def _ordered_complete_rows(
    rows: Sequence[object],
    *,
    symbol: str,
    start_ms: int,
    end_ms: int,
) -> tuple[dict[str, object], ...]:
    normalized: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, Mapping) or str(row.get("symbol", "")) != symbol:
            raise RuntimeError(f"{symbol} 공개봉 cache의 종목 또는 행 형식이 잘못됐습니다.")
        normalized.append(dict(row))
    by_open = {int(str(row["open_ts_ms"])): row for row in normalized}
    if len(by_open) != len(normalized):
        raise RuntimeError(f"{symbol} 공개봉 cache에 중복 시각이 있습니다.")
    ordered = tuple(
        sorted(
            by_open.values(),
            key=lambda row: int(str(row["open_ts_ms"])),
        )
    )
    expected_count = (end_ms - start_ms) // INTERVAL_MS
    if (
        len(ordered) != expected_count
        or not ordered
        or int(str(ordered[0]["open_ts_ms"])) != start_ms
        or int(str(ordered[-1]["open_ts_ms"])) + INTERVAL_MS != end_ms
        or any(
            int(str(right["open_ts_ms"])) - int(str(left["open_ts_ms"])) != INTERVAL_MS
            for left, right in zip(ordered, ordered[1:], strict=False)
        )
    ):
        raise RuntimeError(
            f"{symbol} 공개봉이 연속 구간을 완전히 덮지 않습니다: "
            f"{len(ordered)}/{expected_count}"
        )
    return ordered


def _download_symbol(
    symbol: str,
    *,
    start_ms: int,
    end_ms: int,
    cache_root: Path,
) -> tuple[str, Path, tuple[dict[str, object], ...]]:
    if start_ms % INTERVAL_MS or end_ms % INTERVAL_MS or end_ms <= start_ms:
        raise ValueError("공개봉 워밍업 시작·종료는 5분 경계의 순방향이어야 합니다.")
    path = cache_root / f"{symbol}-5m-{start_ms}-{end_ms}-v2.json"
    if path.exists():
        payload = json.loads(path.read_bytes())
        if not isinstance(payload, list):
            raise ValueError(f"기존 공개봉 워밍업 JSON이 잘못됐습니다: {path}")
        return symbol, path, _ordered_complete_rows(
            payload,
            symbol=symbol,
            start_ms=start_ms,
            end_ms=end_ms,
        )
    rows: list[dict[str, object]] = []
    cursor = start_ms
    timeout = httpx.Timeout(30.0, connect=10.0)
    with httpx.Client(
        timeout=timeout,
        headers={"User-Agent": "ROBOM-FlowScalper-PAPER/0.2"},
    ) as client:
        while cursor < end_ms:
            response: httpx.Response | None = None
            for attempt in range(6):
                response = client.get(
                    BINANCE_FUTURES_KLINES_URL,
                    params={
                        "symbol": symbol,
                        "interval": "5m",
                        "startTime": cursor,
                        "endTime": end_ms - 1,
                        "limit": 1_500,
                    },
                )
                if response.status_code != 429:
                    break
                retry_after = float(response.headers.get("retry-after", "0") or 0)
                time.sleep(max(retry_after, min(30.0, 2.0**attempt)))
            if response is None:
                raise RuntimeError(f"{symbol} 공개봉 응답을 받지 못했습니다.")
            response.raise_for_status()
            page = response.json()
            if not isinstance(page, list) or not page:
                break
            for raw in page:
                open_ts_ms = int(raw[0])
                if open_ts_ms < cursor or open_ts_ms >= end_ms:
                    continue
                rows.append(
                    {
                        "symbol": symbol,
                        "open_ts_ms": open_ts_ms,
                        "open": str(raw[1]),
                        "high": str(raw[2]),
                        "low": str(raw[3]),
                        "close": str(raw[4]),
                        "volume": str(raw[5]),
                        "quote_volume": str(raw[7]),
                        "trade_count": int(raw[8]),
                        "taker_buy_volume": str(raw[9]),
                        "taker_buy_quote_volume": str(raw[10]),
                    }
                )
            next_cursor = int(page[-1][0]) + INTERVAL_MS
            if next_cursor <= cursor:
                raise RuntimeError(f"{symbol} 공개봉 pagination이 전진하지 않습니다.")
            cursor = next_cursor
    ordered = _ordered_complete_rows(
        rows,
        symbol=symbol,
        start_ms=start_ms,
        end_ms=end_ms,
    )
    encoded = json.dumps(ordered, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
    _atomic_write(path, encoded)
    return symbol, path, ordered


def freeze_warmup(
    *,
    symbols: tuple[str, ...],
    start_ms: int,
    end_ms: int,
    cache_root: Path,
) -> dict[str, Any]:
    if len(symbols) < 20 or len(set(symbols)) != len(symbols):
        raise ValueError("100후보 V2 워밍업은 중복 없는 20종목 이상이어야 합니다.")
    resolved_cache = cache_root.resolve()
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                _download_symbol,
                symbol,
                start_ms=start_ms,
                end_ms=end_ms,
                cache_root=resolved_cache,
            )
            for symbol in symbols
        ]
        downloaded = [future.result() for future in futures]
    project_root = Path.cwd().resolve()
    cache_reference = (
        resolved_cache.relative_to(project_root).as_posix()
        if resolved_cache.is_relative_to(project_root)
        else resolved_cache.as_posix()
    )
    files = [
        {
            "symbol": symbol,
            "relative_path": path.relative_to(resolved_cache).as_posix(),
            "file_sha256": _sha256(path),
            "bar_count": len(rows),
            "first_ts_ms": int(str(rows[0]["open_ts_ms"])),
            "last_close_ts_ms": int(str(rows[-1]["open_ts_ms"])) + INTERVAL_MS,
            "size_bytes": path.stat().st_size,
        }
        for symbol, path, rows in sorted(downloaded)
    ]
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "status": "FROZEN_PUBLIC_KLINE_WARMUP",
        "generated_ts_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source": {
            "venue": "BINANCE_USDM",
            "endpoint": BINANCE_FUTURES_KLINES_URL,
            "endpoint_class": "PUBLIC_MARKET_DATA",
            "interval": "5m",
        },
        "cache_root": cache_reference,
        "start_ts_ms": start_ms,
        "cutoff_ts_ms": end_ms,
        "symbols": list(sorted(symbols)),
        "symbol_count": len(symbols),
        "bar_count": sum(len(rows) for _, _, rows in downloaded),
        "files": files,
        "usage_contract": {
            "completed_bars_only": True,
            "decision_time_cutoff_required": True,
            "entry_signal_before_live_public_window": False,
            "execution_price_source": "FROZEN_LIVE_PUBLIC_BID_ASK_ONLY",
            "lookahead_allowed": False,
        },
        "paper_only": True,
        "real_orders_enabled": False,
        "private_api_enabled": False,
        "auth_required": False,
    }
    manifest["manifest_sha256"] = manifest_checksum(manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-ts-ms", type=int, required=True)
    parser.add_argument("--end-ts-ms", type=int, required=True)
    parser.add_argument(
        "--cache-root", type=Path, default=Path("data/strategy100-warmup-v2")
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evidence/STRATEGY_100_WARMUP_MANIFEST_V2.json"),
    )
    parser.add_argument("--symbols", nargs="*", default=list(DEFAULT_SYMBOLS))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = freeze_warmup(
        symbols=tuple(str(value).upper() for value in args.symbols),
        start_ms=args.start_ts_ms,
        end_ms=args.end_ts_ms,
        cache_root=args.cache_root,
    )
    rendered = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n"
    _atomic_write(args.output, rendered)
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "output": args.output.as_posix(),
                "symbol_count": manifest["symbol_count"],
                "bar_count": manifest["bar_count"],
                "manifest_sha256": manifest["manifest_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
