# 공개 영상 아이디어를 결정적 규칙으로 재정의한 12개 PAPER 후보를 비교한다.

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from scripts.research_intraday_trend_tournament import load_segmented_public_klines
from scripts.research_public_trend_candidates import DEFAULT_SYMBOLS, Kline, _parse_date
from scripts.research_slow_regime_trend_tournament import (
    MINIMUM_RESEARCH_DAYS,
    SlowTrendSpec,
    _spec,
    build_report,
)


def _candidate_specs() -> tuple[SlowTrendSpec, ...]:
    families = (
        (
            "LIQUIDITY_15M",
            "FIFTEEN_MINUTE_LIQUIDITY_SWEEP_RECLAIM",
            15,
            "LIQUIDITY_SWEEP_RECLAIM",
            {
                "BALANCED": dict(
                    lookback=12,
                    momentum=0.01,
                    rank_threshold=0.58,
                    breadth=0.52,
                    adx=14,
                    relative_volume=0.75,
                    retest_band=0.75,
                    stop_buffer=0.20,
                    tp1_r=1.5,
                    tp2_r=4.0,
                    cooldown_hours=6,
                    slow_alignment=False,
                ),
                "SELECTIVE": dict(
                    lookback=24,
                    momentum=0.025,
                    rank_threshold=0.75,
                    breadth=0.60,
                    adx=20,
                    relative_volume=1.10,
                    retest_band=0.45,
                    stop_buffer=0.30,
                    tp1_r=2.0,
                    tp2_r=5.0,
                    cooldown_hours=10,
                    slow_alignment=True,
                ),
            },
        ),
        (
            "ICHIMOKU_1H",
            "ONE_HOUR_ICHIMOKU_PULLBACK_CONTINUATION",
            60,
            "ICHIMOKU_PULLBACK_CONTINUATION",
            {
                "BALANCED": dict(
                    lookback=52,
                    momentum=0.015,
                    rank_threshold=0.58,
                    breadth=0.52,
                    adx=14,
                    relative_volume=0.65,
                    retest_band=0.50,
                    stop_buffer=0.25,
                    tp1_r=1.5,
                    tp2_r=4.0,
                    cooldown_hours=12,
                    slow_alignment=False,
                ),
                "SELECTIVE": dict(
                    lookback=52,
                    momentum=0.035,
                    rank_threshold=0.75,
                    breadth=0.60,
                    adx=20,
                    relative_volume=0.90,
                    retest_band=0.30,
                    stop_buffer=0.35,
                    tp1_r=2.0,
                    tp2_r=5.0,
                    cooldown_hours=20,
                    slow_alignment=True,
                ),
            },
        ),
    )
    output: list[SlowTrendSpec] = []
    for family_key, family, interval, setup_kind, styles in families:
        for side_policy in ("LONG", "SHORT", "BOTH"):
            for style, parameters in styles.items():
                spec = _spec(
                    family_key,
                    family,
                    interval,
                    setup_kind,
                    side_policy,
                    style,
                    **parameters,
                )
                output.append(
                    replace(
                        spec,
                        candidate_id=spec.candidate_id.replace("T117_", "T118_", 1),
                    )
                )
    return tuple(output)


PREREGISTERED_VIDEO_CANDIDATES = _candidate_specs()


def build_video_report(
    data: Mapping[str, Sequence[Kline]],
    dataset_manifest: Sequence[Mapping[str, object]],
    *,
    start_ms: int,
    end_ms: int,
) -> dict[str, Any]:
    report = build_report(
        data,
        dataset_manifest,
        start_ms=start_ms,
        end_ms=end_ms,
        specs=PREREGISTERED_VIDEO_CANDIDATES,
    )
    report["adaptive_boundary"] = {
        "prior_results_were_inspected": True,
        "independent_future_oos": False,
        "reason": (
            "기존 ROBOM 후보와 공개 TradingView·YouTube 설명을 본 뒤 비중복 규칙만 "
            "사전등록했으므로 이 역사구간은 후보 제거용 적응 진단입니다."
        ),
    }
    source = report["source"]
    assert isinstance(source, dict)
    source["research_intervals"] = ["15m", "1h", "4h market context"]
    preregistration = report["preregistration"]
    assert isinstance(preregistration, dict)
    preregistration.update(
        {
            "hypothesis_id": "HYP-118-PUBLIC-VIDEO-TREND-TOURNAMENT",
            "path": "docs/research/HYP-118-public-video-trend-tournament.md",
            "external_performance_imported": False,
            "pine_or_video_code_copied": False,
            "source_mapping_path": (
                "docs/research/WAVE118_YOUTUBE_TRADINGVIEW_IDEA_MAPPING_KO.md"
            ),
        }
    )
    report["limitations"] = [
        "유튜브·TradingView 게시자의 승률과 수익 주장은 ROBOM 성과로 가져오지 않았습니다.",
        "역사 kline에는 당시 실행가능 bid·ask 깊이가 없어 BASE·STRESS 고정비용을 차감했습니다.",
        "이 결과는 공개 아이디어를 본 뒤 설계한 적응 진단이며 독립 미래 OOS가 아닙니다.",
        "모호한 order block·기관 의도·비공개 지표는 규칙에서 제외했습니다.",
    ]
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="UTC 시작일 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="UTC 종료일 YYYY-MM-DD, 미포함")
    parser.add_argument("--symbol", action="append")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/public-trend-klines-v1"),
    )
    parser.add_argument("--output-json", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start_ms = _parse_date(args.start)
    end_ms = _parse_date(args.end)
    if end_ms - start_ms < MINIMUM_RESEARCH_DAYS * 86_400_000:
        raise ValueError(
            f"공개 영상 후보 토너먼트 기간은 최소 {MINIMUM_RESEARCH_DAYS}일이어야 합니다."
        )
    data, dataset_manifest = load_segmented_public_klines(
        tuple(args.symbol or DEFAULT_SYMBOLS),
        start_ms=start_ms,
        end_ms=end_ms,
        cache_dir=args.cache_dir,
    )
    report = build_video_report(
        data,
        dataset_manifest,
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
