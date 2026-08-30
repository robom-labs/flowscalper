# Binance에서 선발된 비대칭 추세 runner를 Bybit 공개 perpetual 자료에 무변경 복제한다.

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import cast

import httpx

from backend.app.build_identity import git_commit
from backend.app.research import bootstrap_mean_interval, deflated_sharpe_ratio
from scripts.research_asymmetric_trend_runner_tournament import (
    PREREGISTERED_ASYMMETRIC_TREND_CANDIDATES,
    AsymmetricTrendOutcome,
    AsymmetricTrendSpec,
    _positive_skew_profile,
    asymmetric_candidate_fingerprint,
    research_asymmetric_trend_tournament,
)
from scripts.research_multiyear_trend_tournament import FundingRate
from scripts.research_public_intraday_trend_candidates import IntradayBar
from scripts.research_public_trend_candidates import DEFAULT_SYMBOLS, _parse_date
from scripts.research_slow_regime_trend_tournament import (
    SlowTrendOutcome,
    _concentration,
    _profile,
)

BYBIT_API_BASE = "https://api.bybit.com"
BYBIT_KLINE_PATH = "/v5/market/kline"
BYBIT_FUNDING_PATH = "/v5/market/funding/history"
INTERVAL_MINUTES = 240
INTERVAL_MS = INTERVAL_MINUTES * 60_000
MINIMUM_RESEARCH_DAYS = 1_095
HYPOTHESIS_ID = "HYP-132-BYBIT-ASYMMETRIC-RUNNER-EXTERNAL-REPLICATION"
PREREGISTRATION_PATH = "docs/research/HYP-132-bybit-asymmetric-runner-external-replication.md"
FROZEN_CANDIDATE_IDS = (
    "T131_OBV_MA_CROSS_4H_BOTH_BALANCED_CHAND22_ATR3",
    "T131_OBV_PRICE_BREAKOUT_4H_BOTH_BALANCED_CHAND22_ATR3",
    "T131_SQUEEZE_BREAKOUT_4H_BOTH_BALANCED_CHAND22_ATR4",
    "T131_OBV_FIRST_PULLBACK_4H_BOTH_BALANCED_CHAND22_ATR4",
)


def _frozen_specs() -> tuple[AsymmetricTrendSpec, ...]:
    by_id = {spec.candidate_id: spec for spec in PREREGISTERED_ASYMMETRIC_TREND_CANDIDATES}
    return tuple(by_id[candidate_id] for candidate_id in FROZEN_CANDIDATE_IDS)


FROZEN_EXTERNAL_REPLICATION_CANDIDATES = _frozen_specs()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _request_public(
    client: httpx.Client,
    path: str,
    params: Mapping[str, str | int],
) -> Mapping[str, object]:
    delay = 0.25
    for attempt in range(6):
        try:
            response = client.get(path, params=params)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, Mapping):
                raise TypeError("Bybit 응답이 객체가 아닙니다.")
            ret_code = payload.get("retCode")
            if ret_code == 0:
                return payload
            if ret_code not in {10006, 10016}:
                raise RuntimeError(f"Bybit 공개 API 오류입니다. {ret_code} {payload.get('retMsg')}")
        except (httpx.HTTPError, ValueError, TypeError, RuntimeError):
            if attempt == 5:
                raise
        time.sleep(delay)
        delay = min(delay * 2, 4.0)
    raise AssertionError("도달할 수 없는 Bybit 재시도 경로입니다.")


def _result_list(payload: Mapping[str, object]) -> list[object]:
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise TypeError("Bybit result가 객체가 아닙니다.")
    rows = result.get("list")
    if not isinstance(rows, list):
        raise TypeError("Bybit result.list가 배열이 아닙니다.")
    return rows


