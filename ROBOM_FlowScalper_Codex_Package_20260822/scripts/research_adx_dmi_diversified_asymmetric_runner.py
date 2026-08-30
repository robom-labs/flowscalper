# 상승 중인 ADX와 방향 일치 DMI로 횡보 진입을 줄이는 PAPER 추세 후보를 진단한다.

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from backend.app.build_identity import git_commit
from scripts.research_asymmetric_trend_runner_tournament import (
    AsymmetricTrendOutcome,
    AsymmetricTrendSpec,
    asymmetric_candidate_fingerprint,
    research_asymmetric_trend_tournament,
)
from scripts.research_multiyear_trend_tournament import FundingRate
from scripts.research_public_intraday_trend_candidates import IntradayBar
from scripts.research_public_trend_candidates import DEFAULT_SYMBOLS, _parse_date
from scripts.validate_asymmetric_trend_runners_bybit import (
    BYBIT_API_BASE,
    BYBIT_FUNDING_PATH,
    BYBIT_KLINE_PATH,
    FROZEN_EXTERNAL_REPLICATION_CANDIDATES,
    MINIMUM_RESEARCH_DAYS,
    _candidate_assessment,
    _canonical_bytes,
    load_bybit_public_research_data,
)

HYPOTHESIS_ID = "HYP-133-ADX-DMI-DIVERSIFIED-ASYMMETRIC-RUNNER"
PREREGISTRATION_PATH = "docs/research/HYP-133-adx-dmi-diversified-asymmetric-runner.md"
PREREGISTRATION_COMMIT = "b8dd147bd84446b992e68d0ef7c16de5690d3d24"
BASELINE_REPORT_PATH = Path(
    "evidence/WAVE134_BYBIT_ASYMMETRIC_RUNNER_EXTERNAL_REPLICATION.json"
)
DMI_PERIOD = 14
ADX_MINIMUM = 25.0
ADX_RISE_LOOKBACK = 3
REENTRY_COOLDOWN_HOURS = 168
CANDIDATE_SUFFIX = "ADX25_RISE3_DMI_COOLDOWN168H"


@dataclass(frozen=True, slots=True)
class DirectionalMovement:
    plus_di: float
    minus_di: float
    adx: float


def _hyp133_specs() -> tuple[AsymmetricTrendSpec, ...]:
    output: list[AsymmetricTrendSpec] = []
    for source in FROZEN_EXTERNAL_REPLICATION_CANDIDATES:
        source_key = source.candidate_id.removeprefix("T131_")
        output.append(
            replace(
                source,
                candidate_id=f"T133_{source_key}_{CANDIDATE_SUFFIX}",
                entry=replace(source.entry, cooldown_hours=REENTRY_COOLDOWN_HOURS),
            )
        )
    return tuple(output)


PREREGISTERED_ADX_DMI_DIVERSIFIED_CANDIDATES = _hyp133_specs()


def _source_candidate_id(candidate_id: str) -> str:
    suffix = f"_{CANDIDATE_SUFFIX}"
    if not candidate_id.startswith("T133_") or not candidate_id.endswith(suffix):
        raise ValueError(f"HYP-133 후보 ID 형식이 아닙니다. {candidate_id}")
    return f"T131_{candidate_id.removeprefix('T133_').removesuffix(suffix)}"


def _wilder_average(values: Sequence[float], *, period: int) -> tuple[float | None, ...]:
    output: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) <= period:
        return tuple(output)
    current = sum(values[1 : period + 1]) / period
    output[period] = current
    for index in range(period + 1, len(values)):
        current = ((current * (period - 1)) + values[index]) / period
        output[index] = current
    return tuple(output)


