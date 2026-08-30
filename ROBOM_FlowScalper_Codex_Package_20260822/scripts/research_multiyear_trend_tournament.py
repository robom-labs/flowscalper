# Binance 공개 4시간봉과 실제 펀딩 이력으로 다년 저회전 추세 PAPER 후보를 검증한다.

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TypedDict

import httpx

from backend.app.build_identity import git_commit
from backend.app.research import probability_of_backtest_overfitting
from scripts.research_public_intraday_trend_candidates import IntradayBar
from scripts.research_public_trend_candidates import DEFAULT_SYMBOLS, _parse_date
from scripts.research_slow_regime_trend_tournament import (
    SlowTrendOutcome,
    SlowTrendSpec,
    _context_snapshots,
    _development_profile,
    _eligible,
    _features,
    _fold_returns,
    _oos_assessment,
    _rank_key,
    _rankable,
    _split,
    _symbol_outcomes,
    apply_portfolio_limits,
)

BINANCE_FUTURES_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
BINANCE_FUTURES_FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
INTERVAL = "4h"
INTERVAL_MINUTES = 240
INTERVAL_MS = INTERVAL_MINUTES * 60_000
BASE_EXECUTION_COST_BPS = 13.0
STRESS_EXECUTION_COST_BPS = 25.0
MINIMUM_RESEARCH_DAYS = 1_095
MAXIMUM_FINALISTS = 5
HYPOTHESIS_ID = "HYP-127-MULTIYEAR-LOW-TURNOVER-TREND-TOURNAMENT"
PREREGISTRATION_PATH = "docs/research/HYP-127-multiyear-low-turnover-trend-tournament.md"


@dataclass(frozen=True, slots=True)
class FundingRate:
    symbol: str
    funding_ts_ms: int
    rate: float


@dataclass(frozen=True, slots=True)
class FundingAdjustment:
    funding_bps: float
    applied_event_count: int
    excluded_ambiguous_credit_bps: float
    excluded_ambiguous_credit_count: int


class _SpecParameters(TypedDict):
    lookback: int
    momentum: float
    rank_threshold: float
    breadth: float
    adx: float
    relative_volume: float
    retest_band: float
    stop_buffer: float
    tp1_r: float
    tp2_r: float
    cooldown_hours: int
    slow_alignment: bool


def _spec(
    family_key: str,
    family: str,
    setup_kind: str,
    side_policy: str,
    style: str,
    *,
    lookback: int,
    momentum: float,
    rank_threshold: float,
    breadth: float,
    adx: float,
    relative_volume: float,
    retest_band: float,
    stop_buffer: float,
    tp1_r: float,
    tp2_r: float,
    cooldown_hours: int,
    slow_alignment: bool,
) -> SlowTrendSpec:
    return SlowTrendSpec(
        candidate_id=f"T127_{family_key}_{side_policy}_{style}",
        family=family,
        interval_minutes=INTERVAL_MINUTES,
        setup_kind=setup_kind,
        side_policy=side_policy,
        style=style,
        lookback=lookback,
        momentum_72h_minimum=momentum,
        rank_threshold=rank_threshold,
        breadth_threshold=breadth,
        adx_minimum=adx,
        relative_volume_minimum=relative_volume,
        retest_band_atr=retest_band,
        stop_buffer_atr=stop_buffer,
        tp1_r=tp1_r,
        tp2_r=tp2_r,
        cooldown_hours=cooldown_hours,
        require_slow_alignment=slow_alignment,
    )


