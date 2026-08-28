# 공식 공개 exchangeInfo에서 tick·step·최소수량·최소명목 연구 증거를 고정한다.

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from backend.app.domain.market import Instrument
from backend.app.domain.models import Venue
from backend.app.research.execution import (
    InstrumentMetadataEvidence,
    ResearchInstrumentMetadata,
)


def _positive_decimal(value: object, name: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except Exception as error:
        raise ValueError(f"{name} 공개필터 숫자가 잘못됐습니다.") from error
    if not number.is_finite() or number <= 0:
        raise ValueError(f"{name} 공개필터는 유한한 양수여야 합니다.")
    return number


def _filter(filters: object, name: str) -> Mapping[str, object]:
    if not isinstance(filters, list):
        raise ValueError("exchangeInfo filters가 배열이 아닙니다.")
    for value in filters:
        if isinstance(value, Mapping) and value.get("filterType") == name:
            return value
    raise ValueError(f"필수 공개필터가 없습니다: {name}")


def build_binance_instrument_manifest(
    payload: Mapping[str, object],
    *,
    source_bytes_sha256: str,
    collected_ts_ms: int,
    generated_ts_utc: str,
) -> dict[str, Any]:
    """현재 공개필터를 역사적 point-in-time 증거로 승격하지 않고 보존한다."""

    if (
        len(source_bytes_sha256) != 64
        or any(character not in "0123456789abcdef" for character in source_bytes_sha256)
        or collected_ts_ms < 0
        or not generated_ts_utc
    ):
        raise ValueError("instrument source checksum·수집시각이 잘못됐습니다.")
    symbols = payload.get("symbols")
    if not isinstance(symbols, list):
        raise ValueError("exchangeInfo symbols가 배열이 아닙니다.")
    rows: list[dict[str, object]] = []
    for value in symbols:
        if not isinstance(value, Mapping):
            continue
        if value.get("quoteAsset") != "USDT" or value.get("contractType") != "PERPETUAL":
            continue
        price_filter = _filter(value.get("filters"), "PRICE_FILTER")
        lot_filter = _filter(value.get("filters"), "LOT_SIZE")
        try:
            notional_filter = _filter(value.get("filters"), "MIN_NOTIONAL")
            minimum_notional_raw = notional_filter["notional"]
        except (KeyError, ValueError):
            notional_filter = _filter(value.get("filters"), "NOTIONAL")
            minimum_notional_raw = notional_filter["minNotional"]
        row: dict[str, object] = {
            "symbol": str(value["symbol"]),
            "base_asset": str(value["baseAsset"]),
            "quote_asset": "USDT",
            "status": str(value["status"]),
            "contract_type": "PERPETUAL",
            "tick_size": str(_positive_decimal(price_filter["tickSize"], "tickSize")),
            "quantity_step": str(_positive_decimal(lot_filter["stepSize"], "stepSize")),
            "minimum_quantity": str(_positive_decimal(lot_filter["minQty"], "minQty")),
            "minimum_notional": str(_positive_decimal(minimum_notional_raw, "minimumNotional")),
            "onboard_ts_ms": (int(str(value["onboardDate"])) if value.get("onboardDate") else None),
            "metadata_evidence": InstrumentMetadataEvidence.CURRENT_PUBLIC_CONSERVATIVE.value,
            "historical_promotion_eligible": False,
        }
        rows.append(row)
    rows.sort(key=lambda row: str(row["symbol"]))
    if not rows or len({row["symbol"] for row in rows}) != len(rows):
        raise ValueError("공개 instrument 목록이 비었거나 symbol이 중복됐습니다.")
    material = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "status": "CURRENT_PUBLIC_SNAPSHOT_HISTORICAL_PROMOTION_BLOCKED",
        "generated_ts_utc": generated_ts_utc,
        "collected_ts_ms": collected_ts_ms,
        "venue": Venue.BINANCE_USDM.value,
        "source": {
            "endpoint": "https://fapi.binance.com/fapi/v1/exchangeInfo",
            "authentication": False,
            "response_sha256": source_bytes_sha256,
        },
        "instrument_count": len(rows),
        "historical_point_in_time_metadata": False,
        "historical_promotion_eligible": False,
        "research_execution_allowed": True,
        "instruments_sha256": hashlib.sha256(material.encode()).hexdigest(),
        "instruments": rows,
        "paper_only": True,
        "real_orders_enabled": False,
        "private_api_enabled": False,
    }
    checksum_material = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    manifest["manifest_sha256"] = hashlib.sha256(checksum_material.encode()).hexdigest()
    return manifest


