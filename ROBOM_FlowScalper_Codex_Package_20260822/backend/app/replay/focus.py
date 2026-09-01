"""실제 PAPER 거래를 중심으로 결정적 포지션 집중 리플레이 세션을 만든다."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any

from backend.app.storage.sqlite import SQLiteLedger

_FOCUS_SESSION_VERSION = 8
_CANDIDATE_ID_PATTERN = re.compile(r"^paper-(candidate-[0-9a-f]+)-")
_EXIT_REASON_LABELS = {
    "TAKE_PROFIT": "익절",
    "STOP": "손절",
    "EDGE_DECAY": "진입 근거 약화",
    "PROFIT_PROTECTION": "수익 보호",
    "MAX_HOLD": "전략별 최대 보유시간 도달",
    "EMERGENCY_STALE": "데이터 지연 안전 종료",
    "DATA_GAP": "데이터 공백 안전 종료",
    "MANUAL_PAPER_EXIT": "사용자 모의 종료",
    "FAULT": "오류 안전 종료",
}
_MILESTONE_ORDER = {
    "SIGNAL": 0,
    "ENTRY": 1,
    "TP1_HIT": 2,
    "TP2_HIT": 3,
    "STOP_HIT": 4,
    "EXIT": 5,
}
_ENTRY_REASON_LABELS_KO = {
    "AGGRESSIVE_NOTIONAL_STRONG": "공격 체결 금액이 평소보다 강했습니다.",
    "MULTI_WINDOW_FLOW_ALIGNED": "짧은 구간과 긴 구간의 체결 방향이 같았습니다.",
    "OFI_ALIGNED": "호가 주문흐름이 진입 방향과 같았습니다.",
    "PRICE_RESPONSE_EFFICIENT": "체결 압력이 실제 가격 이동으로 이어졌습니다.",
    "MICROPRICE_ALIGNED": "호가 수량을 반영한 참고가격이 같은 방향이었습니다.",
    "FLOW_ALIGNMENT_PERSISTENT": "방향성이 순간값이 아니라 정해진 시간 동안 이어졌습니다.",
    "STRUCTURE_CONFIRMED": "가격 구조가 진입 방향을 확인했습니다.",
    "FLOW_CONFIRMED": "체결과 호가 흐름이 진입 방향을 확인했습니다.",
    "OFI_REACCELERATION": "주문흐름이 조정 후 다시 강해졌습니다.",
    "TWENTY_FOUR_HOUR_MOMENTUM": "24시간 모멘텀이 진입 방향과 같았습니다.",
    "ACTUAL_BOOK_ENTRY_REQUIRED": "실제 공개호가에서 진입 비용 조건을 확인했습니다.",
}
_REGIME_LABELS_KO = {
    "TREND_UP": "상승 추세",
    "TREND_DOWN": "하락 추세",
    "RANGE": "박스권",
    "HIGH_VOL": "변동성 확대",
    "LOW_VOL": "변동성 축소",
}


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
        persist_cache: bool = True,
    ) -> dict[str, object]:
        cached = ledger.get_replay_focus_session(
            run_id,
            trade_id,
            profile,
            session_version=_FOCUS_SESSION_VERSION,
        )
        if cached is not None:
            return cached
        trade = self._find_trade(ledger, run_id, trade_id, profile)
        candidate = self._find_candidate(ledger, run_id, trade)
        levels = self._plan_levels(trade, candidate)
        entry_context = self._entry_context(trade, candidate, levels)
        symbol = str(trade["symbol"])
        entry_ts = int(str(trade["entry_ts_ms"]))
        exit_ts = int(str(trade["exit_ts_ms"]))
        stored_candles = ledger.list_candles(
            run_id,
            symbol=symbol,
            interval_seconds=180,
            start_ts_ms=entry_ts - pre_roll_ms,
            end_ts_ms=exit_ts + post_roll_ms,
        )
        if not stored_candles:
            stored_candles = ledger.list_candles(
                run_id,
                symbol=symbol,
                interval_seconds=1,
                start_ts_ms=entry_ts - pre_roll_ms,
                end_ts_ms=exit_ts + post_roll_ms,
            )
        candles = [
            self._chart_candle(candle)
            for candle in stored_candles
            if entry_ts - pre_roll_ms
            <= int(str(candle["open_ts_ms"]))
            <= exit_ts + post_roll_ms
        ]
        window: list[dict[str, Any]] = []
        for candle in candles:
            event = self._candle_event(run_id, symbol, candle)
            if int(str(event["venue_ts_ms"])) <= exit_ts:
                window.append(event)
        if not window:
            window = ledger.list_market_events(
                run_id,
                symbol=symbol,
                limit=2_000,
                start_ts_ms=entry_ts - min(pre_roll_ms, 60_000),
                end_ts_ms=exit_ts + min(post_roll_ms, 60_000),
                cooperative_yield=cooperative_yield,
            )
        replay = self._covering_replay_result(
            ledger,
            run_id=run_id,
            entry_ts_ms=entry_ts,
            exit_ts_ms=exit_ts,
        )
        trade_fills = self._trade_transitions(trade, candidate)
        milestones = self._milestones(window, trade, levels)
        frames: list[dict[str, object]] = []
        for index, event in enumerate(window, start=1):
            frames.append(self._frame(event, trade, trade_fills, milestones))
            if cooperative_yield is not None and index % 512 == 0:
                cooperative_yield()
        if not frames:
            raise ValueError("거래 시간대의 저장 공개시장 이벤트가 없습니다.")
        frames.extend(
            (
                self._ledger_transition_frame(
                    trade, trade_fills, milestones, transition="ENTRY"
                ),
                self._ledger_transition_frame(
                    trade, trade_fills, milestones, transition="EXIT"
                ),
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
        replay_count = (
            int(
                str(
                    replay["main_trade_count"]
                    if profile == "BASE"
                    else replay["shadow_trade_count"]
                )
            )
            if replay is not None
            else None
        )
        sample_type = str(trade.get("sample_type", "LIVE_PUBLIC"))
        reconciliation_applicable = sample_type == "LIVE_PUBLIC"
        reconciliation = {
            "applicable": reconciliation_applicable,
            "sample_type": sample_type,
            "source_trade_found": True,
            "replay_trade_path_observed": replay_count is not None and replay_count > 0,
            "source_net_pnl": str(trade["net_pnl_usdt"]),
            "source_fees": str(trade["fees_usdt"]),
            "source_slippage": str(trade["slippage_usdt"]),
            "replay_final_state": (
                str(replay["final_state"]) if replay is not None else "NOT_RUN"
            ),
            "replay_checksum": str(replay["checksum"]) if replay is not None else "",
            "matched": (
                replay_count > 0
                if reconciliation_applicable and replay_count is not None
                else None
            ),
            "reason": (
                "PUBLIC_PAPER_REPLAY_COMPARISON"
                if reconciliation_applicable and replay is not None
                else "FULL_RUN_REPLAY_NOT_RUN"
                if reconciliation_applicable
                else "OFFLINE_FIXTURE_UI_ONLY"
            ),
        }
        session: dict[str, object] = {
            "session_version": _FOCUS_SESSION_VERSION,
            "run_id": run_id,
            "trade_id": trade_id,
            "profile": profile,
            "symbol": symbol,
            "side": str(trade["side"]),
            "strategy_id": str(trade["strategy_id"]),
            "entry_context": entry_context,
            "levels": levels,
            "milestones": milestones,
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
        if persist_cache:
            try:
                ledger.record_replay_focus_session(session, created_ts_ms=created_ts_ms)
            except sqlite3.OperationalError as error:
                # 집중 차트는 원장 읽기 결과가 본체이고 캐시는 선택적 가속 계층이다.
                # 분리 영속화 프로세스가 쓰기 잠금을 가진 짧은 구간에도 차트 응답은
                # 성공시키고, 이미 만든 결과는 현재 실행 동안 다시 계산하지 않는다.
                if "locked" not in str(error).lower() and "busy" not in str(error).lower():
                    raise
                ledger.remember_replay_focus_session(session)
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
            "interval_seconds": int(str(candle.get("interval_seconds", 180))),
        }

    @staticmethod
    def _trade_transitions(
        trade: Mapping[str, object],
        candidate: Mapping[str, object] | None,
    ) -> list[dict[str, object]]:
        """집중 차트는 완결된 거래 원장의 실제 진입·종료 체결만 사용한다."""

        identity = str(trade.get("trade_id", trade.get("shadow_trade_id", "trade")))
        quantity = Decimal(str(trade["quantity"]))
        entry_price = Decimal(str(trade["entry_price"]))
        exit_price = Decimal(str(trade["exit_price"]))
        total_fees = max(Decimal(0), Decimal(str(trade.get("fees_usdt", 0))))
        total_slippage = max(
            Decimal(0), Decimal(str(trade.get("slippage_usdt", 0)))
        )
        entry_notional = abs(entry_price * quantity)
        exit_notional = abs(exit_price * quantity)
        combined_notional = entry_notional + exit_notional
        entry_fee = (
            total_fees * entry_notional / combined_notional
            if combined_notional > 0
            else total_fees / Decimal(2)
        )
        exit_fee = total_fees - entry_fee
        planned_entry = entry_price
        if candidate is not None and candidate.get("planned_entry") is not None:
            planned_entry = Decimal(str(candidate["planned_entry"]))
        direction = Decimal(1) if str(trade["side"]) == "LONG" else Decimal(-1)
        entry_slippage = max(
            Decimal(0),
            min(
                total_slippage,
                (entry_price - planned_entry) * quantity * direction,
            ),
        )
        exit_slippage = total_slippage - entry_slippage
        return [
            {
                "fill_id": f"{identity}:ENTRY",
                "trade_id": identity,
                "intent": "ENTRY",
                "price": str(entry_price),
                "quantity": str(quantity),
                "fee_usdt": str(entry_fee),
                "slippage_usdt": str(entry_slippage),
                "cost_allocation": "LEDGER_TOTAL_SPLIT_BY_EXECUTED_NOTIONAL",
                "ts_ms": int(str(trade["entry_ts_ms"])),
            },
            {
                "fill_id": f"{identity}:EXIT",
                "trade_id": identity,
                "intent": "EXIT",
                "price": str(exit_price),
                "quantity": str(quantity),
                "fee_usdt": str(exit_fee),
                "slippage_usdt": str(exit_slippage),
                "cost_allocation": "LEDGER_TOTAL_SPLIT_BY_EXECUTED_NOTIONAL",
                "ts_ms": int(str(trade["exit_ts_ms"])),
                "exit_reason": str(trade["exit_reason"]),
            },
        ]

    @staticmethod
    def _candle_event(
        run_id: str,
        symbol: str,
        candle: Mapping[str, object],
    ) -> dict[str, Any]:
        """거래 차트는 전체 틱 대신 저장 완료 봉을 결정적 재생 프레임으로 사용한다."""

        open_ts_ms = int(str(candle["open_ts_ms"]))
        interval_seconds = int(str(candle.get("interval_seconds", 180)))
        ts_ms = open_ts_ms + interval_seconds * 1_000 - 1
        close = str(candle["close"])
        return {
            "event_id": f"focus-candle:{run_id}:{symbol}:{open_ts_ms}",
            "run_id": run_id,
            "venue_ts_ms": ts_ms,
            "event_type": f"CANDLE_{interval_seconds}S",
            "data": {
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
                "bid": close,
                "ask": close,
                "mid": close,
                "resolution_seconds": interval_seconds,
            },
        }

    @staticmethod
    def _find_trade(
        ledger: SQLiteLedger,
        run_id: str,
        trade_id: str,
        profile: str,
    ) -> dict[str, Any]:
        trade = ledger.get_paper_trade(run_id, trade_id, profile)
        if trade is not None:
            return trade
        raise ValueError(f"저장 PAPER 거래를 찾을 수 없습니다: {trade_id}/{profile}")

    @staticmethod
    def _find_candidate(
        ledger: SQLiteLedger,
        run_id: str,
        trade: Mapping[str, object],
    ) -> dict[str, Any] | None:
        candidate_id = trade.get("candidate_id")
        if candidate_id is None:
            identity = str(trade.get("trade_id", trade.get("shadow_trade_id", "")))
            matched = _CANDIDATE_ID_PATTERN.match(identity)
            candidate_id = matched.group(1) if matched is not None else None
        if candidate_id is None:
            return None
        return ledger.get_candidate(run_id, str(candidate_id))

    @staticmethod
    def _plan_levels(
        trade: Mapping[str, object],
        candidate: Mapping[str, object] | None,
    ) -> dict[str, object]:
        raw_targets = (
            candidate.get("take_profit_targets", [])
            if candidate is not None
            else []
        )
        targets = raw_targets if isinstance(raw_targets, list) else []
        target_prices = [
            str(target["price"])
            for target in targets
            if isinstance(target, Mapping) and target.get("price") is not None
        ]
        stored_tp1 = trade.get("take_profit_1")
        stored_tp2 = trade.get("take_profit_2")
        take_profit_1 = (
            str(stored_tp1)
            if stored_tp1 is not None
            else target_prices[0]
            if target_prices
            else str(trade["take_profit"])
        )
        take_profit_2 = (
            str(stored_tp2)
            if stored_tp2 is not None
            else target_prices[1]
            if len(target_prices) > 1
            else None
        )
        return {
            "signal_ts_ms": int(
                str(
                    candidate.get("signal_time_ms", trade["entry_ts_ms"])
                    if candidate is not None
                    else trade["entry_ts_ms"]
                )
            ),
            "entry": str(trade["entry_price"]),
            "initial_stop": str(trade["initial_stop"]),
            "take_profit_1": take_profit_1,
            "take_profit_2": take_profit_2,
        }

    @staticmethod
    def _entry_context(
        trade: Mapping[str, object],
        candidate: Mapping[str, object] | None,
        levels: Mapping[str, object],
    ) -> dict[str, object]:
        """원장의 실제 신호 근거와 Registry 설명을 분리해 사람이 읽을 수 있게 정리한다."""

        raw_reason_codes = candidate.get("reason_codes", []) if candidate else []
        reason_codes = (
            [str(code) for code in raw_reason_codes]
            if isinstance(raw_reason_codes, list | tuple)
            else []
        )
        reason_labels = list(
            dict.fromkeys(
                _ENTRY_REASON_LABELS_KO.get(
                    code,
                    "저장된 전략의 세부 진입 조건을 충족했습니다.",
                )
                for code in reason_codes
            )
        )
        strategy_id = str(trade["strategy_id"])
        descriptor = None
        try:
            from backend.app.strategies.registry import StrategyRegistry

            descriptor = StrategyRegistry().descriptor(strategy_id)
        except ValueError:
            descriptor = None
        trade_version = str(trade.get("strategy_version") or "")
        registry_version = (
            descriptor.research_contract.strategy_version if descriptor is not None else ""
        )
        regime = str(candidate.get("regime", "UNKNOWN")) if candidate else "UNKNOWN"
        return {
            "signal_ts_ms": int(str(levels["signal_ts_ms"])),
            "reason_codes": reason_codes,
            "reason_labels_ko": reason_labels,
            "regime": regime,
            "regime_ko": _REGIME_LABELS_KO.get(regime, "시장 상태 확인"),
            "strategy_display_name_ko": (
                descriptor.display_name_ko if descriptor is not None else "이전 전략"
            ),
            "strategy_summary_ko": descriptor.summary_ko if descriptor is not None else "",
            "entry_hypothesis_ko": (
                descriptor.research_contract.entry_hypothesis_ko
                if descriptor is not None
                else ""
            ),
            "required_timeframes": (
                list(descriptor.required_timeframes) if descriptor is not None else []
            ),
            "entry_rules_ko": (
                list(descriptor.entry_rules_ko) if descriptor is not None else []
            ),
            "trade_strategy_version": trade_version,
            "registry_strategy_version": registry_version,
            "registry_metadata_matches_trade": bool(
                descriptor is not None and trade_version and trade_version == registry_version
            ),
            "evidence_ko": "저장된 공개시장 신호·PAPER 원장 기준",
            "paper_only": True,
        }

    @classmethod
    def _milestones(
        cls,
        events: list[dict[str, Any]],
        trade: Mapping[str, object],
        levels: Mapping[str, object],
    ) -> list[dict[str, object]]:
        entry_ts_ms = int(str(trade["entry_ts_ms"]))
        exit_ts_ms = int(str(trade["exit_ts_ms"]))
        side = str(trade["side"])
        raw_flags = trade.get("flags", [])
        flags = {str(flag) for flag in raw_flags} if isinstance(raw_flags, list) else set()
        milestones: list[dict[str, object]] = [
            {
                "kind": "SIGNAL",
                "ts_ms": int(str(levels["signal_ts_ms"])),
                "price": str(levels["entry"]),
                "label": "진입 신호 확정",
            },
            {
                "kind": "ENTRY",
                "ts_ms": entry_ts_ms,
                "price": str(levels["entry"]),
                "label": "PAPER 진입 체결",
            },
        ]
        for label, kind, key in (
            ("TP1", "TP1_HIT", "take_profit_1"),
            ("TP2", "TP2_HIT", "take_profit_2"),
        ):
            target = levels.get(key)
            if label not in flags or target is None:
                continue
            target_value = float(str(target))
            hit_ts_ms = cls._first_target_hit(
                events,
                side=side,
                target=target_value,
                entry_ts_ms=entry_ts_ms,
                exit_ts_ms=exit_ts_ms,
            )
            milestones.append(
                {
                    "kind": kind,
                    "ts_ms": hit_ts_ms or exit_ts_ms,
                    "price": str(target),
                    "label": f"{label} 도달",
                }
            )
        exit_reason = str(trade["exit_reason"])
        if exit_reason == "STOP":
            milestones.append(
                {
                    "kind": "STOP_HIT",
                    "ts_ms": exit_ts_ms,
                    "price": str(trade["exit_price"]),
                    "label": "손절·보호선 실제 체결",
                }
            )
        reason_label = _EXIT_REASON_LABELS.get(exit_reason, exit_reason)
        milestones.append(
            {
                "kind": "EXIT",
                "ts_ms": exit_ts_ms,
                "price": str(trade["exit_price"]),
                "label": f"실제 종료 · {reason_label}",
            }
        )
        milestones.sort(
            key=lambda row: (
                int(str(row["ts_ms"])),
                _MILESTONE_ORDER.get(str(row["kind"]), 99),
            )
        )
        return milestones

    @classmethod
    def _first_target_hit(
        cls,
        events: list[dict[str, Any]],
        *,
        side: str,
        target: float,
        entry_ts_ms: int,
        exit_ts_ms: int,
    ) -> int | None:
        for event in events:
            ts_ms = int(str(event["venue_ts_ms"]))
            if ts_ms < entry_ts_ms or ts_ms > exit_ts_ms:
                continue
            bid, ask = cls._quote(event)
            executable = bid if side == "LONG" else ask
            data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
            assert isinstance(data, Mapping)
            high = cls._finite_number(data.get("high"))
            low = cls._finite_number(data.get("low"))
            if side == "LONG" and high is not None and high >= target:
                return ts_ms
            if side == "SHORT" and low is not None and low <= target:
                return ts_ms
            if executable is None:
                continue
            if (side == "LONG" and executable >= target) or (
                side == "SHORT" and executable <= target
            ):
                return ts_ms
        return None

    @staticmethod
    def _quote(event: Mapping[str, object]) -> tuple[float | None, float | None]:
        data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
        assert isinstance(data, Mapping)

        bid = ReplayFocusSessionBuilder._finite_number(data.get("bid"))
        ask = ReplayFocusSessionBuilder._finite_number(data.get("ask"))
        bids = data.get("bids")
        asks = data.get("asks")
        if bid is None and isinstance(bids, list) and bids and isinstance(bids[0], list):
            bid = ReplayFocusSessionBuilder._finite_number(bids[0][0]) if bids[0] else None
        if ask is None and isinstance(asks, list) and asks and isinstance(asks[0], list):
            ask = ReplayFocusSessionBuilder._finite_number(asks[0][0]) if asks[0] else None
        return bid, ask

    @staticmethod
    def _finite_number(value: object) -> float | None:
        try:
            parsed = float(str(value))
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None

    @staticmethod
    def _frame(
        event: Mapping[str, object],
        trade: Mapping[str, object],
        fills: list[dict[str, Any]],
        milestones: list[dict[str, object]],
    ) -> dict[str, object]:
        ts_ms = int(str(event["venue_ts_ms"]))
        entry_ts = int(str(trade["entry_ts_ms"]))
        exit_ts = int(str(trade["exit_ts_ms"]))
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        markers = [
            marker for marker in milestones if int(str(marker["ts_ms"])) <= ts_ms
        ]
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
        milestones: list[dict[str, object]],
        *,
        transition: str,
    ) -> dict[str, object]:
        """시장 이벤트가 거래 종료보다 먼저 끝나도 저장 원장 체결 전환은 숨기지 않는다."""

        entry_ts = int(str(trade["entry_ts_ms"]))
        exit_ts = int(str(trade["exit_ts_ms"]))
        ts_ms = entry_ts if transition == "ENTRY" else exit_ts
        markers = [
            marker for marker in milestones if int(str(marker["ts_ms"])) <= ts_ms
        ]
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
        matches = ledger.list_comparable_paper_trades(
            run_id,
            strategy_id=str(source.get("strategy_id")),
            symbol=str(source.get("symbol")),
            side=str(source.get("side")),
        )
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
