"""실제 PAPER 거래를 중심으로 결정적 포지션 집중 리플레이 세션을 만든다."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from typing import Any

from backend.app.replay.market import StoredMarketReplay
from backend.app.storage.sqlite import SQLiteLedger


class ReplayFocusSessionBuilder:
    """저장 이벤트·체결·거래를 한 시간축에 정렬하고 미래 표식을 숨긴다."""

    def build(
        self,
        ledger: SQLiteLedger,
        *,
        run_id: str,
        trade_id: str,
        created_ts_ms: int,
        profile: str = "BASE",
        pre_roll_ms: int = 20 * 60 * 1_000,
        post_roll_ms: int = 5 * 60 * 1_000,
        cooperative_yield: Callable[[], None] | None = None,
    ) -> dict[str, object]:
        cached = ledger.get_replay_focus_session(
            run_id,
            trade_id,
            profile,
            session_version=1,
        )
        if cached is not None:
            return cached
        trade = self._find_trade(ledger, run_id, trade_id, profile)
        symbol = str(trade["symbol"])
        entry_ts = int(str(trade["entry_ts_ms"]))
        exit_ts = int(str(trade["exit_ts_ms"]))
        window = ledger.list_market_events(
            run_id,
            symbol=symbol,
            start_ts_ms=entry_ts - pre_roll_ms,
            end_ts_ms=exit_ts + post_roll_ms,
            cooperative_yield=cooperative_yield,
        )
        replay = self._covering_replay_result(
            ledger,
            run_id=run_id,
            entry_ts_ms=entry_ts,
            exit_ts_ms=exit_ts,
        )
        if replay is None:
            replay = StoredMarketReplay().run(
                ledger,
                source_run_id=run_id,
                created_ts_ms=created_ts_ms,
                symbol=symbol,
                cooperative_yield=cooperative_yield,
            ).as_dict()
        fills = ledger.list_fills(run_id)
        orders = ledger.list_orders(run_id)
        order_ids = {
            str(order["order_id"])
            for order in orders
            if str(order.get("trade_id")) == trade_id
        }
        trade_fills = [fill for fill in fills if str(fill.get("order_id")) in order_ids]
        stored_candles = ledger.list_candles(run_id, symbol=symbol, interval_seconds=180)
        if not stored_candles:
            stored_candles = ledger.list_candles(run_id, symbol=symbol, interval_seconds=1)
        candles = [
            self._chart_candle(candle)
            for candle in stored_candles
            if entry_ts - pre_roll_ms
            <= int(str(candle["open_ts_ms"]))
            <= exit_ts + post_roll_ms
        ]
        frames: list[dict[str, object]] = []
        for index, event in enumerate(window, start=1):
            frames.append(self._frame(event, trade, trade_fills))
            if cooperative_yield is not None and index % 512 == 0:
                cooperative_yield()
        if not frames:
            raise ValueError("거래 시간대의 저장 공개시장 이벤트가 없습니다.")
        frames.extend(
            (
                self._ledger_transition_frame(trade, trade_fills, transition="ENTRY"),
                self._ledger_transition_frame(trade, trade_fills, transition="EXIT"),
            )
        )
        frames.sort(
            key=lambda frame: (
                int(str(frame["ts_ms"])),
                1 if frame["event_type"] == "PAPER_LEDGER_TRANSITION" else 0,
                str(frame["event_id"]),
            )
        )
        frames = self._bounded_frames(frames, maximum=50_000)
        keyframes = [
            {"frame_index": index, "ts_ms": int(str(frame["ts_ms"]))}
            for index, frame in enumerate(frames)
            if index == 0
            or index == len(frames) - 1
            or index % 250 == 0
            or self._state_changed(frames[index - 1], frame)
        ]
        comparisons = self._profile_comparison(ledger, run_id, trade)
        replay_count = int(
            str(
                replay["main_trade_count"]
                if profile == "BASE"
                else replay["shadow_trade_count"]
            )
        )
        sample_type = str(trade.get("sample_type", "LIVE_PUBLIC"))
        reconciliation_applicable = sample_type == "LIVE_PUBLIC"
        reconciliation = {
            "applicable": reconciliation_applicable,
            "sample_type": sample_type,
            "source_trade_found": True,
            "replay_trade_path_observed": replay_count > 0,
            "source_net_pnl": str(trade["net_pnl_usdt"]),
            "source_fees": str(trade["fees_usdt"]),
            "source_slippage": str(trade["slippage_usdt"]),
            "replay_final_state": str(replay["final_state"]),
            "replay_checksum": str(replay["checksum"]),
            "matched": replay_count > 0 if reconciliation_applicable else None,
            "reason": (
                "PUBLIC_PAPER_REPLAY_COMPARISON"
                if reconciliation_applicable
                else "OFFLINE_FIXTURE_UI_ONLY"
            ),
        }
        session: dict[str, object] = {
            "session_version": 1,
            "run_id": run_id,
            "trade_id": trade_id,
            "profile": profile,
            "symbol": symbol,
            "side": str(trade["side"]),
            "strategy_id": str(trade["strategy_id"]),
            "start_ts_ms": int(str(frames[0]["ts_ms"])),
            "entry_ts_ms": entry_ts,
            "exit_ts_ms": exit_ts,
            "end_ts_ms": int(str(frames[-1]["ts_ms"])),
            "default_speed": 5,
            "speeds": [0.5, 1, 2, 5, 10, 20, 40, 80],
            "frames": frames,
            "keyframes": keyframes,
            "trade": dict(trade),
            "fills": trade_fills,
            "candles": candles,
            "profile_comparison": comparisons,
            "reconciliation": reconciliation,
            "paper_only": True,
            "real_orders_enabled": False,
            "auth_required": False,
        }
        canonical = json.dumps(session, sort_keys=True, separators=(",", ":"), default=str)
        session["checksum"] = hashlib.sha256(canonical.encode()).hexdigest()
        ledger.record_replay_focus_session(session, created_ts_ms=created_ts_ms)
        return session

    @staticmethod
    def _covering_replay_result(
        ledger: SQLiteLedger,
        *,
        run_id: str,
        entry_ts_ms: int,
        exit_ts_ms: int,
    ) -> dict[str, Any] | None:
        """거래 시간대를 이미 검증한 replay가 있으면 화면 클릭에서 재계산하지 않는다."""

        for replay in reversed(ledger.list_replay_runs(run_id)):
            first_ts_ms = replay.get("first_ts_ms")
            last_ts_ms = replay.get("last_ts_ms")
            if first_ts_ms is None or last_ts_ms is None:
                continue
            if int(str(first_ts_ms)) <= entry_ts_ms and int(str(last_ts_ms)) >= exit_ts_ms:
                safe_boundary = (
                    replay.get("real_orders_enabled") is False
                    and replay.get("auth_required") is False
                )
                if safe_boundary:
                    return replay
        return None

    @staticmethod
    def _chart_candle(candle: Mapping[str, object]) -> dict[str, object]:
        open_ts_ms = int(str(candle["open_ts_ms"]))
        return {
            "time": open_ts_ms // 1_000,
            "open_ts_ms": open_ts_ms,
            "open": float(str(candle["open"])),
            "high": float(str(candle["high"])),
            "low": float(str(candle["low"])),
            "close": float(str(candle["close"])),
            "volume": float(str(candle["volume"])),
            "trade_count": int(str(candle["trade_count"])),
        }

    @staticmethod
    def _find_trade(
        ledger: SQLiteLedger,
        run_id: str,
        trade_id: str,
        profile: str,
    ) -> dict[str, Any]:
        rows = (
            ledger.list_trades(run_id)
            if profile == "BASE"
            else ledger.list_shadow_trades(run_id)
        )
        for trade in rows:
            identity = str(trade.get("trade_id", trade.get("shadow_trade_id", "")))
            if identity == trade_id and str(trade.get("profile", "BASE")) == profile:
                return trade
        raise ValueError(f"저장 PAPER 거래를 찾을 수 없습니다: {trade_id}/{profile}")

    @staticmethod
    def _frame(
        event: Mapping[str, object],
        trade: Mapping[str, object],
        fills: list[dict[str, Any]],
    ) -> dict[str, object]:
        ts_ms = int(str(event["venue_ts_ms"]))
        entry_ts = int(str(trade["entry_ts_ms"]))
        exit_ts = int(str(trade["exit_ts_ms"]))
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        markers = [
            {
                "kind": "ENTRY",
                "ts_ms": entry_ts,
                "price": str(trade["entry_price"]),
                "label": "PAPER 진입 체결",
            }
        ] if ts_ms >= entry_ts else []
        if ts_ms >= exit_ts:
            markers.append(
                {
                    "kind": "EXIT",
                    "ts_ms": exit_ts,
                    "price": str(trade["exit_price"]),
                    "label": "PAPER 종료 체결",
                }
            )
        visible_fills = [fill for fill in fills if int(str(fill["ts_ms"])) <= ts_ms]
        return {
            "ts_ms": ts_ms,
            "event_id": str(event["event_id"]),
            "event_type": str(event["event_type"]),
            "data": data,
            "phase": "PRE_ENTRY" if ts_ms < entry_ts else "OPEN" if ts_ms < exit_ts else "CLOSED",
            "markers": markers,
            "fills": visible_fills,
        }

    @staticmethod
    def _ledger_transition_frame(
        trade: Mapping[str, object],
        fills: list[dict[str, Any]],
        *,
        transition: str,
    ) -> dict[str, object]:
        """시장 이벤트가 거래 종료보다 먼저 끝나도 저장 원장 체결 전환은 숨기지 않는다."""

        entry_ts = int(str(trade["entry_ts_ms"]))
        exit_ts = int(str(trade["exit_ts_ms"]))
        ts_ms = entry_ts if transition == "ENTRY" else exit_ts
        entry_marker = {
            "kind": "ENTRY",
            "ts_ms": entry_ts,
            "price": str(trade["entry_price"]),
            "label": "PAPER 진입 체결",
        }
        markers = [entry_marker]
        if transition == "EXIT":
            markers.append(
                {
                    "kind": "EXIT",
                    "ts_ms": exit_ts,
                    "price": str(trade["exit_price"]),
                    "label": "PAPER 종료 체결",
                }
            )
        return {
            "ts_ms": ts_ms,
            "event_id": (
                f"{trade.get('trade_id', trade.get('shadow_trade_id', 'trade'))}"
                f":{transition}"
            ),
            "event_type": "PAPER_LEDGER_TRANSITION",
            "data": {
                "price": str(
                    trade["entry_price"] if transition == "ENTRY" else trade["exit_price"]
                )
            },
            "phase": "OPEN" if transition == "ENTRY" else "CLOSED",
            "markers": markers,
            "fills": [fill for fill in fills if int(str(fill["ts_ms"])) <= ts_ms],
        }

    @staticmethod
    def _state_changed(
        previous: Mapping[str, object], current: Mapping[str, object]
    ) -> bool:
        def size(value: object) -> int:
            return len(value) if isinstance(value, list) else 0

        return (
            previous.get("phase") != current.get("phase")
            or size(previous.get("markers")) != size(current.get("markers"))
            or size(previous.get("fills")) != size(current.get("fills"))
        )

    @classmethod
    def _bounded_frames(
        cls, frames: list[dict[str, object]], *, maximum: int
    ) -> list[dict[str, object]]:
        """상태 전환은 보존하고 시장 전용 프레임만 균등 축소한다."""

        if len(frames) <= maximum:
            return frames
        essential = {0, len(frames) - 1}
        for index in range(1, len(frames)):
            if cls._state_changed(frames[index - 1], frames[index]):
                essential.add(index)
        remaining = max(1, maximum - len(essential))
        stride = max(1, math.ceil(len(frames) / remaining))
        keep = sorted(essential | set(range(0, len(frames), stride)))
        if len(keep) > maximum:
            optional = [index for index in keep if index not in essential]
            allowed = max(0, maximum - len(essential))
            step = max(1, math.ceil(len(optional) / max(1, allowed)))
            keep = sorted(essential | set(optional[::step][:allowed]))
        return [frames[index] for index in keep]

    @staticmethod
    def _profile_comparison(
        ledger: SQLiteLedger,
        run_id: str,
        source: Mapping[str, object],
    ) -> list[dict[str, object]]:
        matches = [
            row
            for row in [*ledger.list_trades(run_id), *ledger.list_shadow_trades(run_id)]
            if str(row.get("strategy_id")) == str(source.get("strategy_id"))
            and str(row.get("symbol")) == str(source.get("symbol"))
            and str(row.get("side")) == str(source.get("side"))
        ]
        return [
            {
                "profile": str(row.get("profile", "BASE")),
                "trade_id": str(row.get("trade_id", row.get("shadow_trade_id", ""))),
                "fees": str(row.get("fees_usdt", "0")),
                "slippage": str(row.get("slippage_usdt", "0")),
                "net_pnl": str(row.get("net_pnl_usdt", "0")),
            }
            for row in matches
        ]
