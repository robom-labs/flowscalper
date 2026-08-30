# OKX 공개시장 외부복제 검증기다.

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import time
import zipfile
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

import httpx

from backend.app.build_identity import git_commit
from scripts.research_adx_dmi_diversified_asymmetric_runner import (
    ADX_MINIMUM,
    ADX_RISE_LOOKBACK,
    DMI_PERIOD,
    PREREGISTERED_ADX_DMI_DIVERSIFIED_CANDIDATES,
    REENTRY_COOLDOWN_HOURS,
    AdxDmiSignalGate,
    build_directional_movement,
)
from scripts.research_asymmetric_trend_runner_tournament import (
    AsymmetricTrendSpec,
    asymmetric_candidate_fingerprint,
    research_asymmetric_trend_tournament,
)
from scripts.research_multiyear_trend_tournament import FundingRate
from scripts.research_public_intraday_trend_candidates import IntradayBar
from scripts.research_public_trend_candidates import DEFAULT_SYMBOLS, _parse_date
from scripts.validate_asymmetric_trend_runners_bybit import (
    MINIMUM_RESEARCH_DAYS,
    _candidate_assessment,
    _canonical_bytes,
)

OKX_API_BASE = "https://www.okx.com"
OKX_KLINE_PATH = "/api/v5/market/history-candles"
OKX_DOWNLOAD_LINK_PATH = "/priapi/v5/broker/public/trade-data/download-link"
INTERVAL_MINUTES = 240
INTERVAL_MS = INTERVAL_MINUTES * 60_000
HYPOTHESIS_ID = "HYP-134-OKX-ADX-DMI-ASYMMETRIC-RUNNER-EXTERNAL-REPLICATION"
PREREGISTRATION_PATH = (
    "docs/research/HYP-134-okx-adx-dmi-asymmetric-runner-external-replication.md"
)
PREREGISTRATION_COMMIT = "68c3c3e5cea2581ccab801ec9d4c04076b6e80ab"


def _hyp134_specs() -> tuple[AsymmetricTrendSpec, ...]:
    return tuple(
        replace(spec, candidate_id=spec.candidate_id.replace("T133_", "T134_OKX_", 1))
        for spec in PREREGISTERED_ADX_DMI_DIVERSIFIED_CANDIDATES
    )


PREREGISTERED_OKX_REPLICATION_CANDIDATES = _hyp134_specs()


def _okx_instrument(symbol: str) -> str:
    if not symbol.endswith("USDT"):
        raise ValueError(f"Expected a USDT symbol: {symbol}")
    return f"{symbol.removesuffix('USDT')}-USDT-SWAP"


def _request_okx(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    params: Mapping[str, str | int] | None = None,
    json_body: Mapping[str, object] | None = None,
) -> Mapping[str, object]:
    delay = 0.2
    for attempt in range(6):
        try:
            response = client.request(method, path, params=params, json=json_body)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, Mapping):
                raise TypeError("OKX response is not an object")
            if str(payload.get("code")) == "0":
                return payload
            raise RuntimeError(f"OKX public API error: {payload.get('code')} {payload.get('msg')}")
        except (httpx.HTTPError, ValueError, TypeError, RuntimeError):
            if attempt == 5:
                raise
        time.sleep(delay)
        delay = min(delay * 2, 3.2)
    raise AssertionError("unreachable OKX retry path")


def _data_list(payload: Mapping[str, object]) -> list[object]:
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise TypeError("OKX data is not an array")
    return rows


def _parse_kline_rows(
    symbol: str,
    rows: Sequence[object],
    *,
    start_ms: int,
    end_ms: int,
) -> tuple[IntradayBar, ...]:
    parsed: dict[int, tuple[object, ...]] = {}
    for raw in rows:
        if not isinstance(raw, list) or len(raw) < 9:
            raise TypeError("Invalid OKX kline row")
        timestamp = int(raw[0])
        if str(raw[8]) != "1" or not start_ms <= timestamp < end_ms:
            continue
        canonical = tuple(raw[:9])
        existing = parsed.get(timestamp)
        if existing is not None and existing != canonical:
            raise ValueError(f"Conflicting OKX kline duplicate: {timestamp}")
        parsed[timestamp] = canonical
    return tuple(
        IntradayBar(
            symbol=symbol,
            interval_minutes=INTERVAL_MINUTES,
            open_ts_ms=timestamp,
            open=float(str(raw[1])),
            high=float(str(raw[2])),
            low=float(str(raw[3])),
            close=float(str(raw[4])),
            volume=float(str(raw[6])),
        )
        for timestamp, raw in sorted(parsed.items())
    )


