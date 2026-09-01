# V10 연구 후보가 기본 UI·runtime 표면으로 새지 않는 계약을 검증한다.

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend.app.clocks import TestClock as DeterministicClock
from backend.app.domain.models import RuntimeMode
from backend.app.main import create_app
from backend.app.runtime import PaperRuntime

V10_CANDIDATE_IDS = frozenset(
    {
        "SWING_MULTI_HORIZON_TREND_4H1D_V1",
        "DAILY_DONCHIAN_RETEST_1D4H_V1",
        "CFTC_CME_BITCOIN_CROWDING_FILTER_V1",
        "CRYPTO_FUTURES_CURVE_REGIME_FILTER_V1",
        "RESIDUAL_14D_RELATIVE_STRENGTH_V1",
        "BASIS_MOMENTUM_CROSS_SECTIONAL_RESEARCH_V1",
    }
)
V10_REJECTED_IDS = frozenset({"CME_WEEKEND_GAP_FILL"})


def _serialized(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def test_v10_candidates_stay_out_of_default_runtime_and_ui_apis() -> None:
    runtime = PaperRuntime(mode=RuntimeMode.READY, clock=DeterministicClock())

    assert len(V10_CANDIDATE_IDS) == 6
    assert len(runtime.strategy_registry.strategy_ids) == 15
    assert V10_CANDIDATE_IDS.isdisjoint(runtime.strategy_registry.strategy_ids)
    assert V10_REJECTED_IDS.isdisjoint(runtime.strategy_registry.strategy_ids)

    with TestClient(create_app(runtime)) as client:
        family_response = client.get("/api/strategy-families")
        strategy_response = client.get("/api/strategies/summary")
        ui_response = client.get("/api/ui/summary")

    assert family_response.status_code == 200
    assert strategy_response.status_code == 200
    assert ui_response.status_code == 200

    family_payload = family_response.json()
    strategy_payload = strategy_response.json()
    ui_payload = ui_response.json()

    assert family_payload["inventory"]["registered_catalog_item_count"] == 16
    assert family_payload["inventory"]["runtime_registry_variant_count"] == 15
    assert family_payload["inventory"]["current_family_entry_representative_count"] == 3
    assert family_payload["inventory"]["active_directional_entry_count"] == 0
    assert family_payload["v9_research"]["candidate_count"] == 12
    assert family_payload["v9_research"]["runtime_entry_registered_count"] == 0
    assert family_payload["v9_research"]["active_count"] == 0
    assert "v10_research" not in family_payload
    assert strategy_payload["strategy_count"] == 3

    default_payload_text = _serialized(
        {
            "strategy_families": family_payload,
            "strategy_summary": strategy_payload,
            "ui_summary": ui_payload,
        }
    )
    for candidate_id in V10_CANDIDATE_IDS | V10_REJECTED_IDS:
        assert candidate_id not in default_payload_text
