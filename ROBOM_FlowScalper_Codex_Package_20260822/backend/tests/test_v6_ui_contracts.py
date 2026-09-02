# V6 실시간 요약과 사용자·전문가 설정 계약을 검증한다.

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from backend.app.build_identity import APP_VERSION, STRATEGY_VERSION
from backend.app.clocks import TestClock as DeterministicClock
from backend.app.domain.models import RuntimeMode
from backend.app.main import create_app
from backend.app.research.source_metadata import research_source_metadata
from backend.app.runtime import PaperRuntime
from backend.app.storage.sqlite import LedgerInvariantError, SQLiteLedger
from backend.app.ui_v6 import (
    compact_mutation_summary,
    compact_selected_family_detail,
    compact_ui_summary,
    diagnostics_rows,
    payload_size_bytes,
    settings_summary,
    stable_etag,
    strategy_page_summary,
    ui_delta_messages,
)
from backend.tests.test_candidate_paper_portfolio import candidate_plan


def test_v6_summary_excludes_heavy_detail_and_is_less_than_half_dashboard() -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.DEMO_FIXTURE,
        clock=DeterministicClock(),
        run_id="run-v6-summary",
    )
    runtime.boot_fixture(100)
    dashboard = runtime.dashboard()
    assert dashboard["system"]["app_version"] == APP_VERSION

    summary = compact_ui_summary(dashboard)

    assert summary["paper_activity"] == dashboard["paper_activity"]
    assert summary["paper_only"] is True
    assert summary["real_orders_enabled"] is False
    assert summary["auth_required"] is False
    assert summary["private_api_enabled"] is False
    assert summary["api_key_enabled"] is False
    assert summary["wallet_enabled"] is False
    assert summary["runtime_ai_order_decision_enabled"] is False
    assert summary["funding_readiness"] == "NOT_READY"
    assert dashboard["system"]["private_api_enabled"] is False
    assert dashboard["system"]["api_key_enabled"] is False
    assert dashboard["system"]["wallet_enabled"] is False
    assert dashboard["system"]["runtime_ai_order_decision_enabled"] is False
    assert "history" not in summary
    assert "strategies" not in summary
    assert "league_accounts" not in summary
    assert len(summary["scanner"]) <= 10
    assert payload_size_bytes(summary) < payload_size_bytes(dashboard) * 0.5

    strategy_summary = strategy_page_summary(dashboard)
    assert strategy_summary["strategy_count"] == 3
    assert strategy_summary["league_account_count"] == 6
    assert strategy_summary["enabled_directional_entry_candidate_count"] == 6
    assert strategy_summary["paper_only"] is True
    assert strategy_summary["real_orders_enabled"] is False
    assert strategy_summary["auth_required"] is False
    assert strategy_summary["private_api_enabled"] is False
    assert strategy_summary["api_key_enabled"] is False
    assert strategy_summary["wallet_enabled"] is False
    assert strategy_summary["runtime_ai_order_decision_enabled"] is False
    assert strategy_summary["funding_readiness"] == "NOT_READY"
    assert payload_size_bytes(strategy_summary) < payload_size_bytes(dashboard) * 0.35
    assert all(
        "entry_rules_ko" not in row
        and "exit_rules_ko" not in row
        and "governance" not in row
        and "settings_revision" not in row
        for row in strategy_summary["strategies"]
    )
    assert all(
        "windows" not in report
        and "metric_status" not in report
        and "regime_contributions" not in report
        for row in strategy_summary["strategies"]
        for report in row["performance"].values()
    )
    assert all(
        "profile_unique_opportunity_count" in report
        for row in strategy_summary["strategies"]
        for report in row["performance"].values()
    )


def test_v6_paper_activity_separates_kst_today_pnl_and_strategy_opportunities() -> None:
    now_ms = 1_750_000_000_000
    runtime = PaperRuntime(
        mode=RuntimeMode.READY,
        clock=DeterministicClock(current_utc_ms=now_ms),
        run_id="run-v6-paper-activity",
    )
    runtime._historical_all_main_trades = (
        {
            "run_id": runtime.run_id,
            "trade_id": "main-today",
            "sample_type": "LIVE_PUBLIC",
            "exit_ts_ms": now_ms - 60_000,
            "net_pnl": "1.25",
        },
        {
            "run_id": runtime.run_id,
            "trade_id": "main-prior-day",
            "sample_type": "LIVE_PUBLIC",
            "exit_ts_ms": now_ms - 30 * 60 * 60 * 1_000,
            "net_pnl": "9.00",
        },
    )
    strategy_id = "TREND_PULLBACK_RECLAIM_15M_V2"
    shadow_rows = []
    for opportunity_id, exit_ts_ms in (
        ("opportunity-today", now_ms - 30_000),
        ("opportunity-prior-day", now_ms - 30 * 60 * 60 * 1_000),
    ):
        for profile in ("BASE", "STRESS"):
            shadow_rows.append(
                {
                    "run_id": runtime.run_id,
                    "trade_id": f"{opportunity_id}-{profile.lower()}",
                    "candidate_id": opportunity_id,
                    "opportunity_id": opportunity_id,
                    "strategy_id": strategy_id,
                    "strategy_version": STRATEGY_VERSION,
                    "sample_type": "LIVE_PUBLIC",
                    "account_scope": "LEAGUE",
                    "account_id": f"{strategy_id}:{profile}",
                    "profile": profile,
                    "symbol": "BTCUSDT",
                    "side": "LONG",
                    "entry_ts_ms": exit_ts_ms - 60_000,
                    "exit_ts_ms": exit_ts_ms,
                    "net_pnl": "0.10",
                }
            )
    runtime._historical_all_shadow_trades = tuple(shadow_rows)

    activity = runtime.paper_activity_summary()

    assert activity["shared_run_completed_trades"] == 2
    assert activity["shared_today_completed_trades"] == 1
    assert activity["shared_today_realized_pnl_usdt"] == "1.25"
    assert activity["strategy_current_raw_result_rows"] == 4
    assert activity["strategy_current_unique_opportunities"] == 2
    assert activity["strategy_today_raw_result_rows"] == 2
    assert activity["strategy_today_unique_opportunities"] == 1
    assert activity["strategy_grouping_status"] == "PROVEN"


