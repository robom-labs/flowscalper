# OKX 외부복제 후보, 공개자료 파싱과 사전등록 경계를 검증한다.

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from scripts.research_asymmetric_trend_runner_tournament import (
    asymmetric_candidate_fingerprint,
)
from scripts.validate_adx_dmi_asymmetric_runners_okx import (
    PREREGISTERED_OKX_REPLICATION_CANDIDATES,
    _funding_link_requests,
    _okx_instrument,
    _parse_funding_csv,
    _parse_kline_rows,
)


def _timestamp(year: int, month: int, day: int = 1) -> int:
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


def test_hyp134_freezes_four_okx_candidates_without_changing_rules() -> None:
    specs = PREREGISTERED_OKX_REPLICATION_CANDIDATES

    assert [spec.candidate_id for spec in specs] == [
        "T134_OKX_OBV_MA_CROSS_4H_BOTH_BALANCED_CHAND22_ATR3_ADX25_RISE3_DMI_COOLDOWN168H",
        "T134_OKX_OBV_PRICE_BREAKOUT_4H_BOTH_BALANCED_CHAND22_ATR3_ADX25_RISE3_DMI_COOLDOWN168H",
        "T134_OKX_SQUEEZE_BREAKOUT_4H_BOTH_BALANCED_CHAND22_ATR4_ADX25_RISE3_DMI_COOLDOWN168H",
        "T134_OKX_OBV_FIRST_PULLBACK_4H_BOTH_BALANCED_CHAND22_ATR4_ADX25_RISE3_DMI_COOLDOWN168H",
    ]
    assert all(spec.entry.cooldown_hours == 168 for spec in specs)
    assert len(asymmetric_candidate_fingerprint(specs)) == 64


def test_okx_kline_parser_uses_only_completed_rows_and_base_volume() -> None:
    rows = [
        ["200", "101", "104", "99", "103", "50", "7", "721", "1"],
        ["100", "100", "102", "98", "101", "40", "6", "606", "1"],
        ["300", "103", "105", "102", "104", "30", "5", "520", "0"],
    ]

    parsed = _parse_kline_rows("BTCUSDT", rows, start_ms=100, end_ms=400)

    assert [row.open_ts_ms for row in parsed] == [100, 200]
    assert parsed[0].volume == 6
    assert parsed[1].close == 103


def test_okx_kline_parser_rejects_conflicting_duplicate() -> None:
    rows = [
        ["100", "100", "102", "98", "101", "40", "6", "606", "1"],
        ["100", "100", "103", "98", "101", "40", "6", "606", "1"],
    ]

    with pytest.raises(ValueError, match="Conflicting OKX kline duplicate"):
        _parse_kline_rows("BTCUSDT", rows, start_ms=100, end_ms=200)


def test_okx_funding_parser_sorts_filters_and_checks_instrument() -> None:
    payload = (
        b"instrument_name,funding_rate,funding_time\n"
        b"BTC-USDT-SWAP,0.0002,200\n"
        b"ETH-USDT-SWAP,0.9999,150\n"
        b"BTC-USDT-SWAP,0.0001,100\n"
        b"BTC-USDT-SWAP,0.0003,300\n"
    )

    parsed = _parse_funding_csv("BTCUSDT", payload, start_ms=100, end_ms=300)

    assert [row.funding_ts_ms for row in parsed] == [100, 200]
    assert [row.rate for row in parsed] == [0.0001, 0.0002]


def test_funding_download_ranges_obey_official_monthly_and_daily_limits() -> None:
    ranges = _funding_link_requests(
        _timestamp(2023, 7),
        _timestamp(2026, 8, 30),
    )

    monthly = [row for row in ranges if row[0] == "monthly"]
    daily = [row for row in ranges if row[0] == "daily"]
    assert monthly
    assert daily
    assert all(end - start < 154 * 86_400_000 for _, start, end in monthly)
    assert all(end - start < 6 * 86_400_000 for _, start, end in daily)
    assert monthly[0][1] == _timestamp(2023, 7)
    assert daily[-1][2] == _timestamp(2026, 8, 30) - 1


def test_okx_symbol_mapping_is_public_usdt_swap_only() -> None:
    assert _okx_instrument("SOLUSDT") == "SOL-USDT-SWAP"
    with pytest.raises(ValueError, match="USDT"):
        _okx_instrument("BTCUSD")