def _parse_funding_csv(
    symbol: str,
    payload: bytes,
    *,
    start_ms: int,
    end_ms: int,
) -> tuple[FundingRate, ...]:
    parsed: dict[int, float] = {}
    expected_instrument = _okx_instrument(symbol)
    with io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8-sig", newline="") as stream:
        for raw in csv.DictReader(stream):
            if raw["instrument_name"] != expected_instrument:
                continue
            timestamp = int(raw["funding_time"])
            if not start_ms <= timestamp < end_ms:
                continue
            rate = float(raw["funding_rate"])
            existing = parsed.get(timestamp)
            if existing is not None and existing != rate:
                raise ValueError(f"Conflicting OKX funding duplicate: {timestamp}")
            parsed[timestamp] = rate
    return tuple(
        FundingRate(symbol=symbol, funding_ts_ms=timestamp, rate=rate)
        for timestamp, rate in sorted(parsed.items())
    )


def _download_klines(
    client: httpx.Client,
    symbol: str,
    *,
    start_ms: int,
    end_ms: int,
) -> tuple[tuple[IntradayBar, ...], list[object]]:
    cursor = end_ms
    raw_rows: list[object] = []
    while cursor > start_ms:
        payload = _request_okx(
            client,
            "GET",
            OKX_KLINE_PATH,
            params={
                "instId": _okx_instrument(symbol),
                "bar": "4H",
                "after": cursor,
                "limit": 300,
            },
        )
        time.sleep(0.3)
        page = _data_list(payload)
        if not page:
            break
        raw_rows.extend(page)
        timestamps = [int(row[0]) for row in page if isinstance(row, list) and row]
        if not timestamps:
            raise ValueError("OKX kline page has no timestamp")
        oldest = min(timestamps)
        if oldest >= cursor:
            raise ValueError("OKX kline pagination moved forward")
        cursor = oldest
        if oldest <= start_ms:
            break
    return _parse_kline_rows(symbol, raw_rows, start_ms=start_ms, end_ms=end_ms), raw_rows


def _month_start(timestamp_ms: int) -> datetime:
    value = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
    return datetime(value.year, value.month, 1, tzinfo=UTC)