def _candidate_specs() -> tuple[SlowTrendSpec, ...]:
    families: tuple[tuple[str, str, str, Mapping[str, _SpecParameters]], ...] = (
        (
            "CHANNEL_BREAKOUT_4H",
            "FOUR_HOUR_CHANNEL_BREAKOUT_MULTIYEAR",
            "CHANNEL_BREAKOUT",
            {
                "BALANCED": dict(
                    lookback=30,
                    momentum=0.025,
                    rank_threshold=0.60,
                    breadth=0.55,
                    adx=16,
                    relative_volume=0.65,
                    retest_band=0.0,
                    stop_buffer=0.30,
                    tp1_r=1.5,
                    tp2_r=4.0,
                    cooldown_hours=24,
                    slow_alignment=False,
                ),
                "SELECTIVE": dict(
                    lookback=60,
                    momentum=0.050,
                    rank_threshold=0.75,
                    breadth=0.60,
                    adx=22,
                    relative_volume=0.90,
                    retest_band=0.0,
                    stop_buffer=0.45,
                    tp1_r=2.0,
                    tp2_r=5.5,
                    cooldown_hours=48,
                    slow_alignment=True,
                ),
            },
        ),
        (
            "BREAKOUT_RETEST_4H",
            "FOUR_HOUR_BREAKOUT_FIRST_RETEST_MULTIYEAR",
            "BREAKOUT_RETEST",
            {
                "BALANCED": dict(
                    lookback=30,
                    momentum=0.020,
                    rank_threshold=0.60,
                    breadth=0.55,
                    adx=15,
                    relative_volume=0.55,
                    retest_band=0.40,
                    stop_buffer=0.25,
                    tp1_r=1.5,
                    tp2_r=4.0,
                    cooldown_hours=24,
                    slow_alignment=False,
                ),
                "SELECTIVE": dict(
                    lookback=60,
                    momentum=0.040,
                    rank_threshold=0.75,
                    breadth=0.60,
                    adx=21,
                    relative_volume=0.80,
                    retest_band=0.22,
                    stop_buffer=0.35,
                    tp1_r=2.0,
                    tp2_r=5.0,
                    cooldown_hours=36,
                    slow_alignment=True,
                ),
            },
        ),
        (
            "FIRST_PULLBACK_4H",
            "FOUR_HOUR_EARLY_TREND_FIRST_PULLBACK_MULTIYEAR",
            "FIRST_PULLBACK_RECLAIM",
            {
                "BALANCED": dict(
                    lookback=18,
                    momentum=0.015,
                    rank_threshold=0.60,
                    breadth=0.55,
                    adx=15,
                    relative_volume=0.50,
                    retest_band=0.45,
                    stop_buffer=0.25,
                    tp1_r=1.5,
                    tp2_r=4.0,
                    cooldown_hours=20,
                    slow_alignment=False,
                ),
                "SELECTIVE": dict(
                    lookback=12,
                    momentum=0.030,
                    rank_threshold=0.75,
                    breadth=0.60,
                    adx=20,
                    relative_volume=0.75,
                    retest_band=0.25,
                    stop_buffer=0.35,
                    tp1_r=2.0,
                    tp2_r=5.0,
                    cooldown_hours=32,
                    slow_alignment=True,
                ),
            },
        ),
        (
            "ICHIMOKU_PULLBACK_4H",
            "FOUR_HOUR_ICHIMOKU_PULLBACK_MULTIYEAR",
            "ICHIMOKU_PULLBACK_CONTINUATION",
            {
                "BALANCED": dict(
                    lookback=52,
                    momentum=0.020,
                    rank_threshold=0.60,
                    breadth=0.55,
                    adx=15,
                    relative_volume=0.55,
                    retest_band=0.35,
                    stop_buffer=0.25,
                    tp1_r=1.5,
                    tp2_r=4.0,
                    cooldown_hours=24,
                    slow_alignment=False,
                ),
                "SELECTIVE": dict(
                    lookback=52,
                    momentum=0.040,
                    rank_threshold=0.75,
                    breadth=0.60,
                    adx=21,
                    relative_volume=0.80,
                    retest_band=0.18,
                    stop_buffer=0.40,
                    tp1_r=2.0,
                    tp2_r=5.0,
                    cooldown_hours=40,
                    slow_alignment=True,
                ),
            },
        ),
        (
            "SWEEP_RECLAIM_4H",
            "FOUR_HOUR_TREND_LIQUIDITY_SWEEP_RECLAIM_MULTIYEAR",
            "LIQUIDITY_SWEEP_RECLAIM",
            {
                "BALANCED": dict(
                    lookback=18,
                    momentum=0.020,
                    rank_threshold=0.60,
                    breadth=0.55,
                    adx=15,
                    relative_volume=0.55,
                    retest_band=0.65,
                    stop_buffer=0.25,
                    tp1_r=1.5,
                    tp2_r=4.0,
                    cooldown_hours=24,
                    slow_alignment=False,
                ),
                "SELECTIVE": dict(
                    lookback=36,
                    momentum=0.040,
                    rank_threshold=0.75,
                    breadth=0.60,
                    adx=21,
                    relative_volume=0.85,
                    retest_band=0.38,
                    stop_buffer=0.35,
                    tp1_r=2.0,
                    tp2_r=5.0,
                    cooldown_hours=40,
                    slow_alignment=True,
                ),
            },
        ),
    )
    output: list[SlowTrendSpec] = []
    for family_key, family, setup_kind, styles in families:
        for side_policy in ("LONG", "SHORT", "BOTH"):
            for style, parameters in styles.items():
                output.append(
                    _spec(
                        family_key,
                        family,
                        setup_kind,
                        side_policy,
                        style,
                        **parameters,
                    )
                )
    return tuple(output)