def build_directional_movement(
    rows: Sequence[IntradayBar],
    *,
    period: int = DMI_PERIOD,
) -> tuple[DirectionalMovement | None, ...]:
    if period <= 0:
        raise ValueError("DMI period는 양수여야 합니다.")
    if not rows:
        return ()
    true_ranges = [0.0] * len(rows)
    plus_dm = [0.0] * len(rows)
    minus_dm = [0.0] * len(rows)
    for index in range(1, len(rows)):
        current = rows[index]
        previous = rows[index - 1]
        true_ranges[index] = max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        )
        upward = current.high - previous.high
        downward = previous.low - current.low
        plus_dm[index] = upward if upward > downward and upward > 0 else 0.0
        minus_dm[index] = downward if downward > upward and downward > 0 else 0.0

    average_tr = _wilder_average(true_ranges, period=period)
    average_plus = _wilder_average(plus_dm, period=period)
    average_minus = _wilder_average(minus_dm, period=period)
    plus_di: list[float | None] = [None] * len(rows)
    minus_di: list[float | None] = [None] * len(rows)
    dx: list[float | None] = [None] * len(rows)
    for index in range(period, len(rows)):
        tr = average_tr[index]
        plus = average_plus[index]
        minus = average_minus[index]
        if tr is None or plus is None or minus is None or tr <= 0:
            continue
        plus_value = 100 * plus / tr
        minus_value = 100 * minus / tr
        plus_di[index] = plus_value
        minus_di[index] = minus_value
        denominator = plus_value + minus_value
        dx[index] = 0.0 if denominator <= 0 else 100 * abs(plus_value - minus_value) / denominator

    output: list[DirectionalMovement | None] = [None] * len(rows)
    first_adx_index = (period * 2) - 1
    if len(rows) <= first_adx_index:
        return tuple(output)
    seed_values = [value for value in dx[period : first_adx_index + 1] if value is not None]
    if len(seed_values) != period:
        return tuple(output)
    current_adx = sum(seed_values) / period
    for index in range(first_adx_index, len(rows)):
        if index > first_adx_index:
            current_dx = dx[index]
            if current_dx is None:
                continue
            current_adx = ((current_adx * (period - 1)) + current_dx) / period
        plus_value = plus_di[index]
        minus_value = minus_di[index]
        if plus_value is None or minus_value is None:
            continue
        output[index] = DirectionalMovement(
            plus_di=plus_value,
            minus_di=minus_value,
            adx=current_adx,
        )
    return tuple(output)


class AdxDmiSignalGate:
    def __init__(
        self,
        values_by_symbol: Mapping[str, Sequence[DirectionalMovement | None]],
        specs: Sequence[AsymmetricTrendSpec],
    ) -> None:
        self._values_by_symbol = values_by_symbol
        self.audit: dict[str, dict[str, int]] = {
            spec.candidate_id: {
                "original_qualified_count": 0,
                "missing_dmi_count": 0,
                "adx_below_25_count": 0,
                "adx_not_rising_over_3_bars_count": 0,
                "direction_mismatch_count": 0,
                "dmi_gate_pass_count": 0,
                "cooldown_168h_blocked_count": 0,
                "eligible_after_gate_and_cooldown_count": 0,
            }
            for spec in specs
        }

    def __call__(
        self,
        spec: AsymmetricTrendSpec,
        symbol: str,
        index: int,
        direction: int,
    ) -> bool:
        audit = self.audit[spec.candidate_id]
        audit["original_qualified_count"] += 1
        values = self._values_by_symbol.get(symbol, ())
        previous_index = index - ADX_RISE_LOOKBACK
        current = values[index] if 0 <= index < len(values) else None
        previous = values[previous_index] if 0 <= previous_index < len(values) else None
        if current is None or previous is None:
            audit["missing_dmi_count"] += 1
            return False
        if current.adx < ADX_MINIMUM:
            audit["adx_below_25_count"] += 1
            return False
        if current.adx <= previous.adx:
            audit["adx_not_rising_over_3_bars_count"] += 1
            return False
        direction_matches = (
            current.plus_di > current.minus_di
            if direction > 0
            else current.minus_di > current.plus_di
        )
        if not direction_matches:
            audit["direction_mismatch_count"] += 1
            return False
        audit["dmi_gate_pass_count"] += 1
        return True

    def observe_qualified(
        self,
        spec: AsymmetricTrendSpec,
        _symbol: str,
        _index: int,
        _direction: int,
        cooldown_blocked: bool,
    ) -> None:
        key = (
            "cooldown_168h_blocked_count"
            if cooldown_blocked
            else "eligible_after_gate_and_cooldown_count"
        )
        self.audit[spec.candidate_id][key] += 1