def _add_months(value: datetime, count: int) -> datetime:
    index = value.year * 12 + value.month - 1 + count
    return datetime(index // 12, index % 12 + 1, 1, tzinfo=UTC)


def _funding_link_requests(start_ms: int, end_ms: int) -> tuple[tuple[str, int, int], ...]:
    output: list[tuple[str, int, int]] = []
    cursor = _month_start(start_ms)
    end = datetime.fromtimestamp(end_ms / 1000, tz=UTC)
    current_month = datetime(end.year, end.month, 1, tzinfo=UTC)
    while cursor < current_month:
        boundary = min(_add_months(cursor, 5), current_month)
        output.append(
            (
                "monthly",
                int(cursor.timestamp() * 1000),
                int(boundary.timestamp() * 1000) - 1,
            )
        )
        cursor = boundary
    daily_ms = max(int(cursor.timestamp() * 1000), start_ms)
    while daily_ms < end_ms:
        boundary_ms = min(daily_ms + 6 * 86_400_000, end_ms)
        output.append(("daily", daily_ms, boundary_ms - 1))
        daily_ms = boundary_ms
    return tuple(output)


def _funding_urls(
    client: httpx.Client,
    symbol: str,
    *,
    start_ms: int,
    end_ms: int,
) -> tuple[dict[str, object], ...]:
    family = _okx_instrument(symbol).removesuffix("-SWAP")
    output: dict[str, dict[str, object]] = {}
    for aggregation, begin, end in _funding_link_requests(start_ms, end_ms):
        payload = _request_okx(
            client,
            "POST",
            OKX_DOWNLOAD_LINK_PATH,
            json_body={
                "module": "3",
                "instType": "SWAP",
                "instQueryParam": {
                    "instFamilyList": [] if aggregation == "daily" else [family]
                },
                "dateQuery": {
                    "dateAggrType": aggregation,
                    "begin": str(begin),
                    "end": str(end),
                },
            },
        )
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise TypeError("OKX funding download data is not an object")
        details = data.get("details")
        if not isinstance(details, list):
            raise TypeError("OKX funding download details is not an array")
        for detail in details:
            if not isinstance(detail, Mapping):
                raise TypeError("OKX funding detail is not an object")
            group = detail.get("groupDetails")
            if not isinstance(group, list):
                raise TypeError("OKX funding groupDetails is not an array")
            for item in group:
                if not isinstance(item, Mapping) or not isinstance(item.get("url"), str):
                    raise TypeError("OKX funding download link is invalid")
                output[str(item["filename"])] = dict(item)
    if not output:
        raise RuntimeError(f"BLOCKED_BY_OFFICIAL_OKX_HISTORY_ACCESS: {symbol}")
    return tuple(output[key] for key in sorted(output))


def _load_or_download_symbol(
    symbol: str,
    *,
    start_ms: int,
    end_ms: int,
    cache_dir: Path,
) -> tuple[str, tuple[IntradayBar, ...], tuple[FundingRate, ...], dict[str, object]]:
    symbol_dir = cache_dir / symbol
    kline_path = symbol_dir / f"{start_ms}-{end_ms}-4h-klines.json"
    funding_path = symbol_dir / f"{start_ms}-{end_ms}-funding.json"
    source_path = symbol_dir / f"{start_ms}-{end_ms}-funding-sources.json"
    symbol_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(
        base_url=OKX_API_BASE,
        timeout=30.0,
        headers={"User-Agent": "ROBOM-FlowScalper-Public-Paper-Research/0.2"},
    ) as client:
        if kline_path.exists():
            raw_klines = json.loads(kline_path.read_text(encoding="utf-8"))
            if not isinstance(raw_klines, list):
                raise TypeError("OKX kline cache is not an array")
            bars = _parse_kline_rows(symbol, raw_klines, start_ms=start_ms, end_ms=end_ms)
        else:
            bars, raw_klines = _download_klines(client, symbol, start_ms=start_ms, end_ms=end_ms)
            kline_path.write_text(json.dumps(raw_klines, separators=(",", ":")), encoding="utf-8")

        if funding_path.exists() and source_path.exists():
            raw_funding = json.loads(funding_path.read_text(encoding="utf-8"))
            source_manifest = json.loads(source_path.read_text(encoding="utf-8"))
            funding = tuple(
                FundingRate(symbol=symbol, funding_ts_ms=int(row[0]), rate=float(row[1]))
                for row in raw_funding
            )
        else:
            links = _funding_urls(client, symbol, start_ms=start_ms, end_ms=end_ms)
            funding_by_ts: dict[int, FundingRate] = {}
            source_manifest = []
            for item in links:
                filename = str(item["filename"])
                archive_path = symbol_dir / filename
                if archive_path.exists():
                    archive = archive_path.read_bytes()
                else:
                    response = client.get(str(item["url"]))
                    response.raise_for_status()
                    archive = response.content
                    archive_path.write_bytes(archive)
                with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                    names = [name for name in bundle.namelist() if name.endswith(".csv")]
                    if len(names) != 1:
                        raise ValueError(f"Expected one CSV in {filename}")
                    csv_payload = bundle.read(names[0])
                for row in _parse_funding_csv(
                    symbol,
                    csv_payload,
                    start_ms=start_ms,
                    end_ms=end_ms,
                ):
                    existing = funding_by_ts.get(row.funding_ts_ms)
                    if existing is not None and existing.rate != row.rate:
                        raise ValueError(f"Conflicting funding duplicate: {row.funding_ts_ms}")
                    funding_by_ts[row.funding_ts_ms] = row
                source_manifest.append(
                    {
                        "filename": filename,
                        "url": item["url"],
                        "archive_sha256": hashlib.sha256(archive).hexdigest(),
                        "csv_sha256": hashlib.sha256(csv_payload).hexdigest(),
                    }
                )
            funding = tuple(funding_by_ts[key] for key in sorted(funding_by_ts))
            raw_funding = [[row.funding_ts_ms, row.rate] for row in funding]
            funding_path.write_text(
                json.dumps(raw_funding, separators=(",", ":")),
                encoding="utf-8",
            )
            source_path.write_text(
                json.dumps(source_manifest, indent=2, sort_keys=True),
                encoding="utf-8",
            )

    if len(bars) < 205 or not funding:
        raise ValueError(
            f"Insufficient OKX data for {symbol}: bars={len(bars)} funding={len(funding)}"
        )
    gap_count = sum(
        current.open_ts_ms - previous.open_ts_ms != INTERVAL_MS
        for previous, current in zip(bars, bars[1:], strict=False)
    )
    return symbol, bars, funding, {
        "venue": "OKX_USDT_SWAP",
        "symbol": symbol,
        "instrument": _okx_instrument(symbol),
        "bar_count": len(bars),
        "bar_start_ts_ms": bars[0].open_ts_ms,
        "bar_end_ts_ms": bars[-1].open_ts_ms + INTERVAL_MS,
        "bar_gap_count": gap_count,
        "bar_sha256": hashlib.sha256(_canonical_bytes(raw_klines)).hexdigest(),
        "funding_count": len(funding),
        "funding_start_ts_ms": funding[0].funding_ts_ms,
        "funding_end_ts_ms": funding[-1].funding_ts_ms,
        "funding_sha256": hashlib.sha256(_canonical_bytes(raw_funding)).hexdigest(),
        "funding_source_count": len(source_manifest),
        "funding_sources_sha256": hashlib.sha256(_canonical_bytes(source_manifest)).hexdigest(),
        "public_only": True,
        "api_key_used": False,
    }


def load_okx_public_research_data(
    symbols: Sequence[str],
    *,
    start_ms: int,
    end_ms: int,
    cache_dir: Path,
) -> tuple[
    dict[str, tuple[IntradayBar, ...]],
    dict[str, tuple[FundingRate, ...]],
    list[dict[str, object]],
]:
    with ThreadPoolExecutor(max_workers=1) as executor:
        loaded = list(
            executor.map(
                lambda symbol: _load_or_download_symbol(
                    symbol, start_ms=start_ms, end_ms=end_ms, cache_dir=cache_dir
                ),
                symbols,
            )
        )
    return (
        {symbol: rows for symbol, rows, _, _ in loaded},
        {symbol: rows for symbol, _, rows, _ in loaded},
        [manifest for _, _, _, manifest in loaded],
    )


def build_report(
    bars_by_symbol: Mapping[str, Sequence[IntradayBar]],
    funding_by_symbol: Mapping[str, Sequence[FundingRate]],
    manifest: Sequence[Mapping[str, object]],
    *,
    start_ms: int,
    end_ms: int,
    specs: Sequence[AsymmetricTrendSpec] = PREREGISTERED_OKX_REPLICATION_CANDIDATES,
) -> dict[str, object]:
    dmi = {symbol: build_directional_movement(rows) for symbol, rows in bars_by_symbol.items()}
    gate = AdxDmiSignalGate(dmi, specs)
    outcomes, funding_audit = research_asymmetric_trend_tournament(
        bars_by_symbol,
        funding_by_symbol,
        specs,
        signal_gate=gate,
        signal_observer=gate.observe_qualified,
    )
    assessments = {
        candidate_id: _candidate_assessment(
            candidate_id, rows, start_ms=start_ms, end_ms=end_ms, trials=len(specs)
        )
        for candidate_id, rows in outcomes.items()
    }
    passed = sorted(
        candidate_id
        for candidate_id, assessment in assessments.items()
        if assessment["external_venue_replication_pass"] is True
    )
    datasets = list(manifest)
    return {
        "schema_version": 1,
        "status": "EXTERNAL_REPLICATION_PASS_FORWARD_REQUIRED" if passed else "NOT_PROVEN",
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "private_api_enabled": False,
        "profitability_claim": "NOT_PROVEN",
        "real_money_readiness": "NOT_READY",
        "generated_ts_ms": time.time_ns() // 1_000_000,
        "code_hash": git_commit(),
        "source": {
            "venue": "OKX_USDT_SWAP",
            "public_only": True,
            "api_key_used": False,
            "kline_url": f"{OKX_API_BASE}{OKX_KLINE_PATH}",
            "funding_download_link_url": f"{OKX_API_BASE}{OKX_DOWNLOAD_LINK_PATH}",
            "bar_interval": "4h",
            "start_ts_ms": start_ms,
            "end_ts_ms": end_ms,
            "completed_candles_only": True,
            "dataset_hash": hashlib.sha256(_canonical_bytes(datasets)).hexdigest(),
            "datasets": datasets,
        },
        "replication_boundary": {
            "candidate_rules_changed": False,
            "selection_or_ranking_on_okx": False,
            "bybit_results_were_seen_before_this_test": True,
            "classification": "FIXED_EXTERNAL_VENUE_REPLICATION",
        },
        "preregistration": {
            "hypothesis_id": HYPOTHESIS_ID,
            "path": PREREGISTRATION_PATH,
            "commit": PREREGISTRATION_COMMIT,
            "candidate_count": len(specs),
            "candidate_ids": [spec.candidate_id for spec in specs],
            "candidate_fingerprint": asymmetric_candidate_fingerprint(specs),
            "candidate_parameters": [asdict(spec) for spec in specs],
            "dmi_period": DMI_PERIOD,
            "adx_minimum": ADX_MINIMUM,
            "adx_rise_lookback_completed_bars": ADX_RISE_LOOKBACK,
            "reentry_cooldown_hours_same_symbol_any_direction": REENTRY_COOLDOWN_HOURS,
            "minimum_closed_sample": 100,
            "thresholds_lowered_after_results": False,
            "no_fixed_take_profit": True,
            "no_fixed_maximum_hold": True,
        },
        "signal_filter_audit": gate.audit,
        "funding_cost_risk_audit": funding_audit,
        "candidate_assessments": assessments,
        "external_venue_replication_pass_candidates": passed,
        "promotion_assessment": {
            "status": "NOT_PROVEN",
            "registry_changes": [],
            "live_shadow_changes": [],
            "future_bid_ask_depth_base_stress_required": True,
            "minimum_natural_base_stress_opportunities_per_strategy": 30,
            "real_orders_remain_forbidden": True,
        },
        "limitations": [
            "OKX, Bybit and Binance remain correlated crypto markets.",
            "Four-hour bars do not contain executable bid-ask depth or intrabar ordering.",
            "The surviving-symbol universe retains survivorship bias.",
            "External replication does not replace future bid-ask PAPER SHADOW evidence.",
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--symbol", action="append")
    parser.add_argument("--cache-dir", type=Path, default=Path("data/okx-adx-dmi-runner-public-v1"))
    parser.add_argument("--output-json", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start_ms = _parse_date(args.start)
    end_ms = _parse_date(args.end)
    if end_ms - start_ms < MINIMUM_RESEARCH_DAYS * 86_400_000:
        raise ValueError(f"OKX replication requires at least {MINIMUM_RESEARCH_DAYS} days")
    symbols = tuple(args.symbol or DEFAULT_SYMBOLS)
    bars, funding, manifest = load_okx_public_research_data(
        symbols, start_ms=start_ms, end_ms=end_ms, cache_dir=args.cache_dir
    )
    report = build_report(bars, funding, manifest, start_ms=start_ms, end_ms=end_ms)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output_json is None:
        print(rendered, end="")
    else:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
