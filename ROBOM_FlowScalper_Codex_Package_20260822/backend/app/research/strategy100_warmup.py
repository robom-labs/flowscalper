# 공식 공개 5분봉을 100후보 연구의 과거 완료봉 워밍업으로만 공급한다.

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from backend.app.market_data import Candle

BASE_INTERVAL_SECONDS = 300
WARMUP_INTERVALS = (300, 900, 3_600, 14_400, 21_600)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _manifest_checksum(manifest: Mapping[str, object]) -> str:
    material = dict(manifest)
    material.pop("manifest_sha256", None)
    return hashlib.sha256(_canonical_json(material).encode()).hexdigest()


def _resolve_project_path(path: str, *, manifest_path: Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.resolve(strict=True)
    project_root = manifest_path.resolve(strict=True).parent.parent
    return (project_root / candidate).resolve(strict=True)


@dataclass(frozen=True, slots=True)
class PublicWarmupBar:
    symbol: str
    open_ts_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    trade_count: int
    taker_buy_volume: Decimal
    taker_buy_quote_volume: Decimal

    @property
    def close_ts_ms(self) -> int:
        return self.open_ts_ms + BASE_INTERVAL_SECONDS * 1_000


class FrozenStrategy100Warmup:
    """Checksum으로 동결된 공개봉만 읽고 의사결정시각 이후 봉은 제외한다."""

    def __init__(
        self,
        *,
        manifest_path: Path,
        manifest: Mapping[str, object],
        candles_by_key: Mapping[tuple[str, int], Sequence[Candle]],
        manifest_file_sha256: str,
    ) -> None:
        self.manifest_path = manifest_path.resolve(strict=True)
        self.manifest = dict(manifest)
        self.manifest_sha256 = str(manifest["manifest_sha256"])
        self.manifest_file_sha256 = manifest_file_sha256
        self._candles_by_key = {
            key: tuple(values) for key, values in candles_by_key.items()
        }

    @classmethod
    def load(cls, manifest_path: Path) -> FrozenStrategy100Warmup:
        resolved_manifest = manifest_path.resolve(strict=True)
        manifest_bytes = resolved_manifest.read_bytes()
        manifest: dict[str, Any] = json.loads(manifest_bytes)
        if (
            manifest.get("status") != "FROZEN_PUBLIC_KLINE_WARMUP"
            or manifest.get("paper_only") is not True
            or manifest.get("real_orders_enabled") is not False
            or manifest.get("private_api_enabled") is not False
            or manifest.get("auth_required") is not False
            or manifest.get("manifest_sha256") != _manifest_checksum(manifest)
        ):
            raise ValueError("100후보 공개봉 워밍업 manifest 계약이 잘못됐습니다.")
        cache_root = _resolve_project_path(
            str(manifest.get("cache_root", "")), manifest_path=resolved_manifest
        )
        rows = manifest.get("files")
        if not isinstance(rows, list) or not rows:
            raise ValueError("100후보 공개봉 워밍업 파일 목록이 없습니다.")
        all_candles: dict[tuple[str, int], list[Candle]] = defaultdict(list)
        seen_symbols: set[str] = set()
        cutoff_ts_ms = int(str(manifest.get("cutoff_ts_ms", -1)))
        for file_row in rows:
            if not isinstance(file_row, Mapping):
                raise ValueError("100후보 공개봉 워밍업 파일 행이 잘못됐습니다.")
            symbol = str(file_row.get("symbol", ""))
            relative_path = Path(str(file_row.get("relative_path", "")))
            path = (cache_root / relative_path).resolve(strict=True)
            if not path.is_relative_to(cache_root):
                raise ValueError("100후보 공개봉 파일이 cache root 밖에 있습니다.")
            payload_bytes = path.read_bytes()
            if hashlib.sha256(payload_bytes).hexdigest() != file_row.get("file_sha256"):
                raise ValueError(f"100후보 공개봉 파일 checksum이 다릅니다: {path}")
            payload = json.loads(payload_bytes)
            bars = cls._parse_file_rows(
                payload,
                symbol=symbol,
                expected_count=int(str(file_row.get("bar_count", -1))),
                cutoff_ts_ms=cutoff_ts_ms,
            )
            seen_symbols.add(symbol)
            for candle in cls._aggregate_symbol(bars, cutoff_ts_ms=cutoff_ts_ms):
                all_candles[(candle.symbol, candle.interval_seconds)].append(candle)
        expected_symbols = manifest.get("symbols")
        if (
            not isinstance(expected_symbols, list)
            or seen_symbols != {str(value) for value in expected_symbols}
        ):
            raise ValueError("100후보 공개봉 워밍업 종목 목록이 다릅니다.")
        return cls(
            manifest_path=resolved_manifest,
            manifest=manifest,
            candles_by_key=all_candles,
            manifest_file_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        )

    @staticmethod
    def _parse_file_rows(
        payload: object,
        *,
        symbol: str,
        expected_count: int,
        cutoff_ts_ms: int,
    ) -> tuple[PublicWarmupBar, ...]:
        if not symbol or not isinstance(payload, list) or len(payload) != expected_count:
            raise ValueError("100후보 공개봉 파일 종목 또는 행수가 잘못됐습니다.")
        bars: list[PublicWarmupBar] = []
        for raw in payload:
            if not isinstance(raw, Mapping) or str(raw.get("symbol", "")) != symbol:
                raise ValueError("100후보 공개봉 행의 종목이 다릅니다.")
            bar = PublicWarmupBar(
                symbol=symbol,
                open_ts_ms=int(str(raw["open_ts_ms"])),
                open=Decimal(str(raw["open"])),
                high=Decimal(str(raw["high"])),
                low=Decimal(str(raw["low"])),
                close=Decimal(str(raw["close"])),
                volume=Decimal(str(raw["volume"])),
                quote_volume=Decimal(str(raw["quote_volume"])),
                trade_count=int(str(raw["trade_count"])),
                taker_buy_volume=Decimal(str(raw["taker_buy_volume"])),
                taker_buy_quote_volume=Decimal(str(raw["taker_buy_quote_volume"])),
            )
            if (
                bar.open_ts_ms % (BASE_INTERVAL_SECONDS * 1_000) != 0
                or bar.close_ts_ms > cutoff_ts_ms
                or min(bar.open, bar.high, bar.low, bar.close) <= 0
                or bar.high < max(bar.open, bar.close)
                or bar.low > min(bar.open, bar.close)
                or bar.volume < 0
                or bar.quote_volume < 0
                or bar.trade_count < 0
                or bar.taker_buy_volume < 0
                or bar.taker_buy_volume > bar.volume
                or bar.taker_buy_quote_volume < 0
                or bar.taker_buy_quote_volume > bar.quote_volume
            ):
                raise ValueError("100후보 공개봉 값 또는 완료시각이 잘못됐습니다.")
            if bars and bar.open_ts_ms != bars[-1].close_ts_ms:
                raise ValueError("100후보 공개봉에 중복·누락·역행이 있습니다.")
            bars.append(bar)
        if not bars:
            raise ValueError("100후보 공개봉 파일이 비어 있습니다.")
        return tuple(bars)

    @staticmethod
    def _aggregate_symbol(
        bars: Sequence[PublicWarmupBar],
        *,
        cutoff_ts_ms: int,
    ) -> tuple[Candle, ...]:
        output: list[Candle] = []
        base_ms = BASE_INTERVAL_SECONDS * 1_000
        for interval_seconds in WARMUP_INTERVALS:
            interval_ms = interval_seconds * 1_000
            grouped: dict[int, list[PublicWarmupBar]] = defaultdict(list)
            for bar in bars:
                grouped[bar.open_ts_ms - bar.open_ts_ms % interval_ms].append(bar)
            expected_bars = interval_ms // base_ms
            for open_ts_ms, values in sorted(grouped.items()):
                if (
                    open_ts_ms + interval_ms > cutoff_ts_ms
                    or len(values) != expected_bars
                    or values[0].open_ts_ms != open_ts_ms
                    or values[-1].close_ts_ms != open_ts_ms + interval_ms
                ):
                    continue
                volume = sum((row.volume for row in values), start=Decimal(0))
                taker_buy_volume = sum(
                    (row.taker_buy_volume for row in values), start=Decimal(0)
                )
                output.append(
                    Candle(
                        symbol=values[0].symbol,
                        interval_seconds=interval_seconds,
                        open_ts_ms=open_ts_ms,
                        open=values[0].open,
                        high=max(row.high for row in values),
                        low=min(row.low for row in values),
                        close=values[-1].close,
                        volume=volume,
                        trade_count=sum(row.trade_count for row in values),
                        quote_volume=sum(
                            (row.quote_volume for row in values), start=Decimal(0)
                        ),
                        taker_buy_volume=taker_buy_volume,
                        taker_sell_volume=volume - taker_buy_volume,
                        taker_buy_quote_volume=sum(
                            (row.taker_buy_quote_volume for row in values),
                            start=Decimal(0),
                        ),
                        taker_sell_quote_volume=sum(
                            (
                                row.quote_volume - row.taker_buy_quote_volume
                                for row in values
                            ),
                            start=Decimal(0),
                        ),
                    )
                )
        return tuple(output)

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted({symbol for symbol, _ in self._candles_by_key}))

    def candles_before(
        self,
        cutoff_ts_ms: int,
        *,
        maximum_bars: int,
    ) -> tuple[Candle, ...]:
        if cutoff_ts_ms <= 0 or maximum_bars <= 0:
            raise ValueError("100후보 워밍업 cutoff와 보관 봉수는 양수여야 합니다.")
        selected: list[Candle] = []
        for values in self._candles_by_key.values():
            past = [
                candle
                for candle in values
                if candle.open_ts_ms + candle.interval_seconds * 1_000 <= cutoff_ts_ms
            ]
            selected.extend(past[-maximum_bars:])
        return tuple(
            sorted(
                selected,
                key=lambda candle: (
                    candle.symbol,
                    candle.interval_seconds,
                    candle.open_ts_ms,
                ),
            )
        )
