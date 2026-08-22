"""각 전략·비용 프로필의 독립 PAPER shadow 가상계좌와 거래를 관리한다."""

from __future__ import annotations

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
    open_position: ShadowPosition | None = None
    trades: list[ShadowTrade] = field(default_factory=list)

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
            "open_position": self.open_position.shadow_trade_id
            if self.open_position is not None
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
        if account.open_position is not None:
            raise RuntimeError("전략 shadow 계좌는 동시에 한 포지션만 허용합니다.")
        account.open_position = position

    def close(
        self,
        strategy_id: str,
        profile: CostProfile,
        *,
        exit_price: Decimal,
        exit_fee_usdt: Decimal,
        exit_slippage_usdt: Decimal,
        closed_ts_ms: int,
        exit_reason: str,
    ) -> ShadowTrade:
        account = self.account(strategy_id, profile)
        position = account.open_position
        if position is None:
            raise RuntimeError("열린 shadow 포지션 없이 종료할 수 없습니다.")
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
        )
        account.trades.append(trade)
        account.open_position = None
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