def test_v6_summary_preserves_main_only_pending_exposure_after_pause() -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.DEMO_FIXTURE,
        clock=DeterministicClock(),
        run_id="run-v6-main-pending",
    )
    plan = replace(
        candidate_plan(),
        run_id=runtime.run_id,
        shadow_eligible=False,
    )
    runtime.paper_portfolio.offer((plan,), entries_paused=False)
    runtime.set_paused(True, expected_revision=0, reason="V6_MAIN_PENDING_REGRESSION")

    dashboard = runtime.dashboard()
    summary = compact_ui_summary(dashboard)

    assert dashboard["position"] is None
    assert dashboard["focus_positions"] == []
    assert dashboard["league_positions"] == []
    assert sum(row["pending_entries"] for row in dashboard["league_accounts"]) == 0
    assert dashboard["main_pending_entry_count"] == 1
    assert dashboard["league_pending_entry_count"] == 0
    assert dashboard["total_pending_entry_count"] == 1
    assert dashboard["total_open_position_count"] == 0
    assert dashboard["paper_portfolio_flat"] is False
    assert summary["main_pending_entry_count"] == 1
    assert summary["total_pending_entry_count"] == 1
    assert summary["paper_portfolio_flat"] is False


def test_v6_settings_hide_raw_diagnostics_by_default() -> None:
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=DeterministicClock())
    dashboard = runtime.dashboard()

    simple = settings_summary(dashboard)
    diagnostics = diagnostics_rows(dashboard)

    assert simple["funding_readiness"] == "NOT_READY"
    assert simple["safety"]["private_api_enabled"] is False
    assert simple["safety"]["api_key_enabled"] is False
    assert simple["safety"]["wallet_enabled"] is False
    assert simple["safety"]["runtime_ai_order_decision_enabled"] is False
    assert simple["local_preferences"]["research_detail_default"] is False
    assert simple["local_preferences"]["research_detail_affects_execution"] is False
    assert simple["autostart"] == {
        "state": "NOT_PROVEN",
        "paper_state_recovery_reported": True,
        "launch_agent_verified": False,
        "read_only": True,
        "evidence_source": "LAUNCH_AGENT_NOT_INSPECTED",
        "evidence_ko": (
            "이 화면에서는 macOS LaunchAgent 등록 상태를 조회하거나 변경하지 않았습니다. "
            "PAPER 상태 자동 복구 보고값은 로그인·재부팅 자동 시작의 증거가 아닙니다."
        ),
    }
    assert "raw" not in simple
    assert diagnostics["raw"]
    assert all(
        {"key", "label_ko", "value", "severity", "user_visible", "group"} <= row.keys()
        for row in diagnostics["rows"]
    )
    automatic_recovery = next(
        row for row in diagnostics["rows"] if row["key"] == "automatic_recovery_enabled"
    )
    assert automatic_recovery["label_ko"] == "PAPER 상태 자동 복구 계약"
    assert automatic_recovery["group"] == "RUNTIME"
    startup_recovery = next(
        row for row in diagnostics["rows"] if row["key"] == "startup_recovery_state"
    )
    assert startup_recovery["label_ko"] == "\uc2dc\uc791 \ubcf5\uad6c \uacb0\uacfc"
    paper_transition = next(
        row for row in diagnostics["rows"] if row["key"] == "last_paper_transition_state"
    )
    assert paper_transition["label_ko"] == "\ub9c8\uc9c0\ub9c9 PAPER \uc804\ud658 \uacb0\uacfc"


def test_v6_compact_settings_and_diagnostics_preserve_unsafe_source_truth() -> None:
    snapshot = {
        "status": {
            "run_id": "run-unsafe-fixture",
            "mode": "READY",
            "venue": "NONE",
            "real_orders_enabled": True,
            "auth_required": True,
        },
        "system": {
            "private_api_enabled": True,
            "api_key_enabled": True,
            "wallet_enabled": True,
            "runtime_ai_order_decision_enabled": True,
            "funding_readiness": "READY",
        },
        "paper_entry_intent": {"state": "USER_PAUSED", "revision": 0},
        "risk": {"paper_only": False, "active_locks": []},
    }

    compact = compact_ui_summary(snapshot)
    strategies = strategy_page_summary(snapshot)
    settings = settings_summary(snapshot)
    diagnostics = diagnostics_rows(snapshot)

    for payload in (compact, strategies, diagnostics):
        assert payload["paper_only"] is False
        assert payload["real_orders_enabled"] is True
        assert payload["auth_required"] is True
        assert payload["private_api_enabled"] is True
        assert payload["api_key_enabled"] is True
        assert payload["wallet_enabled"] is True
        assert payload["runtime_ai_order_decision_enabled"] is True
        assert payload["funding_readiness"] == "READY"
    assert settings["safety"] == {
        "paper_only": False,
        "real_orders_enabled": True,
        "auth_required": True,
        "private_api_enabled": True,
        "api_key_enabled": True,
        "wallet_enabled": True,
        "runtime_ai_order_decision_enabled": True,
        "entry_state": "USER_PAUSED",
        "entry_revision": 0,
        "active_locks": [],
    }
    assert settings["funding_readiness"] == "READY"


def test_v6_missing_safety_truth_is_not_reported_as_safe() -> None:
    compact = compact_ui_summary({"status": {}, "system": {}})
    strategies = strategy_page_summary({"status": {}, "system": {}})
    settings = settings_summary({"status": {}, "system": {}})
    diagnostics = diagnostics_rows({"status": {}, "system": {}})

    for payload in (compact, strategies, diagnostics):
        assert payload["paper_only"] == "NOT_PROVEN"
    assert compact["real_orders_enabled"] == "NOT_PROVEN"
    assert compact["funding_readiness"] == "NOT_PROVEN"
    assert settings["safety"]["paper_only"] == "NOT_PROVEN"
    assert settings["safety"]["auth_required"] == "NOT_PROVEN"
    assert diagnostics["private_api_enabled"] == "NOT_PROVEN"