def _parse_kline_rows(
    symbol: str,
    rows: Sequence[object],
    *,
    start_ms: int,
    end_ms: int,
) -> tuple[IntradayBar, ...]:
    parsed: dict[int, IntradayBar] = {}
    completed_before = min(end_ms, time.time_ns() // 1_000_000)
    for raw in rows:
        if not isinstance(raw, list) or len(raw) < 6:
            raise TypeError("Bybit kline 행 형식이 잘못됐습니다.")
        open_ts_ms = int(raw[0])
        if not start_ms <= open_ts_ms < end_ms:
            continue
        if open_ts_ms + INTERVAL_MS > completed_before:
            continue
        parsed[open_ts_ms] = IntradayBar(
            symbol=symbol,
            interval_minutes=INTERVAL_MINUTES,
            open_ts_ms=open_ts_ms,
            open=float(raw[1]),
            high=float(raw[2]),
            low=float(raw[3]),
            close=float(raw[4]),
            volume=float(raw[5]),
        )
    return tuple(parsed[key] for key in sorted(parsed))


def _parse_funding_rows(
    symbol: str,
    rows: Sequence[object],
    *,
    start_ms: int,
    end_ms: int,
) -> tuple[FundingRate, ...]:
    parsed: dict[int, FundingRate] = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise TypeError("Bybit funding 행 형식이 잘못됐습니다.")
        timestamp = int(raw["fundingRateTimestamp"])
        if start_ms <= timestamp < end_ms:
            parsed[timestamp] = FundingRate(
                symbol=symbol,
                funding_ts_ms=timestamp,
                rate=float(raw["fundingRate"]),
            )
    return tuple(parsed[key] for key in sorted(parsed))


def _download_klines(
    client: httpx.Client,
    symbol: str,
    *,
    start_ms: int,
    end_ms: int,
) -> tuple[IntradayBar, ...]:
    cursor = end_ms - 1
    raw_rows: list[object] = []
    while cursor >= start_ms:
        payload = _request_public(
            client,
            BYBIT_KLINE_PATH,
            {
                "category": "linear",
                "symbol": symbol,
                "interval": "240",
                "start": start_ms,
                "end": cursor,
                "limit": 1000,
            },
        )
        page = _result_list(payload)
        if not page:
            break
        raw_rows.extend(page)
        timestamps = [int(row[0]) for row in page if isinstance(row, list) and len(row) >= 1]
        if not timestamps:
            raise ValueError("Bybit kline page에 timestamp가 없습니다.")
        oldest = min(timestamps)
        if oldest <= start_ms:
            break
        if oldest > cursor:
            raise ValueError("Bybit kline pagination cursor가 전진했습니다.")
        cursor = oldest - 1
    return _parse_kline_rows(
        symbol,
        raw_rows,
        start_ms=start_ms,
        end_ms=end_ms,
    )


def _download_funding(
    client: httpx.Client,
    symbol: str,
    *,
    start_ms: int,
    end_ms: int,
) -> tuple[FundingRate, ...]:
    cursor = end_ms - 1
    raw_rows: list[object] = []
    while cursor >= start_ms:
        payload = _request_public(
            client,
            BYBIT_FUNDING_PATH,
            {
                "category": "linear",
                "symbol": symbol,
                "startTime": start_ms,
                "endTime": cursor,
                "limit": 200,
            },
        )
        page = _result_list(payload)
        if not page:
            break
        raw_rows.extend(page)
        timestamps = [
            int(row["fundingRateTimestamp"])
            for row in page
            if isinstance(row, Mapping) and "fundingRateTimestamp" in row
        ]
        if not timestamps:
            raise ValueError("Bybit funding page에 timestamp가 없습니다.")
        oldest = min(timestamps)
        if oldest <= start_ms:
            break
        if oldest > cursor:
            raise ValueError("Bybit funding pagination cursor가 전진했습니다.")
        cursor = oldest - 1
    return _parse_funding_rows(
        symbol,
        raw_rows,
        start_ms=start_ms,
        end_ms=end_ms,
    )


def _cache_paths(
    cache_dir: Path,
    symbol: str,
    *,
    start_ms: int,
    end_ms: int,
) -> tuple[Path, Path]:
    key = f"{symbol}-{start_ms}-{end_ms}"
    return (
        cache_dir / f"{key}-4h-klines.json",
        cache_dir / f"{key}-funding.json",
    )


def _load_or_download_symbol(
    symbol: str,
    *,
    start_ms: int,
    end_ms: int,
    cache_dir: Path,
) -> tuple[str, tuple[IntradayBar, ...], tuple[FundingRate, ...], dict[str, object]]:
    kline_path, funding_path = _cache_paths(
        cache_dir,
        symbol,
        start_ms=start_ms,
        end_ms=end_ms,
    )
    if kline_path.exists() and funding_path.exists():
        kline_payload = json.loads(kline_path.read_text(encoding="utf-8"))
        funding_payload = json.loads(funding_path.read_text(encoding="utf-8"))
        if not isinstance(kline_payload, list) or not isinstance(funding_payload, list):
            raise TypeError("Bybit cache가 배열이 아닙니다.")
        bars = _parse_kline_rows(
            symbol,
            kline_payload,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        funding = _parse_funding_rows(
            symbol,
            funding_payload,
            start_ms=start_ms,
            end_ms=end_ms,
        )
    else:
        with httpx.Client(
            base_url=BYBIT_API_BASE,
            timeout=20.0,
            headers={"User-Agent": "ROBOM-FlowScalper-Public-Paper-Research/0.2"},
        ) as client:
            bars = _download_klines(
                client,
                symbol,
                start_ms=start_ms,
                end_ms=end_ms,
            )
            funding = _download_funding(
                client,
                symbol,
                start_ms=start_ms,
                end_ms=end_ms,
            )
        kline_payload = [
            [
                str(row.open_ts_ms),
                str(row.open),
                str(row.high),
                str(row.low),
                str(row.close),
                str(row.volume),
            ]
            for row in bars
        ]
        funding_payload = [
            {
                "symbol": row.symbol,
                "fundingRate": str(row.rate),
                "fundingRateTimestamp": str(row.funding_ts_ms),
            }
            for row in funding
        ]
        cache_dir.mkdir(parents=True, exist_ok=True)
        kline_path.write_text(
            json.dumps(kline_payload, separators=(",", ":")),
            encoding="utf-8",
        )
        funding_path.write_text(
            json.dumps(funding_payload, separators=(",", ":")),
            encoding="utf-8",
        )
    if len(bars) < 205:
        raise ValueError(f"{symbol} Bybit 완성 4시간봉이 205개 미만입니다.")
    gap_count = sum(
        current.open_ts_ms - previous.open_ts_ms != INTERVAL_MS
        for previous, current in zip(bars, bars[1:], strict=False)
    )
    kline_bytes = _canonical_bytes(kline_payload)
    funding_bytes = _canonical_bytes(funding_payload)
    manifest = {
        "venue": "BYBIT_LINEAR",
        "symbol": symbol,
        "bar_interval": "4h",
        "bar_count": len(bars),
        "bar_start_ts_ms": bars[0].open_ts_ms,
        "bar_end_ts_ms": bars[-1].open_ts_ms + INTERVAL_MS,
        "bar_gap_count": gap_count,
        "bar_sha256": hashlib.sha256(kline_bytes).hexdigest(),
        "funding_count": len(funding),
        "funding_start_ts_ms": funding[0].funding_ts_ms if funding else None,
        "funding_end_ts_ms": funding[-1].funding_ts_ms if funding else None,
        "funding_sha256": hashlib.sha256(funding_bytes).hexdigest(),
        "public_only": True,
        "api_key_used": False,
    }
    return symbol, bars, funding, manifest


def load_bybit_public_research_data(
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
    with ThreadPoolExecutor(max_workers=min(4, len(symbols))) as executor:
        loaded = list(
            executor.map(
                lambda symbol: _load_or_download_symbol(
                    symbol,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    cache_dir=cache_dir,
                ),
                symbols,
            )
        )
    bars = {symbol: rows for symbol, rows, _, _ in loaded}
    funding = {symbol: rows for symbol, _, rows, _ in loaded}
    manifest = [row for _, _, _, row in loaded]
    return bars, funding, manifest


def _temporal_profile(
    rows: Sequence[AsymmetricTrendOutcome],
    *,
    start_ms: int,
    end_ms: int,
) -> dict[str, object]:
    folds: list[dict[str, object]] = []
    positive_flags: list[bool] = []
    evaluable_flags: list[bool] = []
    for index in range(8):
        fold_start = start_ms + (end_ms - start_ms) * index // 8
        fold_end = start_ms + (end_ms - start_ms) * (index + 1) // 8
        selected = [
            row
            for row in rows
            if not row.censored and row.entry_ts_ms >= fold_start and row.exit_ts_ms < fold_end
        ]
        stress = _profile(
            cast(Sequence[SlowTrendOutcome], selected),
            "stress_net_bps",
        )
        sample_value = stress["sample_size"]
        if not isinstance(sample_value, int):
            raise TypeError("표본 크기는 정수여야 합니다.")
        sample_size = sample_value
        expectancy = stress["expectancy_bps"]
        profit_factor = stress["profit_factor"]
        evaluable = sample_size >= 10
        positive = (
            evaluable
            and isinstance(expectancy, int | float)
            and expectancy > 0
            and isinstance(profit_factor, int | float)
            and profit_factor > 1
        )
        evaluable_flags.append(evaluable)
        positive_flags.append(positive)
        folds.append(
            {
                "fold": index + 1,
                "start_ts_ms": fold_start,
                "end_ts_ms": fold_end,
                "stress": stress,
                "evaluable": evaluable,
                "positive": positive,
            }
        )
    return {
        "folds": folds,
        "evaluable_fold_count": sum(evaluable_flags),
        "positive_fold_count": sum(positive_flags),
        "latest_two_folds_positive": all(positive_flags[-2:]),
        "stability_pass": (
            sum(evaluable_flags) >= 6 and sum(positive_flags) >= 5 and all(positive_flags[-2:])
        ),
    }


def _candidate_assessment(
    candidate_id: str,
    rows: Sequence[AsymmetricTrendOutcome],
    *,
    start_ms: int,
    end_ms: int,
    trials: int,
) -> dict[str, object]:
    closed = tuple(row for row in rows if not row.censored)
    base = _profile(cast(Sequence[SlowTrendOutcome], closed), "base_net_bps")
    stress = _profile(cast(Sequence[SlowTrendOutcome], closed), "stress_net_bps")
    base_values = [float(row.base_net_bps) for row in closed if row.base_net_bps is not None]
    bootstrap = bootstrap_mean_interval(base_values, seed=20260830)
    dsr = deflated_sharpe_ratio(base_values, trials=trials)
    concentration = _concentration(cast(Sequence[SlowTrendOutcome], closed))
    skew = _positive_skew_profile(closed)
    temporal = _temporal_profile(closed, start_ms=start_ms, end_ms=end_ms)
    share = concentration["largest_positive_contribution_share"]
    payoff = stress["payoff_ratio"]
    stress_expectancy = stress["expectancy_bps"]
    base_expectancy = base["expectancy_bps"]
    base_pf = base["profit_factor"]
    stress_pf = stress["profit_factor"]
    lower = bootstrap["lower"]
    dsr_probability = dsr["dsr_probability"]
    skewness = skew["stress_return_skewness"]
    maximum_winner = skew["maximum_winner_gross_r"]
    gates = {
        "sample_at_least_100": len(closed) >= 100,
        "base_expectancy_positive": (
            isinstance(base_expectancy, int | float) and base_expectancy > 0
        ),
        "stress_expectancy_positive": (
            isinstance(stress_expectancy, int | float) and stress_expectancy > 0
        ),
        "base_profit_factor_at_least_1_15": (isinstance(base_pf, int | float) and base_pf >= 1.15),
        "stress_profit_factor_at_least_1_05": (
            isinstance(stress_pf, int | float) and stress_pf >= 1.05
        ),
        "stress_payoff_at_least_1_50": (isinstance(payoff, int | float) and payoff >= 1.50),
        "stress_return_skewness_positive": (isinstance(skewness, int | float) and skewness > 0),
        "maximum_winner_at_least_3r": (
            isinstance(maximum_winner, int | float) and maximum_winner >= 3
        ),
        "bootstrap_lower_positive": (isinstance(lower, int | float) and lower > 0),
        "dsr_at_least_0_95": (isinstance(dsr_probability, int | float) and dsr_probability >= 0.95),
        "largest_symbol_share_at_most_0_50": (isinstance(share, int | float) and share <= 0.50),
        "temporal_stability": temporal["stability_pass"] is True,
    }
    return {
        "candidate_id": candidate_id,
        "base": base,
        "stress": stress,
        "bootstrap_expectancy_95": bootstrap,
        "deflated_sharpe": dsr,
        "symbol_concentration": concentration,
        "positive_skew_profile": skew,
        "temporal_stability": temporal,
        "replication_gates": gates,
        "external_venue_replication_pass": all(gates.values()),
        "examples": [asdict(row) for row in closed[:20]],
    }


def build_report(
    bars_by_symbol: Mapping[str, Sequence[IntradayBar]],
    funding_by_symbol: Mapping[str, Sequence[FundingRate]],
    manifest: Sequence[Mapping[str, object]],
    *,
    start_ms: int,
    end_ms: int,
    specs: Sequence[AsymmetricTrendSpec] = FROZEN_EXTERNAL_REPLICATION_CANDIDATES,
) -> dict[str, object]:
    outcomes, funding_audit = research_asymmetric_trend_tournament(
        bars_by_symbol,
        funding_by_symbol,
        specs,
    )
    assessments = {
        candidate_id: _candidate_assessment(
            candidate_id,
            rows,
            start_ms=start_ms,
            end_ms=end_ms,
            trials=len(specs),
        )
        for candidate_id, rows in outcomes.items()
    }
    passed = sorted(
        candidate_id
        for candidate_id, assessment in assessments.items()
        if assessment["external_venue_replication_pass"] is True
    )
    datasets = list(manifest)
    dataset_hash = hashlib.sha256(_canonical_bytes(datasets)).hexdigest()
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
            "venue": "BYBIT_LINEAR",
            "public_only": True,
            "api_key_used": False,
            "kline_url": f"{BYBIT_API_BASE}{BYBIT_KLINE_PATH}",
            "funding_url": f"{BYBIT_API_BASE}{BYBIT_FUNDING_PATH}",
            "bar_interval": "4h",
            "start_ts_ms": start_ms,
            "end_ts_ms": end_ms,
            "completed_candles_only": True,
            "dataset_hash": dataset_hash,
            "datasets": datasets,
        },
        "replication_boundary": {
            "candidate_rules_changed": False,
            "selection_or_ranking_on_bybit": False,
            "binance_oos_was_seen_before_this_test": True,
            "fully_independent_market_regime": False,
            "reason": (
                "거래소·거래량·펀딩·가격경로는 다르지만 동일 암호자산 시장 충격이 강하게 "
                "연결돼 완전 독립 미래표본은 아닙니다."
            ),
        },
        "preregistration": {
            "hypothesis_id": HYPOTHESIS_ID,
            "path": PREREGISTRATION_PATH,
            "candidate_count": len(specs),
            "candidate_ids": [spec.candidate_id for spec in specs],
            "candidate_fingerprint": asymmetric_candidate_fingerprint(specs),
            "candidate_parameters": [asdict(spec) for spec in specs],
            "selection_after_bybit_results_forbidden": True,
            "minimum_closed_sample": 100,
            "temporal_fold_count": 8,
            "minimum_evaluable_folds": 6,
            "minimum_positive_folds": 5,
            "latest_two_positive_required": True,
            "thresholds_lowered_after_results": False,
        },
        "funding_cost_risk_audit": funding_audit,
        "candidate_assessments": assessments,
        "external_venue_replication_pass_candidates": passed,
        "promotion_assessment": {
            "registry_changes": [],
            "future_live_public_bid_ask_shadow_required": True,
            "minimum_natural_base_stress_opportunities_per_strategy": 30,
            "real_orders_remain_forbidden": True,
        },
        "limitations": [
            "Bybit와 Binance는 같은 암호자산 시장 충격에 연결돼 완전 독립표본이 아닙니다.",
            "4시간봉에는 당시 실행가능 bid·ask 깊이와 봉 내부 가격순서가 없습니다.",
            "현재 생존 종목 중심이라 survivorship bias가 있습니다.",
            "외부 venue 통과도 실제 호가 기반 미래 PAPER SHADOW를 대신하지 않습니다.",
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
        default=Path("data/bybit-asymmetric-runner-public-v1"),
    )
    parser.add_argument("--output-json", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start_ms = _parse_date(args.start)
    end_ms = _parse_date(args.end)
    if end_ms - start_ms < MINIMUM_RESEARCH_DAYS * 86_400_000:
        raise ValueError(f"Bybit 외부복제 기간은 최소 {MINIMUM_RESEARCH_DAYS}일이어야 합니다.")
    symbols = tuple(args.symbol or DEFAULT_SYMBOLS)
    bars, funding, manifest = load_bybit_public_research_data(
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
