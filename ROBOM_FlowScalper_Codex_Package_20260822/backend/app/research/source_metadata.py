# 등록된 연구 출처를 사용자 화면용 구조화 메타데이터로 제공한다.

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class ResearchSourceMetadata:
    source_id: str
    title: str
    publisher: str
    date: str
    url: str | None
    idea_used: str
    our_modification: str
    metadata_status: str = "REGISTERED"

    def as_dict(self) -> dict[str, str | None]:
        return asdict(self)


_SOURCES = {
    row.source_id: row
    for row in (
        ResearchSourceMetadata(
            "SRC-OFI-2010",
            "The Price Impact of Order Book Events",
            "Cont, Kukanov, Stoikov",
            "2010",
            "https://arxiv.org/abs/1011.6402",
            "깊이보정 주문흐름 불균형을 단기 방향 확인 입력으로 사용합니다.",
            "암호화폐 공개 호가의 spread, 유동성, 실행가능 비용을 함께 검증합니다.",
        ),
        ResearchSourceMetadata(
            "SRC-QI-2015",
            "Queue Imbalance as a One-Tick-Ahead Price Predictor in a Limit Order Book",
            "Gould, Bonart",
            "2015",
            "https://arxiv.org/abs/1512.03492",
            "sequence-valid 양방향 호가의 queue 비대칭을 보조 피처로 사용합니다.",
            "one-tick 예측을 수익으로 간주하지 않고 비용, 지연, depth 체결을 별도 검증합니다.",
        ),
        ResearchSourceMetadata(
            "SRC-MLOFI-2019",
            "Multi-Level Order-Flow Imbalance in a Limit Order Book",
            "Xu, Gould, Howison",
            "2019",
            "https://arxiv.org/abs/1907.06230",
            "여러 호가 단계의 OFI와 호가 기울기를 공정가 확인에 사용합니다.",
            "자산, tick size, 세션 차이를 일반화하지 않고 공개 crypto depth에서 재검증합니다.",
        ),
        ResearchSourceMetadata(
            "SRC-MICROPRICE-2017",
            "The Micro-Price: A High-Frequency Estimator of Future Prices",
            "Stoikov",
            "2017",
            "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2970694",
            "spread와 imbalance 기반 micro-price를 방향 확인 입력으로 사용합니다.",
            "추정 공정가를 단독 신호나 체결가능 수익으로 해석하지 않습니다.",
        ),
        ResearchSourceMetadata(
            "SRC-PBO-2015",
            "The Probability of Backtest Overfitting",
            "Bailey et al.",
            "2015",
            "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253",
            "다중 후보 선택에 따른 백테스트 과적합 확률을 기록합니다.",
            "거래가 없는 사전등록 가설도 후보 수에 포함하고 PBO 단독으로 승격하지 않습니다.",
        ),
        ResearchSourceMetadata(
            "SRC-DSR-2014",
            "The Deflated Sharpe Ratio",
            "Bailey, Lopez de Prado",
            "2014",
            "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551",
            "비정규 수익과 다중 시험 편향을 반영한 DSR을 기록합니다.",
            "단기 수익을 연환산하지 않고 표본 부족은 INSUFFICIENT로 유지합니다.",
        ),
        ResearchSourceMetadata(
            "SRC-CRYPTO-MOMENTUM-2018",
            "Risks and Returns of Cryptocurrency",
            "Liu, Tsyvinski",
            "2018",
            "https://www.nber.org/papers/w24877",
            "완성 시간봉의 중기 모멘텀과 추세 정렬을 후보로 시험합니다.",
            "논문의 기간과 자산 결과를 현재 USD-M PAPER 성과로 간주하지 않습니다.",
        ),
        ResearchSourceMetadata(
            "SRC-TSMOM-2012",
            "Time Series Momentum",
            "Moskowitz, Ooi, Pedersen",
            "2012",
            "https://www.sciencedirect.com/science/article/pii/S0304405X11002613",
            "추세 방향, 다중 시간축 정렬, 고정 위험계획의 가설 출처로 사용합니다.",
            "월 단위 전통자산 결과를 분 단위 암호화폐 수익성으로 일반화하지 않습니다.",
        ),
        ResearchSourceMetadata(
            "SRC-CRYPTO-TREND-2020",
            "A Decade of Evidence of Trend Following Investing in Cryptocurrencies",
            "Rozario et al.",
            "2020",
            "https://arxiv.org/abs/2009.12155",
            "완성봉 추세, 눌림, 돌파 재확인 후보의 가설 출처로 사용합니다.",
            "현재 공개 bid, ask, BASE, STRESS와 미래 OOS에서 별도 반증합니다.",
        ),
        ResearchSourceMetadata(
            "SRC-BINANCE-AGGTRADE",
            "Aggregate Trade Streams",
            "Binance USD-M Futures",
            "게시일 미표기",
            "https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Aggregate-Trade-Streams",
            "공개 체결의 event time, 가격, 수량, aggressor 방향을 canonical flow에 사용합니다.",
            "수신 지연이 큰 체결은 보존하되 현재 전략 입력에서는 fail-closed합니다.",
        ),
        ResearchSourceMetadata(
            "SRC-BINANCE-DEPTH",
            "Diff. Book Depth Streams",
            "Binance USD-M Futures",
            "게시일 미표기",
            "https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Diff-Book-Depth-Streams",
            "공개 depth delta와 update ID로 sequence-valid 호가장을 복원합니다.",
            "sequence gap, stale, 500ms 초과 실행호가는 신규 진입에 사용하지 않습니다.",
        ),
        ResearchSourceMetadata(
            "SRC-BINANCE-KLINE",
            "Kline/Candlestick Streams",
            "Binance USD-M Futures",
            "게시일 미표기",
            "https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Kline-Candlestick-Streams",
            "공개 kline 필드와 마감 여부를 canonical candle 대조에 사용합니다.",
            "진행 중 봉은 연구 피처에서 제외하고 완성 봉만 사용합니다.",
        ),
    )
}


def research_source_metadata(source_id: str) -> dict[str, str | None]:
    source = _SOURCES.get(source_id)
    if source is not None:
        return source.as_dict()
    return {
        "source_id": source_id,
        "title": source_id,
        "publisher": "등록 정보 없음",
        "date": "NOT_PROVEN",
        "url": None,
        "idea_used": "등록된 연구 출처 설명이 없습니다.",
        "our_modification": "메타데이터 확인 전에는 연구 근거로 단정하지 않습니다.",
        "metadata_status": "NOT_PROVEN",
    }


def research_source_metadata_rows(
    source_ids: Iterable[str],
) -> list[dict[str, str | None]]:
    return [research_source_metadata(source_id) for source_id in dict.fromkeys(source_ids)]