def load_research_instruments(
    manifest: Mapping[str, object],
) -> dict[str, ResearchInstrumentMetadata]:
    normalized_manifest = dict(manifest)
    claimed_manifest_sha = normalized_manifest.pop("manifest_sha256", None)
    actual_manifest_sha = hashlib.sha256(
        json.dumps(
            normalized_manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    if claimed_manifest_sha != actual_manifest_sha:
        raise ValueError("instrument manifest 내부 checksum이 다릅니다.")
    if (
        manifest.get("status") != "CURRENT_PUBLIC_SNAPSHOT_HISTORICAL_PROMOTION_BLOCKED"
        or manifest.get("venue") != Venue.BINANCE_USDM.value
        or manifest.get("research_execution_allowed") is not True
        or manifest.get("historical_promotion_eligible") is not False
        or manifest.get("paper_only") is not True
        or manifest.get("real_orders_enabled") is not False
        or manifest.get("private_api_enabled") is not False
    ):
        raise ValueError("instrument manifest의 PAPER·역사증거 경계가 잘못됐습니다.")
    rows = manifest.get("instruments")
    if not isinstance(rows, list) or not rows:
        raise ValueError("instrument manifest에 공개필터가 없습니다.")
    rows_material = json.dumps(
        rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if manifest.get("instruments_sha256") != hashlib.sha256(rows_material.encode()).hexdigest():
        raise ValueError("instrument 행 checksum이 다릅니다.")
    if manifest.get("instrument_count") != len(rows):
        raise ValueError("instrument 행 수가 manifest와 다릅니다.")
    source = manifest.get("source")
    if not isinstance(source, Mapping):
        raise ValueError("instrument manifest source가 없습니다.")
    source_checksum = str(source.get("response_sha256", ""))
    snapshot_ts_ms = int(str(manifest.get("collected_ts_ms", -1)))
    result: dict[str, ResearchInstrumentMetadata] = {}
    for value in rows:
        if not isinstance(value, Mapping):
            raise ValueError("instrument manifest 행이 object가 아닙니다.")
        symbol = str(value["symbol"])
        if symbol in result:
            raise ValueError("instrument manifest symbol이 중복됐습니다.")
        result[symbol] = ResearchInstrumentMetadata(
            instrument=Instrument(
                venue=Venue(str(manifest["venue"])),
                symbol=symbol,
                base_asset=str(value["base_asset"]),
                quote_asset=str(value["quote_asset"]),
                status=str(value["status"]),
                contract_type=str(value["contract_type"]),
                tick_size=_positive_decimal(value["tick_size"], "tickSize"),
                quantity_step=_positive_decimal(value["quantity_step"], "quantityStep"),
                minimum_quantity=_positive_decimal(value["minimum_quantity"], "minimumQuantity"),
                onboard_ts_ms=(
                    int(str(value["onboard_ts_ms"]))
                    if value.get("onboard_ts_ms") is not None
                    else None
                ),
            ),
            minimum_notional=_positive_decimal(value["minimum_notional"], "minimumNotional"),
            snapshot_ts_ms=snapshot_ts_ms,
            source_checksum=source_checksum,
            evidence=InstrumentMetadataEvidence(str(value["metadata_evidence"])),
        )
    return result