def _baseline_summary(
    report: Mapping[str, object] | None,
    *,
    report_path: Path,
) -> dict[str, object]:
    if report is None:
        return {
            "status": "NOT_AVAILABLE",
            "path": str(report_path),
            "used_for_selection": False,
        }
    assessments = report.get("candidate_assessments")
    if not isinstance(assessments, Mapping):
        raise TypeError("HYP-132 baseline candidate_assessments가 객체가 아닙니다.")
    selected: dict[str, object] = {}
    for source in FROZEN_EXTERNAL_REPLICATION_CANDIDATES:
        assessment = assessments.get(source.candidate_id)
        if not isinstance(assessment, Mapping):
            raise KeyError(f"HYP-132 baseline 후보가 없습니다. {source.candidate_id}")
        selected[source.candidate_id] = {
            "base": assessment.get("base"),
            "stress": assessment.get("stress"),
            "bootstrap_expectancy_95": assessment.get("bootstrap_expectancy_95"),
            "deflated_sharpe": assessment.get("deflated_sharpe"),
            "symbol_concentration": assessment.get("symbol_concentration"),
            "positive_skew_profile": assessment.get("positive_skew_profile"),
            "temporal_stability": assessment.get("temporal_stability"),
        }
    source_bytes = report_path.read_bytes() if report_path.exists() else _canonical_bytes(report)
    return {
        "status": "HYP132_RESULT_ALREADY_SEEN_ADAPTIVE_REFERENCE_ONLY",
        "path": str(report_path),
        "sha256": hashlib.sha256(source_bytes).hexdigest(),
        "used_for_selection": False,
        "candidates": selected,
    }


def _diagnostic_assessment(
    candidate_id: str,
    rows: Sequence[AsymmetricTrendOutcome],
    *,
    start_ms: int,
    end_ms: int,
    trials: int,
) -> dict[str, object]:
    assessment = _candidate_assessment(
        candidate_id,
        rows,
        start_ms=start_ms,
        end_ms=end_ms,
        trials=trials,
    )
    gates = assessment.pop("replication_gates")
    passed = assessment.pop("external_venue_replication_pass")
    assessment["diagnostic_gates"] = gates
    assessment["adaptive_development_gate_pass"] = passed
    return assessment


