"""유동성·비용·건전성으로 종목을 필터링하고 강건한 상대순위를 계산한다."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from fnmatch import fnmatch

from backend.app.domain.market import Instrument, Ticker
from backend.app.domain.models import Venue


@dataclass(frozen=True, slots=True)
class UniverseObservation:
    instrument: Instrument
    ticker: Ticker
    executable_depth_usdt: Decimal
    data_quality_score: float
    gap_count: int
    shock: bool = False
    warmup_minutes: float = 10.0


@dataclass(frozen=True, slots=True)
class RankedSymbol:
    symbol: str
    score: float
    spread_bps: float
    quote_turnover_24h: Decimal


@dataclass(frozen=True, slots=True)
class UniverseSelection:
    venue: Venue
    wide: tuple[RankedSymbol, ...]
    deep: tuple[RankedSymbol, ...]
    excluded: dict[str, tuple[str, ...]]


class UniverseSelector:
    def __init__(
        self,
        *,
        wide_max: int = 50,
        deep_max: int = 10,
        minimum_turnover: Decimal = Decimal("20000000"),
        maximum_spread_bps: Decimal = Decimal("12"),
        minimum_warmup_minutes: float = 10.0,
    ) -> None:
        self.wide_max = wide_max
        self.deep_max = deep_max
        self.minimum_turnover = minimum_turnover
        self.maximum_spread_bps = maximum_spread_bps
        self.minimum_warmup_minutes = minimum_warmup_minutes
        self._deep_previous: tuple[str, ...] = ()

    def select(self, observations: list[UniverseObservation]) -> UniverseSelection:
        venues = {item.instrument.venue for item in observations}
        if len(venues) != 1:
            raise ValueError("한 유니버스 선택에서 거래소를 섞을 수 없습니다.")
        venue = next(iter(venues), Venue.FIXTURE)
        eligible: list[UniverseObservation] = []
        excluded: dict[str, tuple[str, ...]] = {}
        for observation in observations:
            reasons = self._exclusions(observation)
            if reasons:
                excluded[observation.instrument.symbol] = tuple(reasons)
            else:
                eligible.append(observation)
        scores = self._scores(eligible)
        ranked = tuple(
            sorted(
                (
                    RankedSymbol(
                        symbol=item.instrument.symbol,
                        score=scores[item.instrument.symbol],
                        spread_bps=float(item.ticker.spread_bps),
                        quote_turnover_24h=item.ticker.quote_turnover_24h,
                    )
                    for item in eligible
                ),
                key=lambda item: (-item.score, item.symbol),
            )
        )
        wide = ranked[: self.wide_max]
        wide_symbols = {item.symbol for item in wide}
        prior = {symbol for symbol in self._deep_previous if symbol in wide_symbols}
        deep_candidates = sorted(
            wide,
            key=lambda item: (item.symbol not in prior, -item.score, item.symbol),
        )
        deep = tuple(deep_candidates[: self.deep_max])
        self._deep_previous = tuple(item.symbol for item in deep)
        return UniverseSelection(venue=venue, wide=wide, deep=deep, excluded=excluded)

    def _exclusions(self, item: UniverseObservation) -> list[str]:
        instrument = item.instrument
        ticker = item.ticker
        reasons: list[str] = []
        if instrument.status.upper() != "TRADING":
            reasons.append("NOT_TRADING")
        if instrument.quote_asset != "USDT" or "PERPETUAL" not in instrument.contract_type.upper():
            reasons.append("NOT_USDT_PERPETUAL")
        if instrument.base_asset in {"USDC", "FDUSD", "TUSD", "USDP"}:
            reasons.append("DENYLISTED")
        leveraged_patterns = ("*UPUSDT", "*DOWNUSDT", "*BULLUSDT", "*BEARUSDT")
        if any(fnmatch(instrument.symbol, pattern) for pattern in leveraged_patterns):
            reasons.append("DENYLISTED")
        if ticker.quote_turnover_24h < self.minimum_turnover:
            reasons.append("LOW_TURNOVER")
        if ticker.spread_bps > self.maximum_spread_bps:
            reasons.append("WIDE_SPREAD")
        if item.executable_depth_usdt <= 0:
            reasons.append("LOW_DEPTH")
        if item.data_quality_score < 0.8:
            reasons.append("STALE_BOOK")
        if item.gap_count:
            reasons.append("SEQUENCE_GAP")
        if item.shock:
            reasons.append("SHOCK_STATE")
        if item.warmup_minutes < self.minimum_warmup_minutes:
            reasons.append("STALE_TRADES")
        return reasons

    @staticmethod
    def _scores(items: list[UniverseObservation]) -> dict[str, float]:
        if not items:
            return {}

        def percentile(values: list[Decimal], value: Decimal) -> float:
            if len(values) == 1:
                return 1.0
            ordered = sorted(values)
            return ordered.index(value) / (len(ordered) - 1)

        turnovers = [item.ticker.quote_turnover_24h for item in items]
        depths = [item.executable_depth_usdt for item in items]
        spreads = [item.ticker.spread_bps for item in items]
        activities = [Decimal(item.ticker.trade_count_24h) for item in items]
        return {
            item.instrument.symbol: round(
                0.30 * percentile(turnovers, item.ticker.quote_turnover_24h)
                + 0.25 * percentile(depths, item.executable_depth_usdt)
                + 0.15 * percentile(activities, Decimal(item.ticker.trade_count_24h))
                + 0.20 * item.data_quality_score
                - 0.10 * percentile(spreads, item.ticker.spread_bps)
                - 0.03 * item.gap_count,
                8,
            )
            for item in items
        }