def test_v6_missing_paper_only_is_not_inferred_from_other_safe_fields() -> None:
    snapshot = {
        "status": {"real_orders_enabled": False, "auth_required": False},
        "system": {
            "private_api_enabled": False,
            "api_key_enabled": False,
            "wallet_enabled": False,
            "runtime_ai_order_decision_enabled": False,
            "funding_readiness": "NOT_READY",
        },
    }

    compact = compact_ui_summary(snapshot)
    strategies = strategy_page_summary(snapshot)
    settings = settings_summary(snapshot)
    diagnostics = diagnostics_rows(snapshot)

    for payload in (compact, strategies, diagnostics):
        assert payload["paper_only"] == "NOT_PROVEN"
        assert payload["real_orders_enabled"] is False
        assert payload["private_api_enabled"] is False
        assert payload["funding_readiness"] == "NOT_READY"
    assert settings["safety"]["paper_only"] == "NOT_PROVEN"
    assert settings["safety"]["real_orders_enabled"] is False
    assert settings["safety"]["private_api_enabled"] is False
    assert settings["funding_readiness"] == "NOT_READY"


def test_v6_conflicting_nested_safety_truth_is_never_masked_by_safe_top_level() -> None:
    snapshot = {
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "private_api_enabled": False,
        "api_key_enabled": False,
        "wallet_enabled": False,
        "runtime_ai_order_decision_enabled": False,
        "funding_readiness": "NOT_READY",
        "status": {
            "real_orders_enabled": False,
            "auth_required": False,
        },
        "system": {
            "private_api_enabled": True,
            "api_key_enabled": True,
            "wallet_enabled": True,
            "runtime_ai_order_decision_enabled": True,
            "funding_readiness": "READY",
        },
        "risk": {"paper_only": True, "active_locks": []},
    }

    compact = compact_ui_summary(snapshot)
    strategies = strategy_page_summary(snapshot)
    settings = settings_summary(snapshot)
    diagnostics = diagnostics_rows(snapshot)

    for payload in (compact, strategies, diagnostics):
        assert payload["paper_only"] is True
        assert payload["real_orders_enabled"] is False
        assert payload["auth_required"] is False
        assert payload["private_api_enabled"] == "NOT_PROVEN"
        assert payload["api_key_enabled"] == "NOT_PROVEN"
        assert payload["wallet_enabled"] == "NOT_PROVEN"
        assert payload["runtime_ai_order_decision_enabled"] == "NOT_PROVEN"
        assert payload["funding_readiness"] == "NOT_PROVEN"
    assert settings["safety"]["paper_only"] is True
    assert settings["safety"]["private_api_enabled"] == "NOT_PROVEN"
    assert settings["safety"]["api_key_enabled"] == "NOT_PROVEN"
    assert settings["safety"]["wallet_enabled"] == "NOT_PROVEN"
    assert settings["safety"]["runtime_ai_order_decision_enabled"] == "NOT_PROVEN"
    assert settings["funding_readiness"] == "NOT_PROVEN"


def test_v6_research_source_metadata_is_structured_and_unknown_is_not_proven() -> None:
    registered = research_source_metadata("SRC-OFI-2010")
    unknown = research_source_metadata("SRC-NOT-REGISTERED")

    assert {
        "source_id",
        "title",
        "publisher",
        "date",
        "url",
        "idea_used",
        "our_modification",
        "metadata_status",
    } <= registered.keys()
    assert registered["metadata_status"] == "REGISTERED"
    assert registered["url"] == "https://arxiv.org/abs/1011.6402"
    assert unknown["source_id"] == "SRC-NOT-REGISTERED"
    assert unknown["metadata_status"] == "NOT_PROVEN"
    assert unknown["url"] is None


def test_v6_etag_is_stable_and_content_addressed() -> None:
    first = stable_etag({"families": [{"id": "TREND_PULLBACK"}]})
    reordered = stable_etag({"families": [{"id": "TREND_PULLBACK"}]})
    changed = stable_etag({"families": [{"id": "BREAKOUT_RUNNER"}]})

    assert first == reordered
    assert first != changed
    assert first.startswith('"') and first.endswith('"')


def test_v6_split_summary_settings_diagnostics_and_websocket_api() -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.DEMO_FIXTURE,
        clock=DeterministicClock(),
        run_id="run-v6-api",
    )
    runtime.boot_fixture(30)

    with TestClient(create_app(runtime)) as client:
        dashboard = client.get("/api/dashboard")
        summary = client.get("/api/ui/summary")
        strategies = client.get("/api/strategies/summary")
        strategies_cached = client.get(
            "/api/strategies/summary",
            headers={"If-None-Match": strategies.headers["etag"]},
        )
        settings = client.get("/api/settings/summary")
        diagnostics = client.get("/api/diagnostics")
        with client.websocket_connect("/ws/ui") as websocket:
            message = websocket.receive_json()
            runtime.set_paused(
                True,
                expected_revision=0,
                actor="USER_UI",
                reason="USER_V6_WS_TEST_PAUSE",
            )
            summary_delta = websocket.receive_json()
            websocket.send_json(
                {"type": "select_family", "family_id": "TREND_PULLBACK"}
            )
            selected = websocket.receive_json()
            after_selected = [websocket.receive_json()]
            while after_selected[-1]["type"] != "heartbeat" and len(after_selected) < 3:
                after_selected.append(websocket.receive_json())

    assert dashboard.status_code == 200
    assert summary.status_code == 200
    assert strategies.status_code == 200
    assert strategies_cached.status_code == 304
    assert strategies.headers["cache-control"] == "private, max-age=2"
    assert settings.status_code == 200
    assert diagnostics.status_code == 200
    assert len(summary.content) < len(dashboard.content) * 0.5
    assert len(strategies.content) < len(dashboard.content) * 0.35
    assert message["type"] == "snapshot"
    assert message["schema_version"] == 1
    assert message["sequence"] == 1
    assert message["data"]["status"]["run_id"] == "run-v6-api"
    assert message["data"]["real_orders_enabled"] is False
    assert "history" not in message["data"]
    assert summary_delta["type"] == "summary_delta"
    assert summary_delta["sequence"] == 2
    assert summary_delta["data"]["paused"] is True
    assert summary_delta["data"]["paper_entry_intent"]["state"] == "USER_PAUSED"
    assert selected["type"] == "selected_detail_delta"
    assert selected["sequence"] == 3
    assert selected["data"]["family_id"] == "TREND_PULLBACK"
    assert selected["data"]["detail"]["current_variant_id"] == (
        "TREND_PULLBACK_RECLAIM_15M_V2"
    )
    assert "conditions" not in selected["data"]["detail"]
    assert "history" not in selected["data"]["detail"]
    assert all(
        "entry_rules_ko" not in variant["runtime_state"]
        for variant in selected["data"]["detail"]["variants"]
    )
    assert after_selected[-1]["type"] == "heartbeat"
    assert [row["sequence"] for row in after_selected] == list(
        range(4, 4 + len(after_selected))
    )


