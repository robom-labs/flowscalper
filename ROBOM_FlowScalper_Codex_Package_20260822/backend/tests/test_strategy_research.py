"""시간순 전략 연구 결과가 실제 A~J 런타임 기준선을 빠뜨리지 않는지 검증한다."""

from backend.app.strategies.registry import StrategyRegistry
from scripts.research_strategy_revision import RUNTIME_VARIANT_NAMES, summarize


def test_empty_research_summary_keeps_every_runtime_strategy_baseline() -> None:
    summary = summarize([])

    assert set(RUNTIME_VARIANT_NAMES).issubset(summary)
    assert len(RUNTIME_VARIANT_NAMES) == len(StrategyRegistry().strategy_ids)
    for variant_name in RUNTIME_VARIANT_NAMES:
        assert summary[variant_name]["base"]["sample_size"] == 0
        assert summary[variant_name]["stress"]["sample_size"] == 0
