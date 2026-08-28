# 공식 공개 instrument 필터가 최소명목까지 checksum과 함께 fail-closed로 고정되는지 검증한다.

from __future__ import annotations

from copy import deepcopy

from backend.app.research import (
    InstrumentMetadataEvidence,
    build_binance_instrument_manifest,
    load_research_instruments,
)


def _payload() -> dict[str, object]:
    return {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "baseAsset": "BTC",
                "quoteAsset": "USDT",
                "status": "TRADING",
                "contractType": "PERPETUAL",
                "onboardDate": 1_600_000_000_000,
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            },
            {
                "symbol": "BTCUSD_PERP",
                "baseAsset": "BTC",
                "quoteAsset": "USD",
                "status": "TRADING",
                "contractType": "PERPETUAL",
                "filters": [],
            },
        ]
    }


def test_current_public_instrument_snapshot_is_executable_but_not_historical_proof() -> None:
    manifest = build_binance_instrument_manifest(
        _payload(),
        source_bytes_sha256="a" * 64,
        collected_ts_ms=123,
        generated_ts_utc="2026-08-28T00:00:00Z",
    )
    instruments = load_research_instruments(manifest)

    assert manifest["instrument_count"] == 1
    assert manifest["historical_point_in_time_metadata"] is False
    assert manifest["historical_promotion_eligible"] is False
    assert manifest["real_orders_enabled"] is False
    assert set(instruments) == {"BTCUSDT"}
    assert instruments["BTCUSDT"].minimum_notional == 5
    assert instruments["BTCUSDT"].evidence is InstrumentMetadataEvidence.CURRENT_PUBLIC_CONSERVATIVE
    assert instruments["BTCUSDT"].promotion_eligible is False


def test_missing_minimum_notional_filter_fails_closed() -> None:
    payload = _payload()
    symbols = payload["symbols"]
    assert isinstance(symbols, list)
    first = symbols[0]
    assert isinstance(first, dict)
    first["filters"] = [
        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
    ]

    try:
        build_binance_instrument_manifest(
            payload,
            source_bytes_sha256="a" * 64,
            collected_ts_ms=123,
            generated_ts_utc="2026-08-28T00:00:00Z",
        )
    except ValueError as error:
        assert "NOTIONAL" in str(error)
    else:
        raise AssertionError("최소명목 없는 instrument manifest가 생성됐습니다.")


def test_tampered_instrument_manifest_is_rejected_before_execution() -> None:
    manifest = build_binance_instrument_manifest(
        _payload(),
        source_bytes_sha256="a" * 64,
        collected_ts_ms=123,
        generated_ts_utc="2026-08-28T00:00:00Z",
    )
    tampered = deepcopy(manifest)
    instruments = tampered["instruments"]
    assert isinstance(instruments, list)
    row = instruments[0]
    assert isinstance(row, dict)
    row["tick_size"] = "0.01"

    try:
        load_research_instruments(tampered)
    except ValueError as error:
        assert "checksum" in str(error)
    else:
        raise AssertionError("변조된 instrument manifest가 실행에 사용됐습니다.")