def test_v6_websocket_delta_partition_and_unchanged_contract() -> None:
    runtime = PaperRuntime(
        mode=RuntimeMode.DEMO_FIXTURE,
        clock=DeterministicClock(),
        run_id="run-v6-delta",
    )
    runtime.boot_fixture(30)
    previous = compact_ui_summary(runtime.dashboard())
    current = deepcopy(previous)
    current["status"] = dict(current["status"]) | {"run_id": "run-v6-delta-next"}
    current["position"] = {"symbol": "BTCUSDT", "side": "LONG"}
    strategy_rows = current["strategy_state"]
    assert isinstance(strategy_rows, list) and strategy_rows
    strategy_rows[0] = dict(strategy_rows[0]) | {"mode": "OFF"}

    messages = ui_delta_messages(previous, current)

    assert [message["type"] for message in messages] == [
        "summary_delta",
        "position_delta",
        "strategy_row_delta",
    ]
    assert messages[0]["data"] == {"status": current["status"]}
    assert messages[1]["data"] == {"position": current["position"]}
    assert messages[2]["data"]["rows"][0]["mode"] == "OFF"
    assert "history" not in messages[0]["data"]
    assert "conditions" not in messages[2]["data"]["rows"][0]
    assert ui_delta_messages(current, current) == []


def test_v6_websocket_delta_never_hides_safety_contract_changes() -> None:
    previous = {
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "private_api_enabled": False,
        "api_key_enabled": False,
        "wallet_enabled": False,
        "runtime_ai_order_decision_enabled": False,
        "funding_readiness": "NOT_READY",
    }
    current = {
        "paper_only": False,
        "real_orders_enabled": True,
        "auth_required": True,
        "private_api_enabled": True,
        "api_key_enabled": True,
        "wallet_enabled": True,
        "runtime_ai_order_decision_enabled": True,
        "funding_readiness": "READY",
    }

    messages = ui_delta_messages(previous, current)

    assert messages == [{"type": "summary_delta", "data": current}]

    missing_previous = dict(previous)
    del missing_previous["wallet_enabled"]
    missing_delta = ui_delta_messages(missing_previous, current)
    assert missing_delta[0]["data"]["wallet_enabled"] is True


def test_v6_chart_delta_uses_small_upserts_and_never_repeats_full_chart() -> None:
    points = [
        {
            "index": index,
            "ts_ms": 1_000 + index,
            "bid": 100 + index,
            "ask": 101 + index,
            "mid": 100.5 + index,
            "microprice": 100.6 + index,
        }
        for index in range(200)
    ]
    previous = {
        "chart": {
            "symbol": "BTCUSDT",
            "interval": "3m",
            "fixture": False,
            "points": points,
            "candles": [],
            "lines": {"entry": None, "take_profit": None, "stop": None},
        }
    }
    current = deepcopy(previous)
    current["chart"]["points"] = [
        *points[1:],
        dict(points[-1]) | {"index": 200, "ts_ms": 1_200, "bid": 301},
    ]

    messages = ui_delta_messages(previous, current)

    assert [message["type"] for message in messages] == ["chart_delta"]
    delta = messages[0]["data"]
    assert delta["refresh_required"] is False
    assert delta["removed_point_ts_ms"] == [1_000]
    assert [row["ts_ms"] for row in delta["point_upserts"]] == [1_200]
    assert "chart" not in delta
    assert payload_size_bytes(delta) < payload_size_bytes(current["chart"]) * 0.1

    changed_selection = deepcopy(current)
    changed_selection["chart"]["symbol"] = "ETHUSDT"
    selection_delta = ui_delta_messages(current, changed_selection)[0]
    assert selection_delta == {
        "type": "chart_delta",
        "data": {
            "symbol": "ETHUSDT",
            "interval": "3m",
            "fixture": False,
            "refresh_required": True,
        },
    }


def test_v6_mutation_summary_omits_chart_scanner_and_heavy_detail() -> None:
    runtime = PaperRuntime(mode=RuntimeMode.DEMO_FIXTURE, clock=DeterministicClock())
    runtime.boot_fixture(30)
    dashboard = runtime.dashboard()

    mutation = compact_mutation_summary(dashboard)

    assert mutation["paper_only"] is True
    assert mutation["real_orders_enabled"] is False
    assert mutation["auth_required"] is False
    assert "chart" not in mutation
    assert "scanner" not in mutation
    assert "history" not in mutation
    assert "strategies" not in mutation
    assert payload_size_bytes(mutation) < payload_size_bytes(dashboard) * 0.2


def test_v6_selected_detail_compaction_drops_heavy_runtime_fields() -> None:
    compact = compact_selected_family_detail(
        {
            "family_id": "TREND_PULLBACK",
            "label_ko": "추세 눌림",
            "current_variant_id": "TREND_PULLBACK_RECLAIM_15M_V2",
            "paper_only": True,
            "real_orders_enabled": False,
            "auth_required": False,
            "private_api_enabled": False,
            "api_key_enabled": False,
            "wallet_enabled": False,
            "runtime_ai_order_decision_enabled": False,
            "funding_readiness": "NOT_READY",
            "conditions": [{"key": "heavy-condition"}],
            "history": [{"trade_id": "heavy-history"}],
            "variants": [
                {
                    "strategy_id": "TREND_PULLBACK_RECLAIM_15M_V2",
                    "family_id": "TREND_PULLBACK",
                    "variant_id": "V2",
                    "is_current_variant": True,
                    "setting": {
                        "mode": "SHADOW",
                        "settings_revision": 3,
                        "research_enabled": True,
                    },
                    "runtime_state": {
                        "strategy_id": "TREND_PULLBACK_RECLAIM_15M_V2",
                        "mode": "SHADOW",
                        "entry_rules_ko": ["heavy-rule"],
                        "performance": {
                            "BASE": {
                                "profile": "BASE",
                                "sample_size": 40,
                                "net_pnl": "10.0",
                                "windows": {"ALL": {"heavy": True}},
                            }
                        },
                    },
                }
            ],
        }
    )

    assert "conditions" not in compact
    assert "history" not in compact
    assert {
        key: compact[key]
        for key in (
            "paper_only",
            "real_orders_enabled",
            "auth_required",
            "private_api_enabled",
            "api_key_enabled",
            "wallet_enabled",
            "runtime_ai_order_decision_enabled",
            "funding_readiness",
        )
    } == {
        "paper_only": True,
        "real_orders_enabled": False,
        "auth_required": False,
        "private_api_enabled": False,
        "api_key_enabled": False,
        "wallet_enabled": False,
        "runtime_ai_order_decision_enabled": False,
        "funding_readiness": "NOT_READY",
    }
    assert compact["variants"][0]["setting"]["research_enabled"] is True
    runtime_state = compact["variants"][0]["runtime_state"]
    assert "entry_rules_ko" not in runtime_state
    assert runtime_state["performance"]["BASE"] == {
        "profile": "BASE",
        "sample_size": 40,
        "net_pnl": "10.0",
    }


