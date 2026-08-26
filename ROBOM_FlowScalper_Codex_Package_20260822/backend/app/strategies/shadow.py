"""각 전략·비용 프로필의 독립 PAPER shadow 가상계좌와 거래를 관리한다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from backend.app.costing import CostProfile
from backend.app.domain.models import Side


@dataclass(frozen=True, slots=True)
class ShadowAccountKey:
    strategy_id: str
    profile: CostProfile


@dataclass(frozen=True, slots=True)
class ShadowPosition:
    shadow_trade_id: str
    symbol: str
    side: Side
    quantity: Decimal
    entry_price: Decimal
    entry_fee_usdt: Decimal
    entry_slippage_usdt: Decimal
    opened_ts_ms: int


@dataclass(frozen=True, slots=True)
class ShadowTrade:
    shadow_trade_id: str
    strategy_id: str
    profile: CostProfile
    symbol: str
    side: Side
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    gross_pnl_usdt: Decimal
    fees_usdt: Decimal
    slippage_usdt: Decimal
    net_pnl_usdt: Decimal
    opened_ts_ms: int
    closed_ts_ms: int
    exit_reason: str
    tp1_hit_ts_ms: int | None = None
    tp2_hit_ts_ms: int | None = None
    time_to_tp1_ms: int | None = None
    time_to_tp2_ms: int | None = None
    time_to_stop_ms: int | None = None


@dataclass(slots=True)
class ShadowAccount:
    key: ShadowAccountKey
    starting_equity_usdt: Decimal = Decimal("1000")
    current_equity_usdt: Decimal = Decimal("1000")
    peak_equity_usdt: Decimal = Decimal("1000")
    realized_pnl_usdt: Decimal = Decimal(0)
    fees_usdt: Decimal = Decimal(0)
    slippage_usdt: Decimal = Decimal(0)
    maximum_drawdown_usdt: Decimal = Decimal(0)
    open_positions: dict[str, ShadowPosition] = field(default_factory=dict)
    trades: list[ShadowTrade] = field(default_factory=list)

    @property
    def open_position(self) -> ShadowPosition | None:
        """기존 단일 포지션 테스트를 위한 읽기 전용 호환 속성이다."""

        if len(self.open_positions) > 1:
            raise AttributeError("Strategy League 계좌는 open_positions를 사용해야 합니다.")
        return next(iter(self.open_positions.values()), None)

    def snapshot(self) -> dict[str, object]:
        return {
            "strategy_id": self.key.strategy_id,
            "profile": self.key.profile.value,
            "starting_equity_usdt": str(self.starting_equity_usdt),
            "current_equity_usdt": str(self.current_equity_usdt),
            "realized_pnl_usdt": str(self.realized_pnl_usdt),
            "fees_usdt": str(self.fees_usdt),
            "slippage_usdt": str(self.slippage_usdt),
            "maximum_drawdown_usdt": str(self.maximum_drawdown_usdt),
            "closed_trades": len(self.trades),
            "account_id": f"{self.key.strategy_id}:{self.key.profile.value}",
            "open_positions": len(self.open_positions),
            "open_position": self.open_position.shadow_trade_id
            if len(self.open_positions) == 1 and self.open_position is not None
            else None,
        }


class ShadowLedger:
    """한 전략의 손익이나 포지션이 다른 전략 계좌에 영향을 주지 않게 한다."""

    def __init__(self, strategy_ids: tuple[str, ...]) -> None:
        self._accounts = {
            ShadowAccountKey(strategy_id, profile): ShadowAccount(
                ShadowAccountKey(strategy_id, profile)
            )
            for strategy_id in strategy_ids
            for profile in CostProfile
        }

    def account(self, strategy_id: str, profile: CostProfile) -> ShadowAccount:
        try:
            return self._accounts[ShadowAccountKey(strategy_id, profile)]
        except KeyError as error:
            raise ValueError(f"등록되지 않은 shadow 계좌: {strategy_id}/{profile}") from error

    def open(
        self,
        strategy_id: str,
        profile: CostProfile,
        position: ShadowPosition,
    ) -> None:
        account = self.account(strategy_id, profile)
        if position.symbol in account.open_positions:
            raise RuntimeError("동일 전략 계좌·종목의 중복 포지션은 허용하지 않습니다.")
        if len(account.open_positions) >= 3:
            raise RuntimeError("전략 리그 계좌는 최대 3개 종목만 보유합니다.")
        account.open_positions[position.symbol] = position

    def close(
        self,
        strategy_id: str,
        profile: CostProfile,
        *,
        shadow_trade_id: str | None = None,
        exit_price: Decimal,
        exit_fee_usdt: Decimal,
        exit_slippage_usdt: Decimal,
        closed_ts_ms: int,
        exit_reason: str,
        tp1_hit_ts_ms: int | None = None,
        tp2_hit_ts_ms: int | None = None,
        time_to_tp1_ms: int | None = None,
        time_to_tp2_ms: int | None = None,
        time_to_stop_ms: int | None = None,
    ) -> ShadowTrade:
        account = self.account(strategy_id, profile)
        matches = [
            position
            for position in account.open_positions.values()
            if shadow_trade_id is None or position.shadow_trade_id == shadow_trade_id
        ]
        if not matches:
            raise RuntimeError("열린 shadow 포지션 없이 종료할 수 없습니다.")
        if len(matches) > 1:
            raise RuntimeError("다중 shadow 포지션 종료에는 trade ID가 필요합니다.")
        position = matches[0]
        direction = Decimal(1) if position.side is Side.LONG else Decimal(-1)
        gross = (exit_price - position.entry_price) * position.quantity * direction
        fees = position.entry_fee_usdt + exit_fee_usdt
        slippage = position.entry_slippage_usdt + exit_slippage_usdt
        net = gross - fees - slippage
        trade = ShadowTrade(
            shadow_trade_id=position.shadow_trade_id,
            strategy_id=strategy_id,
            profile=profile,
            symbol=position.symbol,
            side=position.side,
            quantity=position.quantity,
            entry_price=position.entry_price,
            exit_price=exit_price,
            gross_pnl_usdt=gross,
            fees_usdt=fees,
            slippage_usdt=slippage,
            net_pnl_usdt=net,
            opened_ts_ms=position.opened_ts_ms,
            closed_ts_ms=closed_ts_ms,
            exit_reason=exit_reason,
            tp1_hit_ts_ms=tp1_hit_ts_ms,
            tp2_hit_ts_ms=tp2_hit_ts_ms,
            time_to_tp1_ms=time_to_tp1_ms,
            time_to_tp2_ms=time_to_tp2_ms,
            time_to_stop_ms=time_to_stop_ms,
        )
        account.trades.append(trade)
        account.open_positions.pop(position.symbol, None)
        account.realized_pnl_usdt += net
        account.fees_usdt += fees
        account.slippage_usdt += slippage
        account.current_equity_usdt += net
        account.peak_equity_usdt = max(account.peak_equity_usdt, account.current_equity_usdt)
        account.maximum_drawdown_usdt = max(
            account.maximum_drawdown_usdt,
            account.peak_equity_usdt - account.current_equity_usdt,
        )
        return trade

    def rows(self) -> list[dict[str, object]]:
        return [account.snapshot() for account in self._accounts.values()]

    def recovery_state(self) -> dict[str, object]:
        """재시작 뒤에도 전략·비용 프로필별 가상계좌가 섞이지 않게 직렬화한다."""

        return {
            "accounts": [
                {
                    "strategy_id": account.key.strategy_id,
                    "profile": account.key.profile.value,
                    "starting_equity_usdt": str(account.starting_equity_usdt),
                    "current_equity_usdt": str(account.current_equity_usdt),
                    "peak_equity_usdt": str(account.peak_equity_usdt),
                    "realized_pnl_usdt": str(account.realized_pnl_usdt),
                    "fees_usdt": str(account.fees_usdt),
                    "slippage_usdt": str(account.slippage_usdt),
                    "maximum_drawdown_usdt": str(account.maximum_drawdown_usdt),
                    "open_positions": {
                        symbol: _shadow_position_payload(position)
                        for symbol, position in account.open_positions.items()
                    },
                    "trades": [_shadow_trade_payload(trade) for trade in account.trades],
                }
                for _, account in sorted(
                    self._accounts.items(),
                    key=lambda item: (item[0].strategy_id, item[0].profile.value),
                )
            ]
        }

    def restore_state(
        self,
        payload: Mapping[str, object],
        *,
        allow_missing: bool = False,
    ) -> None:
        """checksum 검증을 마친 snapshot만 현재 Registry 계좌에 복원한다."""

        rows = payload.get("accounts")
        if not isinstance(rows, list):
            raise ValueError("shadow 복구 snapshot에 accounts가 없습니다.")
        seen: set[ShadowAccountKey] = set()
        for value in rows:
            if not isinstance(value, Mapping):
                raise ValueError("shadow 복구 계좌 형식이 잘못됐습니다.")
            key = ShadowAccountKey(
                str(value["strategy_id"]), CostProfile(str(value["profile"]))
            )
            if key in seen or key not in self._accounts:
                raise ValueError(f"shadow 복구 계좌가 중복되거나 미등록입니다: {key}")
            seen.add(key)
            account = self._accounts[key]
            account.starting_equity_usdt = Decimal(str(value["starting_equity_usdt"]))
            account.current_equity_usdt = Decimal(str(value["current_equity_usdt"]))
            account.peak_equity_usdt = Decimal(str(value["peak_equity_usdt"]))
            account.realized_pnl_usdt = Decimal(str(value["realized_pnl_usdt"]))
            account.fees_usdt = Decimal(str(value["fees_usdt"]))
            account.slippage_usdt = Decimal(str(value["slippage_usdt"]))
            account.maximum_drawdown_usdt = Decimal(
                str(value["maximum_drawdown_usdt"])
            )
            positions = value.get("open_positions")
            if isinstance(positions, Mapping):
                account.open_positions = {
                    str(symbol): _shadow_position_from_payload(position)
                    for symbol, position in positions.items()
                    if isinstance(position, Mapping)
                }
                if len(account.open_positions) != len(positions):
                    raise ValueError("shadow 복구 포지션 형식이 잘못됐습니다.")
            else:
                position = value.get("open_position")
                account.open_positions = (
                    {str(position["symbol"]): _shadow_position_from_payload(position)}
                    if isinstance(position, Mapping)
                    else {}
                )
            if len(account.open_positions) > 3:
                raise ValueError("shadow 복구 포지션 상한을 초과했습니다.")
            trade_rows = value.get("trades")
            if not isinstance(trade_rows, list):
                raise ValueError("shadow 복구 거래 목록 형식이 잘못됐습니다.")
            account.trades = [
                _shadow_trade_from_payload(row)
                for row in trade_rows
                if isinstance(row, Mapping)
            ]
            if len(account.trades) != len(trade_rows):
                raise ValueError("shadow 복구 거래 행 형식이 잘못됐습니다.")
            if account.current_equity_usdt != (
                account.starting_equity_usdt + account.realized_pnl_usdt
            ):
                raise ValueError("shadow 복구 계좌 손익이 자산과 일치하지 않습니다.")
        if not allow_missing and seen != set(self._accounts):
            raise ValueError("shadow 복구 snapshot의 전략 계좌 집합이 Registry와 다릅니다.")


def _shadow_position_payload(position: ShadowPosition | None) -> dict[str, object] | None:
    if position is None:
        return None
    return {
        "shadow_trade_id": position.shadow_trade_id,
        "symbol": position.symbol,
        "side": position.side.value,
        "quantity": str(position.quantity),
        "entry_price": str(position.entry_price),
        "entry_fee_usdt": str(position.entry_fee_usdt),
        "entry_slippage_usdt": str(position.entry_slippage_usdt),
        "opened_ts_ms": position.opened_ts_ms,
    }


def _shadow_position_from_payload(payload: Mapping[str, object]) -> ShadowPosition:
    return ShadowPosition(
        shadow_trade_id=str(payload["shadow_trade_id"]),
        symbol=str(payload["symbol"]),
        side=Side(str(payload["side"])),
        quantity=Decimal(str(payload["quantity"])),
        entry_price=Decimal(str(payload["entry_price"])),
        entry_fee_usdt=Decimal(str(payload["entry_fee_usdt"])),
        entry_slippage_usdt=Decimal(str(payload["entry_slippage_usdt"])),
        opened_ts_ms=int(str(payload["opened_ts_ms"])),
    )


def _shadow_trade_payload(trade: ShadowTrade) -> dict[str, object]:
    return {
        "shadow_trade_id": trade.shadow_trade_id,
        "strategy_id": trade.strategy_id,
        "profile": trade.profile.value,
        "symbol": trade.symbol,
        "side": trade.side.value,
        "quantity": str(trade.quantity),
        "entry_price": str(trade.entry_price),
        "exit_price": str(trade.exit_price),
        "gross_pnl_usdt": str(trade.gross_pnl_usdt),
        "fees_usdt": str(trade.fees_usdt),
        "slippage_usdt": str(trade.slippage_usdt),
        "net_pnl_usdt": str(trade.net_pnl_usdt),
        "opened_ts_ms": trade.opened_ts_ms,
        "closed_ts_ms": trade.closed_ts_ms,
        "exit_reason": trade.exit_reason,
        "tp1_hit_ts_ms": trade.tp1_hit_ts_ms,
        "tp2_hit_ts_ms": trade.tp2_hit_ts_ms,
        "time_to_tp1_ms": trade.time_to_tp1_ms,
        "time_to_tp2_ms": trade.time_to_tp2_ms,
        "time_to_stop_ms": trade.time_to_stop_ms,
    }


def _shadow_trade_from_payload(payload: Mapping[str, object]) -> ShadowTrade:
    return ShadowTrade(
        shadow_trade_id=str(payload["shadow_trade_id"]),
        strategy_id=str(payload["strategy_id"]),
        profile=CostProfile(str(payload["profile"])),
        symbol=str(payload["symbol"]),
        side=Side(str(payload["side"])),
        quantity=Decimal(str(payload["quantity"])),
        entry_price=Decimal(str(payload["entry_price"])),
        exit_price=Decimal(str(payload["exit_price"])),
        gross_pnl_usdt=Decimal(str(payload["gross_pnl_usdt"])),
        fees_usdt=Decimal(str(payload["fees_usdt"])),
        slippage_usdt=Decimal(str(payload["slippage_usdt"])),
        net_pnl_usdt=Decimal(str(payload["net_pnl_usdt"])),
        opened_ts_ms=int(str(payload["opened_ts_ms"])),
        closed_ts_ms=int(str(payload["closed_ts_ms"])),
        exit_reason=str(payload["exit_reason"]),
        tp1_hit_ts_ms=_optional_int(payload.get("tp1_hit_ts_ms")),
        tp2_hit_ts_ms=_optional_int(payload.get("tp2_hit_ts_ms")),
        time_to_tp1_ms=_optional_int(payload.get("time_to_tp1_ms")),
        time_to_tp2_ms=_optional_int(payload.get("time_to_tp2_ms")),
        time_to_stop_ms=_optional_int(payload.get("time_to_stop_ms")),
    )


def _optional_int(value: object | None) -> int | None:
    return None if value is None else int(str(value))
