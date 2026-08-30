# Bybit 외부복제의 고정 후보, 공개자료 파싱과 시간순 gate를 검증한다.

from __future__ import annotations

import time

from scripts.research_asymmetric_trend_runner_tournament import (
    asymmetric_candidate_fingerprint,
)
from scripts.validate_asymmetric_trend_runners_bybit import (
    FROZEN_CANDIDATE_IDS,
    FROZEN_EXTERNAL_REPLICATION_CANDIDATES,
    INTERVAL_MS,
    _parse_funding_rows,
    _parse_kline_rows,
)


def test_external_replication_freezes_exactly_four_hyp131_finalists() -> None:
    specs = FROZEN_EXTERNAL_REPLICATION_CANDIDATES

    assert tuple(spec.candidate_id for spec in specs) == FROZEN_CANDIDATE_IDS
    assert len(specs) == 4
    assert (
        asymmetric_candidate_fingerprint(specs)
        == "8aac503aea1119d7ebe14dd9598a3ed6303d240db015d3ca71854d34a3041cb9"
    )


def test_bybit_kline_parser_sorts_deduplicates_and_excludes_end_boundary() -> None:
    now_ms = time.time_ns() // 1_000_000
    start_ms = now_ms - 10 * INTERVAL_MS
    end_ms = now_ms - INTERVAL_MS
    rows = [
        [str(start_ms + INTERVAL_MS), "101", "103", "99", "102", "5", "0"],
        [str(start_ms), "100", "102", "98", "101", "4", "0"],
        [str(start_ms), "100", "102", "98", "101", "4", "0"],
        [str(end_ms), "999", "999", "999", "999", "1", "0"],
    ]

    parsed = _parse_kline_rows(
        "BTCUSDT",
        rows,
        start_ms=start_ms,
        end_ms=end_ms,
    )

    assert [row.open_ts_ms for row in parsed] == [
        start_ms,
        start_ms + INTERVAL_MS,
    ]
    assert parsed[0].open == 100
    assert parsed[1].close == 102


def test_bybit_kline_parser_excludes_unfinished_candle() -> None:
    now_ms = time.time_ns() // 1_000_000
    rows = [[str(now_ms - 1), "100", "101", "99", "100", "5", "0"]]

    parsed = _parse_kline_rows(
        "BTCUSDT",
        rows,
        start_ms=now_ms - INTERVAL_MS,
        end_ms=now_ms + INTERVAL_MS,
    )

    assert parsed == ()


def test_bybit_funding_parser_sorts_deduplicates_and_excludes_end() -> None:
    rows = [
        {
            "symbol": "BTCUSDT",
            "fundingRate": "0.0002",
            "fundingRateTimestamp": "200",
        },
        {
            "symbol": "BTCUSDT",
            "fundingRate": "0.0001",
            "fundingRateTimestamp": "100",
        },
        {
            "symbol": "BTCUSDT",
            "fundingRate": "0.0003",
            "fundingRateTimestamp": "300",
        },
    ]

    parsed = _parse_funding_rows(
        "BTCUSDT",
        rows,
        start_ms=100,
        end_ms=300,
    )

    assert [row.funding_ts_ms for row in parsed] == [100, 200]
    assert [row.rate for row in parsed] == [0.0001, 0.0002]