def test_v6_family_catalog_detail_conditions_and_cas_research_api() -> None:
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=DeterministicClock())

    with TestClient(create_app(runtime)) as client:
        catalog_response = client.get("/api/strategy-families")
        catalog_payload = catalog_response.json()
        assert catalog_payload["paper_only"] is True
        assert catalog_payload["real_orders_enabled"] is False
        assert catalog_payload["auth_required"] is False
        assert catalog_payload["private_api_enabled"] is False
        assert catalog_payload["api_key_enabled"] is False
        assert catalog_payload["wallet_enabled"] is False
        assert catalog_payload["runtime_ai_order_decision_enabled"] is False
        assert catalog_payload["funding_readiness"] == "NOT_READY"
        assert catalog_payload["inventory"] == {
            "schema": "flowscalper.strategy_inventory.v1",
            "registered_catalog_item_count": 16,
            "runtime_registry_variant_count": 15,
            "enabled_directional_entry_candidate_count": 6,
            "current_family_entry_representative_count": 3,
            "inactive_history_runtime_variant_count": 9,
            "catalog_virtual_filter_count": 1,
            "active_directional_entry_count": 0,
        }
        v9_research = catalog_payload["v9_research"]
        assert v9_research["candidate_count"] == 12
        assert v9_research["monitoring_on_count"] == 12
        assert v9_research["direction_strategy_count"] == 2
        assert v9_research["market_neutral_strategy_count"] == 1
        assert v9_research["runtime_entry_registered_count"] == 0
        assert v9_research["entry_enabled_count"] == 0
        assert v9_research["active_count"] == 0
        assert len(v9_research["candidates"]) == 12
        assert all(row["monitoring_enabled"] for row in v9_research["candidates"])
        assert not any(row["entry_enabled"] for row in v9_research["candidates"])
        assert all(
            row["source_ids"]
            and len(row["source_ids"]) == len(set(row["source_ids"]))
            and all(source_id.startswith("SRC-") for source_id in row["source_ids"])
            for row in v9_research["candidates"]
        )
        dc_candidates = [
            row
            for row in v9_research["candidates"]
            if str(row["candidate_id"]).startswith("DC_OVERSHOOT_")
        ]
        assert len(dc_candidates) == 2
        assert all(
            row["readiness"] == "PARTIAL_SOURCE_NOT_CONNECTED"
            for row in dc_candidates
        )
        semivariance_candidates = [
            row
            for row in v9_research["candidates"]
            if row["candidate_id"]
            in {
                "SEMIVARIANCE_MOMENTUM_REVERSAL_ROUTER_V1",
                "DOWNSIDE_SEMIVARIANCE_RISK_OVERLAY_V1",
            }
        ]
        assert len(semivariance_candidates) == 2
        assert all(
            row["readiness"] == "PARTIAL_SOURCE_NOT_CONNECTED"
            for row in semivariance_candidates
        )
        etag = catalog_response.headers["etag"]
        cached = client.get("/api/strategy-families", headers={"If-None-Match": etag})
        detail = client.get("/api/strategy-families/TREND_PULLBACK")
        conditions = client.get("/api/strategy-families/TREND_PULLBACK/conditions")
        disabled = client.patch(
            "/api/strategy-families/TREND_PULLBACK/research-enabled",
            json={
                "research_enabled": False,
                "expected_revision": 0,
                "reason": "USER_V6_TEST_OFF",
            },
        )
        disabled_catalog = client.get("/api/strategy-families")
        stale = client.patch(
            "/api/strategy-families/TREND_PULLBACK/research-enabled",
            json={
                "research_enabled": True,
                "expected_revision": 0,
                "reason": "USER_V6_TEST_STALE",
            },
        )
        restored = client.patch(
            "/api/strategy-families/TREND_PULLBACK/research-enabled",
            json={
                "research_enabled": True,
                "expected_revision": 1,
                "reason": "USER_V6_TEST_UNDO",
            },
        )
        filter_enabled = client.patch(
            "/api/strategy-families/ORDERFLOW_CONFIRMATION/research-enabled",
            json={
                "research_enabled": True,
                "expected_revision": 0,
                "reason": "USER_V6_TEST_FILTER",
            },
        )

    catalog = catalog_response.json()
    assert catalog_response.status_code == 200
    assert len(catalog["families"]) == 8
    availability = {
        row["family_id"]: row["availability_label_ko"]
        for row in catalog["families"]
    }
    assert availability["POSITIONING_LIQUIDATION"] == "연구 준비"
    assert availability["MARKET_REGIME_FILTERS"] == "라우터 전용"
    assert availability["SESSION_PROFILE"] == "연구 준비"
    assert availability["MARKET_NEUTRAL"] == "엔진 검증 필요"
    assert cached.status_code == 304
    assert cached.content == b""
    assert disabled_catalog.headers["etag"] != etag
    assert disabled_catalog.json()["inventory"][
        "enabled_directional_entry_candidate_count"
    ] == 5
    assert disabled_catalog.json()["inventory"][
        "inactive_history_runtime_variant_count"
    ] == 10
    assert detail.status_code == 200
    assert detail.json()["current_variant_id"] == "TREND_PULLBACK_RECLAIM_15M_V2"
    detail_current = next(
        row for row in detail.json()["variants"] if row["is_current_variant"]
    )
    assert detail_current["research_sources"]
    assert all(
        {
            "source_id",
            "title",
            "publisher",
            "date",
            "url",
            "idea_used",
            "our_modification",
        }
        <= source.keys()
        for source in detail_current["research_sources"]
    )
    assert any(
        row["strategy_id"] == "TREND_PULLBACK_RECLAIM_15M_V3"
        for row in detail.json()["offline_challengers"]
    )
    assert conditions.status_code == 200
    assert conditions.json()["total"] > 0
    assert all(row["status"] == "WAITING_DATA" for row in conditions.json()["conditions"])
    assert disabled.status_code == 200
    current = next(row for row in disabled.json()["variants"] if row["is_current_variant"])
    assert current["setting"]["mode"] == "OFF"
    assert current["setting"]["lifecycle"] == "RESEARCH"
    assert stale.status_code == 409
    assert stale.json()["detail"]["error_code"] == "STRATEGY_SETTINGS_REVISION_CONFLICT"
    assert restored.status_code == 200
    restored_current = next(
        row for row in restored.json()["variants"] if row["is_current_variant"]
    )
    assert restored_current["setting"]["mode"] == "SHADOW"
    assert restored_current["setting"]["settings_revision"] == 2
    assert filter_enabled.status_code == 200
    filter_variant = next(
        row for row in filter_enabled.json()["variants"] if row["is_current_variant"]
    )
    assert filter_variant["strategy_id"] == "ORDERFLOW_CONFIRMATION_FILTER_V2"
    assert filter_variant["setting"]["research_enabled"] is True


