# V9 통합지침의 연구 Source ID가 누락 없이 구조화되는지 검증한다.

from __future__ import annotations

from backend.app.research.source_metadata import research_source_metadata_rows

V9_SOURCE_IDS = (
    "SRC-DC-ALGO-TRADING-2022",
    "SRC-DC-ACTUAL-CONFIRMATION-2024",
    "SRC-DC-TSFDC-2018",
    "SRC-DC-MULTI-THRESHOLD-2026",
    "SRC-REALIZED-SEMIVARIANCE-MOMREV-2023",
    "SRC-BTC-JUMP-STRUCTURAL-BREAK-2020",
    "SRC-CRYPTO-JUMP-TICK-2024",
    "SRC-INTRAWEEK-PERIODICITY-JUMP",
    "SRC-CRYPTO-ASYMMETRIC-PERSISTENCE-2026",
    "SRC-COPULA-CRYPTO-PAIRS-2025",
    "SRC-FDR-BH-1995",
    "SRC-FALSE-DISCOVERIES-FINANCE-2020",
    "SRC-PHACKING-TRADING-STRATEGIES",
    "SRC-E-BH-2022",
    "SRC-EVALUE-DYNAMIC-VOLATILITY-2025",
    "SRC-ANYTIME-VALID-2026",
    "SRC-HIERARCHICAL-SHRINKAGE-2013",
    "SRC-DC-MULTIOBJECTIVE-2026",
)


def test_v9_source_registry_contains_all_declared_ids_without_profit_claims() -> None:
    rows = research_source_metadata_rows(V9_SOURCE_IDS)

    assert [row["source_id"] for row in rows] == list(V9_SOURCE_IDS)
    assert all(row["metadata_status"] == "REGISTERED_FROM_V9_SPEC" for row in rows)
    assert all(row["url"] or row["source_id"] == "SRC-PHACKING-TRADING-STRATEGIES" for row in rows)
    assert all("\uc218\uc775\uc131" not in str(row["idea_used"]) for row in rows)
    assert any("BLOCKED_ENGINE" in str(row["our_modification"]) for row in rows)
    assert any("\uc790\ub3d9 \uc2b9\uaca9" in str(row["our_modification"]) for row in rows)