def build_report(
    bars_by_symbol: Mapping[str, Sequence[IntradayBar]],
    funding_by_symbol: Mapping[str, Sequence[FundingRate]],
    manifest: Sequence[Mapping[str, object]],
    *,
    start_ms: int,
    end_ms: int,
    baseline_report: Mapping[str, object] | None = None,
    baseline_report_path: Path = BASELINE_REPORT_PATH,
    specs: Sequence[
        AsymmetricTrendSpec
    ] = PREREGISTERED_ADX_DMI_DIVERSIFIED_CANDIDATES,
) -> dict[str, object]:
    dmi_by_symbol = {
        symbol: build_directional_movement(rows)
        for symbol, rows in sorted(bars_by_symbol.items())
    }
    gate = AdxDmiSignalGate(dmi_by_symbol, specs)
    outcomes, funding_audit = research_asymmetric_trend_tournament(
        bars_by_symbol,
        funding_by_symbol,
        specs,
        signal_gate=gate,
        signal_observer=gate.observe_qualified,
    )
    assessments = {
        candidate_id: _diagnostic_assessment(
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
        if assessment["adaptive_development_gate_pass"] is True
    )
    datasets = list(manifest)
    dataset_hash = hashlib.sha256(_canonical_bytes(datasets)).hexdigest()
    return {
        "schema_version": 1,
        "status": (
            "ADAPTIVE_DIAGNOSTIC_GATE_PASS_INDEPENDENT_CONFIRMATION_REQUIRED"
            if passed
            else "NOT_PROVEN"
        ),
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
        "adaptive_boundary": {
            "hyp132_bybit_results_were_inspected": True,
            "independent_external_confirmation": False,
            "classification": "ADAPTIVE_DEVELOPMENT_DIAGNOSTIC",
            "selection_or_threshold_tuning_after_hyp133_results": False,
            "reason": (
                "HYP-132 Bybit 결과를 본 뒤 횡보와 종목집중 실패를 겨냥해 만든 규칙이므로 "
                "같은 Bybit 표본은 독립 외부검증이 아닙니다."
            ),
        },
        "baseline_reference": _baseline_summary(
            baseline_report,
            report_path=baseline_report_path,
        ),
        "research_basis": [
            {
                "title": "Directional Movement (DMI)",
                "url": "https://www.tradingview.com/support/solutions/43000502250-directional-movement-dmi/",
                "use": "ADX 추세 강도와 +DI·-DI 방향 확인",
            },
            {
                "title": "Average Directional Index (ADX)",
                "url": "https://www.tradingview.com/support/solutions/43000589099-average-directional-index-adx/",
                "use": "고정 ADX 25와 상승 중인 추세 강도 확인",
            },
        ],
        "preregistration": {
            "hypothesis_id": HYPOTHESIS_ID,
            "path": PREREGISTRATION_PATH,
            "commit": PREREGISTRATION_COMMIT,
            "candidate_count": len(specs),
            "candidate_ids": [spec.candidate_id for spec in specs],
            "source_candidate_ids": [
                _source_candidate_id(spec.candidate_id) for spec in specs
            ],
            "candidate_fingerprint": asymmetric_candidate_fingerprint(specs),
            "candidate_parameters": [asdict(spec) for spec in specs],
            "dmi_period": DMI_PERIOD,
            "adx_minimum": ADX_MINIMUM,
            "adx_rise_lookback_completed_bars": ADX_RISE_LOOKBACK,
            "long_requires_plus_di_above_minus_di": True,
            "short_requires_minus_di_above_plus_di": True,
            "reentry_cooldown_hours_same_symbol_any_direction": REENTRY_COOLDOWN_HOURS,
            "minimum_closed_sample": 100,
            "temporal_fold_count": 8,
            "minimum_evaluable_folds": 6,
            "minimum_positive_folds": 5,
            "latest_two_positive_required": True,
            "thresholds_lowered_after_results": False,
            "no_fixed_take_profit": True,
            "no_fixed_maximum_hold": True,
            "no_partial_take_profit": True,
        },
        "signal_filter_audit": gate.audit,
        "funding_cost_risk_audit": funding_audit,
        "candidate_assessments": assessments,
        "adaptive_development_gate_pass_candidates": passed,
        "promotion_assessment": {
            "status": "NOT_PROVEN",
            "registry_changes": [],
            "live_shadow_changes": [],
            "unopened_okx_or_future_live_public_confirmation_required": True,
            "future_bid_ask_depth_base_stress_required": True,
            "minimum_natural_base_stress_opportunities_per_strategy": 30,
            "real_orders_remain_forbidden": True,
        },
        "limitations": [
            (
                "HYP-132 Bybit 결과를 본 뒤 만든 적응 진단이라 같은 자료에서의 개선은 "
                "독립 검증이 아닙니다."
            ),
            "4시간봉에는 당시 실행가능 bid·ask 깊이와 봉 내부 가격순서가 없습니다.",
            "현재 생존 종목 중심이라 survivorship bias가 있습니다.",
            "ADX·DMI는 횡보 손실을 줄일 수 있다는 가설이지 높은 승률이나 수익을 보장하지 않습니다.",
            "독립 OKX 또는 미래 실제 호가 PAPER SHADOW 전에는 운영 후보가 아닙니다.",
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
    parser.add_argument(
        "--baseline-report",
        type=Path,
        default=BASELINE_REPORT_PATH,
    )
    parser.add_argument("--output-json", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start_ms = _parse_date(args.start)
    end_ms = _parse_date(args.end)
    if end_ms - start_ms < MINIMUM_RESEARCH_DAYS * 86_400_000:
        raise ValueError(f"ADX·DMI 추세 진단 기간은 최소 {MINIMUM_RESEARCH_DAYS}일이어야 합니다.")
    symbols = tuple(args.symbol or DEFAULT_SYMBOLS)
    bars, funding, manifest = load_bybit_public_research_data(
        symbols,
        start_ms=start_ms,
        end_ms=end_ms,
        cache_dir=args.cache_dir,
    )
    baseline_report: Mapping[str, object] | None = None
    if args.baseline_report.exists():
        loaded = json.loads(args.baseline_report.read_text(encoding="utf-8"))
        if not isinstance(loaded, Mapping):
            raise TypeError("HYP-132 baseline report가 객체가 아닙니다.")
        baseline_report = loaded
    report = build_report(
        bars,
        funding,
        manifest,
        start_ms=start_ms,
        end_ms=end_ms,
        baseline_report=baseline_report,
        baseline_report_path=args.baseline_report,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output_json is None:
        print(rendered, end="")
        return
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