def test_v6_trades_api_groups_base_and_stress_as_one_opportunity(
    monkeypatch: MonkeyPatch,
) -> None:
    common = {
        "run_id": "run-v6-trades",
        "opportunity_id": "opportunity-v6-001",
        "strategy": "BREAKOUT_RETEST_30M_V2",
        "strategy_version": "V2",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry_ts_ms": 1_000,
        "exit_ts_ms": 2_000,
        "replay_available": True,
        "mae_r": "-0.25",
        "mfe_r": "1.75",
        "peak_unrealized_usdt": "4.20",
        "giveback_usdt": "0.35",
        "runner_net_pnl_usdt": "1.10",
        "trail_trigger_slippage_usdt": "0.04",
        "trailing_activation_ts_ms": 1_500,
        "runner_started_ts_ms": 1_600,
        "trailing_state_checksum": "checksum-v6",
    }
    rows = [
        {
            **common,
            "trade_id": "trade-base",
            "profile": "BASE",
            "net_pnl": "8.83",
        },
        {
            **common,
            "trade_id": "trade-stress",
            "profile": "STRESS",
            "net_pnl": "6.66",
        },
    ]

    def fake_history_records(self: PaperRuntime, **_: object) -> dict[str, object]:
        return {
            "rows": rows,
            "scope": {"run_scope": "CURRENT", "account_scope": "ALL"},
            "paper_only": True,
            "real_orders_enabled": False,
            "auth_required": False,
        }

    monkeypatch.setattr(PaperRuntime, "history_records", fake_history_records)
    with TestClient(
        create_app(
            PaperRuntime(
                mode=RuntimeMode.READY,
                clock=DeterministicClock(),
                run_id="run-v6-trades",
            )
        )
    ) as client:
        response = client.get("/api/trades")

    assert response.status_code == 200
    payload = response.json()
    assert {
        "unique_opportunities": 1,
        "raw_result_rows": 2,
        "base_result_rows": 1,
        "stress_result_rows": 1,
    }.items() <= payload["counts"].items()
    assert len(payload["opportunities"]) == 1
    opportunity = payload["opportunities"][0]
    assert set(opportunity["profiles"]) == {"BASE", "STRESS"}
    assert opportunity["profiles"]["BASE"]["net_pnl"] == "8.83"
    assert opportunity["profiles"]["STRESS"]["net_pnl"] == "6.66"
    assert opportunity["profiles"]["BASE"]["mae_r"] == "-0.25"
    assert opportunity["profiles"]["BASE"]["trailing_state_checksum"] == "checksum-v6"
    assert opportunity["profiles"]["BASE"]["fill_evidence_state"] == "CURRENT_MAIN_NO_FILL"
    assert opportunity["profiles"]["BASE"]["fills"] == []
    assert opportunity["key"] == {
        "run_id": "run-v6-trades",
        "strategy_id": "BREAKOUT_RETEST_30M_V2",
        "strategy_version": "V2",
        "opportunity_id": "opportunity-v6-001",
        "symbol": "BTCUSDT",
        "side": "LONG",
    }
    assert payload["paper_only"] is True
    assert payload["real_orders_enabled"] is False
    assert payload["auth_required"] is False


def test_v6_trades_limit_is_applied_after_complete_opportunity_grouping(
    monkeypatch: MonkeyPatch,
) -> None:
    def opportunity_rows(
        opportunity_id: str,
        *,
        exit_ts_ms: int,
        partial_base: bool,
    ) -> list[dict[str, object]]:
        common: dict[str, object] = {
            "run_id": "run-v6-limit",
            "opportunity_id": opportunity_id,
            "strategy": "BREAKOUT_RETEST_30M_V2",
            "strategy_version": "V2",
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_ts_ms": exit_ts_ms - 1_000,
            "exit_ts_ms": exit_ts_ms,
        }
        rows = [
            {**common, "trade_id": f"{opportunity_id}-base-1", "profile": "BASE"},
            {**common, "trade_id": f"{opportunity_id}-stress", "profile": "STRESS"},
        ]
        if partial_base:
            rows.append(
                {**common, "trade_id": f"{opportunity_id}-base-2", "profile": "BASE"}
            )
        return rows

    rows = opportunity_rows("opportunity-old", exit_ts_ms=2_000, partial_base=False) + (
        opportunity_rows("opportunity-new", exit_ts_ms=4_000, partial_base=True)
    )
    captured_limit: list[int] = []

    def fake_history_records(self: PaperRuntime, **kwargs: object) -> dict[str, object]:
        captured_limit.append(int(str(kwargs["limit"])))
        return {"rows": rows, "scope": {"account_scope": "ALL"}}

    monkeypatch.setattr(PaperRuntime, "history_records", fake_history_records)
    with TestClient(
        create_app(
            PaperRuntime(
                mode=RuntimeMode.READY,
                clock=DeterministicClock(),
                run_id="run-v6-limit",
            )
        )
    ) as client:
        response = client.get("/api/trades", params={"limit": 1})

    assert response.status_code == 200
    payload = response.json()
    assert captured_limit == [2_000]
    assert payload["counts"]["unique_opportunities"] == 2
    assert payload["counts"]["returned_opportunities"] == 1
    assert payload["opportunities"][0]["key"]["opportunity_id"] == "opportunity-new"
    assert payload["opportunities"][0]["raw_result_row_count"] == 3
    assert payload["opportunities"][0]["partial_exit_row_count"] == 1