PREREGISTERED_CANDIDATES = _candidate_specs()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _cache_path(
    cache_dir: Path,
    symbol: str,
    kind: str,
    start_ms: int,
    end_ms: int,
) -> Path:
    return cache_dir / f"{symbol}-{kind}-{start_ms}-{end_ms}.json"


def _request_page(
    client: httpx.Client,
    url: str,
    *,
    params: Mapping[str, str | int | float | bool | None],
) -> list[object]:
    response: httpx.Response | None = None
    for attempt in range(7):
        response = client.get(url, params=params)
        if response.status_code not in {418, 429} and response.status_code < 500:
            break
        retry_after = float(response.headers.get("retry-after", "0") or 0)
        time.sleep(max(retry_after, min(30.0, 2.0**attempt)))
    if response is None:
        raise RuntimeError("Binance 공개시장 응답을 받지 못했습니다.")
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("Binance 공개시장 응답이 배열이 아닙니다.")
    return payload


def _download_four_hour_bars(
    symbol: str,
    *,
    start_ms: int,
    end_ms: int,
    cache_dir: Path,
) -> tuple[tuple[IntradayBar, ...], Path]:
    path = _cache_path(cache_dir, symbol, INTERVAL, start_ms, end_ms)
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        return tuple(IntradayBar(**row) for row in payload), path
    rows: list[IntradayBar] = []
    cursor = start_ms
    headers = {"User-Agent": "ROBOM-FlowScalper-PAPER/0.2"}
    with httpx.Client(timeout=30, headers=headers) as client:
        while cursor < end_ms:
            page = _request_page(
                client,
                BINANCE_FUTURES_KLINES_URL,
                params={
                    "symbol": symbol,
                    "interval": INTERVAL,
                    "startTime": cursor,
                    "endTime": end_ms - 1,
                    "limit": 1_500,
                },
            )
            if not page:
                break
            for raw in page:
                if not isinstance(raw, list) or len(raw) < 10:
                    raise RuntimeError(f"{symbol} 4시간봉 형식이 올바르지 않습니다.")
                open_ts_ms = int(raw[0])
                if open_ts_ms < start_ms or open_ts_ms + INTERVAL_MS > end_ms:
                    continue
                rows.append(
                    IntradayBar(
                        symbol=symbol,
                        interval_minutes=INTERVAL_MINUTES,
                        open_ts_ms=open_ts_ms,
                        open=float(raw[1]),
                        high=float(raw[2]),
                        low=float(raw[3]),
                        close=float(raw[4]),
                        volume=float(raw[5]),
                    )
                )
            last = page[-1]
            if not isinstance(last, list):
                raise RuntimeError(f"{symbol} 4시간봉 cursor 행이 올바르지 않습니다.")
            next_cursor = int(last[0]) + INTERVAL_MS
            if next_cursor <= cursor:
                raise RuntimeError(f"{symbol} 4시간봉 cursor가 전진하지 않았습니다.")
            cursor = next_cursor
            time.sleep(0.05)
    ordered = tuple(
        sorted(
            {row.open_ts_ms: row for row in rows}.values(),
            key=lambda row: row.open_ts_ms,
        )
    )
    if len(ordered) < 1_000:
        raise RuntimeError(f"{symbol} 4시간 연구봉이 부족합니다: {len(ordered)}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(row) for row in ordered], separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return ordered, path


def _download_funding_rates(
    symbol: str,
    *,
    start_ms: int,
    end_ms: int,
    cache_dir: Path,
) -> tuple[tuple[FundingRate, ...], Path]:
    path = _cache_path(cache_dir, symbol, "funding", start_ms, end_ms)
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        return tuple(FundingRate(**row) for row in payload), path
    rows: list[FundingRate] = []
    cursor = start_ms
    headers = {"User-Agent": "ROBOM-FlowScalper-PAPER/0.2"}
    with httpx.Client(timeout=30, headers=headers) as client:
        while cursor < end_ms:
            page = _request_page(
                client,
                BINANCE_FUTURES_FUNDING_URL,
                params={
                    "symbol": symbol,
                    "startTime": cursor,
                    "endTime": end_ms - 1,
                    "limit": 1_000,
                },
            )
            if not page:
                break
            for raw in page:
                if not isinstance(raw, dict):
                    raise RuntimeError(f"{symbol} 펀딩 이력 형식이 올바르지 않습니다.")
                funding_ts_ms = int(raw["fundingTime"])
                if start_ms <= funding_ts_ms < end_ms:
                    rows.append(
                        FundingRate(
                            symbol=symbol,
                            funding_ts_ms=funding_ts_ms,
                            rate=float(raw["fundingRate"]),
                        )
                    )
            last = page[-1]
            if not isinstance(last, dict):
                raise RuntimeError(f"{symbol} 펀딩 cursor 행이 올바르지 않습니다.")
            next_cursor = int(last["fundingTime"]) + 1
            if next_cursor <= cursor:
                raise RuntimeError(f"{symbol} 펀딩 cursor가 전진하지 않았습니다.")
            cursor = next_cursor
            time.sleep(0.05)
    ordered = tuple(
        sorted(
            {row.funding_ts_ms: row for row in rows}.values(),
            key=lambda row: row.funding_ts_ms,
        )
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(row) for row in ordered], separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return ordered, path


def _load_symbol_bundle(
    symbol: str,
    *,
    start_ms: int,
    end_ms: int,
    cache_dir: Path,
) -> tuple[str, tuple[IntradayBar, ...], tuple[FundingRate, ...], Path, Path]:
    bars, bar_path = _download_four_hour_bars(
        symbol,
        start_ms=start_ms,
        end_ms=end_ms,
        cache_dir=cache_dir,
    )
    funding, funding_path = _download_funding_rates(
        symbol,
        start_ms=start_ms,
        end_ms=end_ms,
        cache_dir=cache_dir,
    )
    return symbol, bars, funding, bar_path, funding_path


def load_public_research_data(
    symbols: Sequence[str],
    *,
    start_ms: int,
    end_ms: int,
    cache_dir: Path,
) -> tuple[
    dict[str, tuple[IntradayBar, ...]],
    dict[str, tuple[FundingRate, ...]],
    tuple[dict[str, object], ...],
]:
    if "BTCUSDT" not in symbols:
        raise ValueError("시장 레짐 기준 BTCUSDT가 연구 종목에 필요합니다.")
    with ThreadPoolExecutor(max_workers=min(2, len(symbols))) as executor:
        futures = [
            executor.submit(
                _load_symbol_bundle,
                symbol,
                start_ms=start_ms,
                end_ms=end_ms,
                cache_dir=cache_dir,
            )
            for symbol in symbols
        ]
        downloaded = [future.result() for future in futures]
    bars = {symbol: values for symbol, values, _, _, _ in downloaded}
    funding = {symbol: values for symbol, _, values, _, _ in downloaded}
    manifest_rows: list[dict[str, object]] = []
    for symbol, values, rates, bar_path, funding_path in sorted(downloaded):
        manifest_rows.append(
            {
                "symbol": symbol,
                "venue": "BINANCE_USDM",
                "bar_interval": INTERVAL,
                "bar_start_ts_ms": values[0].open_ts_ms,
                "bar_end_ts_ms": values[-1].close_ts_ms,
                "bar_count": len(values),
                "bar_file_sha256": _sha256(bar_path),
                "funding_start_ts_ms": rates[0].funding_ts_ms if rates else None,
                "funding_end_ts_ms": rates[-1].funding_ts_ms if rates else None,
                "funding_count": len(rates),
                "funding_file_sha256": _sha256(funding_path),
                "bar_source": BINANCE_FUTURES_KLINES_URL,
                "funding_source": BINANCE_FUTURES_FUNDING_URL,
            }
        )
    return bars, funding, tuple(manifest_rows)


def funding_adjustment(
    rates: Sequence[FundingRate],
    *,
    side: str,
    entry_ts_ms: int,
    exit_ts_ms: int,
    bar_interval_ms: int = INTERVAL_MS,
) -> FundingAdjustment:
    if bar_interval_ms <= 0:
        raise ValueError("펀딩 경계의 봉 시간은 양수여야 합니다.")
    timestamps = [row.funding_ts_ms for row in rates]
    start = bisect.bisect_left(timestamps, entry_ts_ms)
    end = bisect.bisect_right(timestamps, exit_ts_ms)
    direction = 1 if side == "LONG" else -1
    exit_bar_open_ts_ms = exit_ts_ms - bar_interval_ms + 1
    funding_bps = 0.0
    applied = 0
    excluded_credit = 0.0
    excluded_count = 0
    for row in rates[start:end]:
        cashflow_bps = -direction * row.rate * 10_000
        ambiguous_credit = cashflow_bps > 0 and (
            row.funding_ts_ms == entry_ts_ms or row.funding_ts_ms >= exit_bar_open_ts_ms
        )
        if ambiguous_credit:
            excluded_credit += cashflow_bps
            excluded_count += 1
            continue
        funding_bps += cashflow_bps
        applied += 1
    return FundingAdjustment(
        funding_bps=funding_bps,
        applied_event_count=applied,
        excluded_ambiguous_credit_bps=excluded_credit,
        excluded_ambiguous_credit_count=excluded_count,
    )


def apply_actual_funding_and_costs(
    outcome: SlowTrendOutcome,
    rates: Sequence[FundingRate],
    *,
    bar_interval_ms: int = INTERVAL_MS,
) -> tuple[SlowTrendOutcome, FundingAdjustment]:
    if outcome.censored or outcome.gross_bps is None:
        return outcome, FundingAdjustment(0.0, 0, 0.0, 0)
    adjustment = funding_adjustment(
        rates,
        side=outcome.side,
        entry_ts_ms=outcome.entry_ts_ms,
        exit_ts_ms=outcome.exit_ts_ms,
        bar_interval_ms=bar_interval_ms,
    )
    gross_with_funding = outcome.gross_bps + adjustment.funding_bps
    return (
        replace(
            outcome,
            base_net_bps=gross_with_funding - BASE_EXECUTION_COST_BPS,
            stress_net_bps=gross_with_funding - STRESS_EXECUTION_COST_BPS,
        ),
        adjustment,
    )


def research_tournament(
    bars_by_symbol: Mapping[str, Sequence[IntradayBar]],
    funding_by_symbol: Mapping[str, Sequence[FundingRate]],
    specs: Sequence[SlowTrendSpec] = PREREGISTERED_CANDIDATES,
) -> tuple[
    dict[str, tuple[SlowTrendOutcome, ...]],
    dict[str, dict[str, float | int]],
]:
    features_by_symbol = {
        symbol: _features(symbol_bars) for symbol, symbol_bars in sorted(bars_by_symbol.items())
    }
    snapshots = _context_snapshots(bars_by_symbol, features_by_symbol)
    raw: dict[str, list[SlowTrendOutcome]] = {spec.candidate_id: [] for spec in specs}
    for spec in specs:
        for symbol, symbol_bars in sorted(bars_by_symbol.items()):
            raw[spec.candidate_id].extend(
                _symbol_outcomes(
                    symbol_bars,
                    features_by_symbol[symbol],
                    snapshots,
                    spec,
                )
            )
    output: dict[str, tuple[SlowTrendOutcome, ...]] = {}
    audit: dict[str, dict[str, float | int]] = {}
    spec_by_id = {spec.candidate_id: spec for spec in specs}
    for candidate_id, candidate_rows in raw.items():
        selected = apply_portfolio_limits(candidate_rows)
        adjusted: list[SlowTrendOutcome] = []
        total_funding_bps = 0.0
        applied_events = 0
        excluded_credit_bps = 0.0
        excluded_credit_events = 0
        for row in selected:
            revised, funding = apply_actual_funding_and_costs(
                row,
                funding_by_symbol.get(row.symbol, ()),
                bar_interval_ms=(spec_by_id[candidate_id].interval_minutes * 60_000),
            )
            adjusted.append(revised)
            total_funding_bps += funding.funding_bps
            applied_events += funding.applied_event_count
            excluded_credit_bps += funding.excluded_ambiguous_credit_bps
            excluded_credit_events += funding.excluded_ambiguous_credit_count
        output[candidate_id] = tuple(adjusted)
        audit[candidate_id] = {
            "closed_trade_count": sum(not row.censored for row in adjusted),
            "censored_open_count": sum(row.censored for row in adjusted),
            "applied_funding_event_count": applied_events,
            "net_funding_cashflow_bps": total_funding_bps,
            "excluded_ambiguous_boundary_credit_count": excluded_credit_events,
            "excluded_ambiguous_boundary_credit_bps": excluded_credit_bps,
        }
    return output, audit


def select_development_candidates(
    development: Mapping[str, Mapping[str, object]],
    specs: Sequence[SlowTrendSpec] = PREREGISTERED_CANDIDATES,
) -> tuple[str, ...]:
    spec_by_id = {spec.candidate_id: spec for spec in specs}
    eligible = sorted(
        (candidate_id for candidate_id, profile in development.items() if _eligible(profile)),
        key=lambda candidate_id: (*_rank_key(development[candidate_id]), candidate_id),
        reverse=True,
    )
    selected: list[str] = []
    selected_families: set[str] = set()
    for candidate_id in eligible:
        family = spec_by_id[candidate_id].family
        if family in selected_families:
            continue
        selected.append(candidate_id)
        selected_families.add(family)
        if len(selected) == MAXIMUM_FINALISTS:
            break
    return tuple(selected)


def candidate_fingerprint(
    specs: Sequence[SlowTrendSpec] = PREREGISTERED_CANDIDATES,
) -> str:
    payload = [asdict(spec) for spec in specs]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _profile_metric(
    profiles: Mapping[str, Mapping[str, object]],
    candidate_id: str,
    section: str,
    metric: str,
) -> object:
    values = profiles[candidate_id][section]
    if not isinstance(values, Mapping):
        raise TypeError(f"{candidate_id} {section} 성과가 객체가 아닙니다.")
    return values[metric]


def build_report(
    bars_by_symbol: Mapping[str, Sequence[IntradayBar]],
    funding_by_symbol: Mapping[str, Sequence[FundingRate]],
    dataset_manifest: Sequence[Mapping[str, object]],
    *,
    start_ms: int,
    end_ms: int,
    specs: Sequence[SlowTrendSpec] = PREREGISTERED_CANDIDATES,
) -> dict[str, object]:
    outcomes, funding_audit = research_tournament(
        bars_by_symbol,
        funding_by_symbol,
        specs,
    )
    splits = {
        candidate_id: _split(rows, start_ms=start_ms, end_ms=end_ms)
        for candidate_id, rows in outcomes.items()
    }
    development = {
        candidate_id: _development_profile(parts) for candidate_id, parts in splits.items()
    }
    finalists = select_development_candidates(development, specs)
    development_end_ms = start_ms + int((end_ms - start_ms) * 0.70)
    pbo = probability_of_backtest_overfitting(
        _fold_returns(outcomes, start_ms=start_ms, development_end_ms=development_end_ms)
    )
    oos = {
        candidate_id: _oos_assessment(
            candidate_id,
            splits[candidate_id],
            trials=len(specs),
            global_pbo=pbo,
        )
        for candidate_id in finalists
    }
    historical_pass = tuple(
        candidate_id
        for candidate_id, assessment in oos.items()
        if bool(assessment["adaptive_historical_robustness_pass"])
    )
    spec_by_id = {spec.candidate_id: spec for spec in specs}
    ranked_ids = sorted(
        (candidate_id for candidate_id, profile in development.items() if _rankable(profile)),
        key=lambda candidate_id: (*_rank_key(development[candidate_id]), candidate_id),
        reverse=True,
    )
    unranked_ids = sorted(
        candidate_id for candidate_id, profile in development.items() if not _rankable(profile)
    )
    candidates = [asdict(spec) for spec in specs]
    datasets = list(dataset_manifest)
    dataset_hash = hashlib.sha256(
        json.dumps(datasets, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "schema_version": 1,
        "status": (
            "ADAPTIVE_HISTORICAL_PASS_FORWARD_REQUIRED" if historical_pass else "NOT_PROVEN"
        ),
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "private_api_enabled": False,
        "profitability_claim": "NOT_PROVEN",
        "real_money_readiness": "NOT_READY",
        "generated_ts_ms": time.time_ns() // 1_000_000,
        "code_hash": git_commit(),
        "adaptive_boundary": {
            "prior_intraday_and_short_history_results_were_inspected": True,
            "independent_future_oos": False,
            "reason": (
                "앞선 장중·단기 추세 결과를 확인한 뒤 시간축과 비용모형을 바꿨으므로 "
                "마지막 30%도 역사 진단일 뿐 독립 미래표본이 아닙니다."
            ),
        },
        "source": {
            "venue": "BINANCE_USDM",
            "public_only": True,
            "bar_endpoint": BINANCE_FUTURES_KLINES_URL,
            "funding_endpoint": BINANCE_FUTURES_FUNDING_URL,
            "bar_interval": INTERVAL,
            "start_ts_ms": start_ms,
            "end_ts_ms": end_ms,
            "completed_candles_only": True,
            "dataset_hash": dataset_hash,
            "datasets": datasets,
        },
        "research_basis": [
            {
                "title": "Risks and Returns of Cryptocurrency",
                "url": "https://www.nber.org/papers/w24877",
                "use": "암호화폐 time-series momentum을 가설 출처로만 사용",
            },
            {
                "title": (
                    "Technical analysis in cryptocurrency markets: "
                    "Do transaction costs and bubbles matter?"
                ),
                "url": "https://doi.org/10.1016/j.intfin.2022.101601",
                "use": "이동평균·돌파 규칙과 거래비용 민감도를 가설·비용경계에 사용",
            },
            {
                "title": "The Deflated Sharpe Ratio",
                "url": "https://ssrn.com/abstract=2460551",
                "use": "30회 후보시험의 선택편향 보정",
            },
            {
                "title": "Momentum Crashes",
                "url": "https://www.nber.org/papers/w20439",
                "use": "고변동 반전국면의 추세 손실 위험을 명시",
            },
            {
                "title": "Binance USD-M public market data",
                "url": "https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data",
                "use": "공개 4시간봉과 펀딩 이력의 공식 데이터 계약",
            },
        ],
        "preregistration": {
            "hypothesis_id": HYPOTHESIS_ID,
            "path": PREREGISTRATION_PATH,
            "candidate_count": len(specs),
            "family_count": len({spec.family for spec in specs}),
            "candidate_fingerprint": candidate_fingerprint(specs),
            "candidates": candidates,
            "base_execution_cost_bps": BASE_EXECUTION_COST_BPS,
            "stress_execution_cost_bps": STRESS_EXECUTION_COST_BPS,
            "historical_funding_directionally_applied": True,
            "ambiguous_entry_or_exit_bar_funding_credit_excluded": True,
            "next_bar_open_entry": True,
            "same_bar_stop_before_target": True,
            "fixed_maximum_hold": False,
            "censored_open_positions_are_not_scored": True,
            "tp1_fraction": 0.4,
            "maximum_concurrent_positions": 2,
            "maximum_daily_entries": 2,
            "split": "chronological 50% train / 20% validation / 30% diagnostic OOS",
            "embargo_days": 7,
            "thresholds_lowered_after_results": False,
        },
        "funding_cost_audit": funding_audit,
        "development_profiles": development,
        "ranking_contract": {
            "minimum_development_closed_trades": 60,
            "minimum_validation_closed_trades": 20,
            "minimum_oos_closed_trades": 30,
            "sparse_candidates_are_not_ranked": True,
            "maximum_distinct_family_finalists": MAXIMUM_FINALISTS,
        },
        "development_ranking_top_10": [
            {
                "rank": index + 1,
                "candidate_id": candidate_id,
                "family": spec_by_id[candidate_id].family,
                "side_policy": spec_by_id[candidate_id].side_policy,
                "eligible": _eligible(development[candidate_id]),
                "validation_stress_expectancy_bps": (
                    _profile_metric(
                        development,
                        candidate_id,
                        "validation_stress",
                        "expectancy_bps",
                    )
                ),
                "validation_stress_profit_factor": (
                    _profile_metric(
                        development,
                        candidate_id,
                        "validation_stress",
                        "profit_factor",
                    )
                ),
                "development_stress_expectancy_bps": (
                    _profile_metric(
                        development,
                        candidate_id,
                        "stress",
                        "expectancy_bps",
                    )
                ),
            }
            for index, candidate_id in enumerate(ranked_ids[:10])
        ],
        "unranked_insufficient_sample": unranked_ids,
        "selected_on_train_validation": list(finalists),
        "selection_bias": {
            "candidate_trials": len(specs),
            "pbo": pbo,
        },
        "diagnostic_oos": oos,
        "adaptive_historical_robustness_pass_candidates": list(historical_pass),
        "promotion_assessment": {
            "status": "NOT_PROVEN",
            "registry_changes": [],
            "shadow_implementation_candidates": list(historical_pass),
            "future_live_public_forward_samples_required": True,
            "actual_bid_ask_depth_forward_required": True,
            "minimum_natural_base_stress_opportunities_per_strategy": 30,
            "real_orders_remain_forbidden": True,
        },
        "limitations": [
            "고정 12종목은 현재 생존한 대형 종목 중심이라 survivorship bias가 있습니다.",
            "4시간봉은 과거 실행가능 bid·ask 깊이와 봉 내부 가격순서를 제공하지 않습니다.",
            "같은 봉에서 stop과 target이 모두 닿으면 stop을 먼저 적용했습니다.",
            "실제 공개 펀딩은 적용했지만 실행비용은 BASE·STRESS 고정 왕복비용입니다.",
            (
                "역사 통과가 있어도 실제 bid·ask SHADOW와 독립 미래표본 전에는 "
                "수익성이 입증되지 않습니다."
            ),
            (
                "70% 승률은 참고값이며 비용후 기대값·Profit Factor·drawdown·"
                "DSR·PBO를 대신하지 않습니다."
            ),
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="UTC 시작일 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="UTC 종료일 YYYY-MM-DD, 미포함")
    parser.add_argument("--symbol", action="append")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/multiyear-trend-public-v1"),
    )
    parser.add_argument("--output-json", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start_ms = _parse_date(args.start)
    end_ms = _parse_date(args.end)
    if end_ms - start_ms < MINIMUM_RESEARCH_DAYS * 86_400_000:
        raise ValueError(f"다년 추세 토너먼트 기간은 최소 {MINIMUM_RESEARCH_DAYS}일이어야 합니다.")
    symbols = tuple(args.symbol or DEFAULT_SYMBOLS)
    bars, funding, manifest = load_public_research_data(
        symbols,
        start_ms=start_ms,
        end_ms=end_ms,
        cache_dir=args.cache_dir,
    )
    report = build_report(
        bars,
        funding,
        manifest,
        start_ms=start_ms,
        end_ms=end_ms,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output_json is None:
        print(rendered, end="")
        return
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