def test_v6_trades_marks_the_internal_raw_limit_boundary_not_proven(
    monkeypatch: MonkeyPatch,
) -> None:
    row = {
        "run_id": "run-v6-boundary",
        "opportunity_id": "opportunity-v6-boundary",
        "strategy": "BREAKOUT_RETEST_30M_V2",
        "strategy_version": "V2",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry_ts_ms": 1_000,
        "exit_ts_ms": 2_000,
        "trade_id": "trade-v6-boundary",
        "profile": "BASE",
    }

    def fake_history_records(self: PaperRuntime, **_: object) -> dict[str, object]:
        return {
            "rows": [row],
            "scope": {"limit": 2_000, "returned_count": 2_000},
        }

    monkeypatch.setattr(PaperRuntime, "history_records", fake_history_records)
    with TestClient(create_app(PaperRuntime(mode=RuntimeMode.READY))) as client:
        response = client.get("/api/trades", params={"limit": 1})

    assert response.status_code == 200
    payload = response.json()
    assert payload["grouping_status"] == "NOT_PROVEN"
    assert payload["source_status"] == "NOT_PROVEN_RAW_LIMIT_BOUNDARY"
    assert payload["scope"]["source_grouping_complete"] is False


def test_v6_trades_separates_main_and_league_profile_results(
    monkeypatch: MonkeyPatch,
) -> None:
    common = {
        "run_id": "run-v6-account",
        "opportunity_id": "opportunity-v6-account",
        "strategy": "BREAKOUT_RETEST_30M_V2",
        "strategy_version": "V2",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry_ts_ms": 1_000,
        "exit_ts_ms": 2_000,
        "profile": "BASE",
    }
    rows = [
        {
            **common,
            "trade_id": "main-base",
            "account_scope": "MAIN",
            "account_id": "SHARED_PAPER",
            "net_pnl": "9.00",
        },
        {
            **common,
            "trade_id": "league-base",
            "account_scope": "LEAGUE",
            "account_id": "BREAKOUT_RETEST_30M_V2:BASE",
            "net_pnl": "1.00",
        },
        {
            **common,
            "trade_id": "league-stress",
            "account_scope": "LEAGUE",
            "account_id": "BREAKOUT_RETEST_30M_V2:STRESS",
            "profile": "STRESS",
            "net_pnl": "0.50",
        },
    ]

    def fake_history_records(self: PaperRuntime, **_: object) -> dict[str, object]:
        return {"rows": rows, "scope": {"account_scope": "ALL"}}

    monkeypatch.setattr(PaperRuntime, "history_records", fake_history_records)
    with TestClient(create_app(PaperRuntime(mode=RuntimeMode.READY))) as client:
        response = client.get("/api/trades")

    assert response.status_code == 200
    opportunity = response.json()["opportunities"][0]
    assert opportunity["partial_exit_row_count"] == 0
    assert set(opportunity["profiles"]) == {"BASE"}
    assert opportunity["profiles"]["BASE"]["net_pnl"] == "9.00"
    assert opportunity["profile_account_refs"]["BASE"] == {
        "account_scope": "MAIN",
        "account_id": "SHARED_PAPER",
    }
    assert [account["account_id"] for account in opportunity["accounts"]] == [
        "SHARED_PAPER",
        "BREAKOUT_RETEST_30M_V2:BASE",
        "BREAKOUT_RETEST_30M_V2:STRESS",
    ]
    assert [group["account_scope"] for group in opportunity["account_groups"]] == [
        "MAIN",
        "LEAGUE",
    ]
    assert set(opportunity["account_groups"][1]["profiles"]) == {"BASE", "STRESS"}
    assert opportunity["account_groups"][1]["profiles"]["BASE"]["net_pnl"] == "1.00"
    assert (
        opportunity["account_groups"][1]["profiles"]["BASE"]["fill_evidence_state"]
        == "SHADOW_UNAVAILABLE"
    )
    assert opportunity["account_groups"][1]["profiles"]["BASE"]["fills"] == []


def test_v6_trades_fill_reconciliation_mismatch_fails_closed(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    row = {
        "run_id": "run-v6-fill-mismatch",
        "trade_id": "trade-v6-fill-mismatch",
        "opportunity_id": "opportunity-v6-fill-mismatch",
        "strategy": "BREAKOUT_RETEST_30M_V2",
        "strategy_version": "V2",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry_ts_ms": 1_000,
        "exit_ts_ms": 2_000,
        "profile": "BASE",
        "account_scope": "MAIN",
    }

    def fake_history_records(self: PaperRuntime, **_: object) -> dict[str, object]:
        return {"rows": [row], "scope": {"account_scope": "MAIN"}}

    ledger = SQLiteLedger(tmp_path / "fill-mismatch.sqlite3")
    runtime = PaperRuntime(mode=RuntimeMode.READY, ledger=ledger)
    monkeypatch.setattr(PaperRuntime, "history_records", fake_history_records)

    def fail_closed(_: object) -> dict[tuple[str, str], dict[str, object]]:
        raise LedgerInvariantError("main 거래와 fill 비용 합계가 일치하지 않습니다.")

    monkeypatch.setattr(ledger, "list_trade_fill_evidence", fail_closed)
    with TestClient(create_app(runtime)) as client:
        response = client.get("/api/trades")

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "error_code": "TRADE_FILL_LEDGER_INVARIANT",
        "error_message_ko": "main 거래와 fill 비용 합계가 일치하지 않습니다.",
        "retryable": False,
    }
    ledger.close()


def test_v6_trades_serializes_verified_main_fills_in_chronological_order(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    row = {
        "run_id": "run-v6-fills",
        "trade_id": "trade-v6-fills",
        "opportunity_id": "opportunity-v6-fills",
        "strategy": "BREAKOUT_RETEST_30M_V2",
        "strategy_version": "V2",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry_ts_ms": 1_000,
        "exit_ts_ms": 2_000,
        "profile": "BASE",
        "account_scope": "MAIN",
        "account_id": "SHARED_PAPER",
    }
    verified_fills = [
        {
            "fill_id": "fill-entry",
            "order_id": "order-entry",
            "ts_ms": 1_200,
            "side": "BUY",
            "intent": "ENTRY_IOC",
            "price": "100.10",
            "quantity": "1",
            "fee_usdt": "0.06",
            "slippage_usdt": "0.08",
        },
        {
            "fill_id": "fill-exit",
            "order_id": "order-exit",
            "ts_ms": 2_000,
            "side": "SELL",
            "intent": "TAKE_PROFIT",
            "price": "101.90",
            "quantity": "1",
            "fee_usdt": "0.0612",
            "slippage_usdt": "0.12",
        },
    ]

    def fake_history_records(self: PaperRuntime, **_: object) -> dict[str, object]:
        return {"rows": [row], "scope": {"account_scope": "MAIN"}}

    ledger = SQLiteLedger(tmp_path / "verified-fills.sqlite3")
    runtime = PaperRuntime(mode=RuntimeMode.READY, ledger=ledger)
    monkeypatch.setattr(PaperRuntime, "history_records", fake_history_records)
    monkeypatch.setattr(
        ledger,
        "list_trade_fill_evidence",
        lambda keys: {
            ("run-v6-fills", "trade-v6-fills"): {
                "fill_evidence_state": "PRESENT",
                "fill_evidence_reason_ko": "원시 PAPER 체결과 거래 비용 합계를 확인했습니다.",
                "fills": verified_fills,
            }
        }
        if keys == [("run-v6-fills", "trade-v6-fills")]
        else {},
    )
    with TestClient(create_app(runtime)) as client:
        response = client.get("/api/trades")

    assert response.status_code == 200
    base = response.json()["opportunities"][0]["profiles"]["BASE"]
    assert base["fill_evidence_state"] == "PRESENT"
    assert base["fills"] == verified_fills
    ledger.close()


def test_v6_trades_quarantines_unknown_legacy_row_instead_of_failing(
    monkeypatch: MonkeyPatch,
) -> None:
    legacy_row = {
        "run_id": "run-v6-legacy",
        "trade_id": "legacy-unknown",
        "strategy": "UNKNOWN",
        "strategy_version": "UNKNOWN",
        "opportunity_id": None,
        "candidate_id": None,
        "signal_event_id": None,
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry_ts_ms": 1_000,
        "exit_ts_ms": 2_000,
        "profile": "BASE",
    }

    def fake_history_records(self: PaperRuntime, **_: object) -> dict[str, object]:
        return {"rows": [legacy_row], "scope": {"version_scope": "ALL"}}

    monkeypatch.setattr(PaperRuntime, "history_records", fake_history_records)
    with TestClient(create_app(PaperRuntime(mode=RuntimeMode.READY))) as client:
        response = client.get("/api/trades", params={"version_scope": "ALL"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["opportunities"] == []
    assert payload["grouping_status"] == "NOT_PROVEN"
    assert payload["counts"]["unresolved_result_rows"] == 1
    assert payload["unresolved"][0]["status"] == "NOT_PROVEN"
    assert payload["unresolved"][0]["reason_code"] == (
        "MISSING_VERIFIABLE_OPPORTUNITY_LINKAGE"
    )


def test_history_normalization_preserves_metrics_for_persisted_and_current_rows(
    tmp_path: Path,
) -> None:
    metric_fields = {
        "mae_r": "-0.30",
        "mfe_r": "1.80",
        "peak_unrealized_usdt": "4.20",
        "giveback_usdt": "0.35",
        "runner_net_pnl_usdt": "1.10",
        "trail_trigger_slippage_usdt": "0.04",
        "trailing_activation_ts_ms": 1_500,
        "runner_started_ts_ms": 1_600,
        "trailing_state_checksum": "metric-checksum",
    }

    def raw_trade(run_id: str, trade_id: str) -> dict[str, object]:
        return {
            "run_id": run_id,
            "trade_id": trade_id,
            "venue": "BINANCE_USDM",
            "symbol": "BTCUSDT",
            "strategy_id": "BREAKOUT_RETEST_30M_V2",
            "strategy_version": STRATEGY_VERSION,
            "side": "LONG",
            "entry_price": "100",
            "exit_price": "102",
            "initial_stop": "99",
            "take_profit": "102",
            "candidate_id": f"candidate-{trade_id}",
            "signal_event_id": f"signal-{trade_id}",
            "quantity": "1",
            "exit_reason": "TAKE_PROFIT",
            "gross_pnl_usdt": "2.0",
            "fees_usdt": "0.2",
            "slippage_usdt": "0.1",
            "net_pnl_usdt": "1.7",
            "entry_ts_ms": 1_000,
            "exit_ts_ms": 2_000,
            "holding_ms": 1_000,
            "profile": "BASE",
            "sample_type": "LIVE_PUBLIC",
            **metric_fields,
        }

    ledger = SQLiteLedger(tmp_path / "v6-trade-metrics.sqlite3")
    ledger.start_run(
        "run-persisted-metrics",
        mode="REPLAY",
        venue="BINANCE_USDM",
        config={"strategy_version": STRATEGY_VERSION},
        started_ts_ms=1,
    )
    ledger.record_trade(raw_trade("run-persisted-metrics", "persisted-metrics"))
    persisted_runtime = PaperRuntime(
        mode=RuntimeMode.REPLAY,
        run_id="run-persisted-metrics",
        ledger=ledger,
    )
    persisted = persisted_runtime.history_records(limit=10)["rows"][0]

    current_runtime = PaperRuntime(
        mode=RuntimeMode.LIVE_SHADOW_PAPER,
        run_id="run-current-metrics",
    )
    current_runtime._historical_all_main_trades = (
        raw_trade("run-current-metrics", "current-metrics"),
    )
    current_runtime.dashboard_trade_cache_ready = True
    current = current_runtime.history_records(limit=10)["rows"][0]

    for field, expected in metric_fields.items():
        assert persisted[field] == expected
        assert current[field] == expected
    ledger.close()
