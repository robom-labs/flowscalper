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


def _v9_spec_source(
    source_id: str,
    title: str,
    year: str,
    doi: str | None,
    idea_used: str,
    our_modification: str,
) -> ResearchSourceMetadata:
    """사용자가 제공한 V9 명세의 Source ID를 수익성 주장 없이 등록한다."""

    return ResearchSourceMetadata(
        source_id=source_id,
        title=title,
        publisher="V9 통합마스터지침 등록 출처",
        date=year,
        url=f"https://doi.org/{doi}" if doi is not None else None,
        idea_used=idea_used,
        our_modification=our_modification,
        metadata_status="REGISTERED_FROM_V9_SPEC",
    )


_V9_SOURCES = (
    _v9_spec_source(
        "SRC-DC-ALGO-TRADING-2022",
        "Algorithmic trading with directional changes",
        "2022",
        "10.1007/s10462-022-10307-0",
        "Event-based intrinsic time과 multi-threshold DC 연구 가설에 사용합니다.",
        "논문 성과를 crypto 수익성으로 일반화하지 않고 실제 관측 mid로 다시 검증합니다.",
    ),
    _v9_spec_source(
        "SRC-DC-ACTUAL-CONFIRMATION-2024",
        "Exploiting the potential of a directional changes-based trading "
        "algorithm in the stock market",
        "2024",
        "10.1016/j.frl.2023.104936",
        "이론적 DC 확인과 실제 관측 가능한 crossing을 분리합니다.",
        "봉 내부 보간을 진입 근거로 쓰지 않고 actual confirmation만 허용합니다.",
    ),
    _v9_spec_source(
        "SRC-DC-TSFDC-2018",
        "Directional Change trend-reversal forecasting study",
        "2018",
        "10.1002/isaf.1425",
        "DC trend reversal forecast를 연구 가설로만 사용합니다.",
        "FX 결과를 crypto에 이식하지 않고 PAPER OOS로 다시 반증합니다.",
    ),
    _v9_spec_source(
        "SRC-DC-MULTI-THRESHOLD-2026",
        "A genetic algorithm for the optimization of multi-threshold trading "
        "strategies in the directional changes paradigm",
        "2026",
        "10.1007/s10462-025-11419-z",
        "Multi-threshold DC 후보 설계에 사용합니다.",
        "모든 탐색을 trial 수에 포함하고 runtime 자동 최적화를 금지합니다.",
    ),
    _v9_spec_source(
        "SRC-REALIZED-SEMIVARIANCE-MOMREV-2023",
        "Realized semivariance momentum-reversal study",
        "2023",
        "10.1016/j.jempfin.2023.03.001",
        "Upside·downside realized semivariance 비대칭을 router 연구에 사용합니다.",
        "Commodity 결과를 crypto 방향 신호로 직접 사용하지 않습니다.",
    ),
    _v9_spec_source(
        "SRC-BTC-JUMP-STRUCTURAL-BREAK-2020",
        "Bitcoin jump and structural-break volatility study",
        "2020",
        "10.1111/eufm.12254",
        "Bitcoin 변동성의 jump·structural break 위험 가설에 사용합니다.",
        "예측 성과를 진입 근거로 삼지 않고 위험 축소 후보로만 검증합니다.",
    ),
    _v9_spec_source(
        "SRC-CRYPTO-JUMP-TICK-2024",
        "Crypto jump clustering and intraday-pattern study",
        "2024",
        "10.1007/s42521-024-00116-1",
        "Crypto jump 군집·시간패턴과 negative jump 위험 가설에 사용합니다.",
        "Signed jump threshold가 사전등록되기 전에는 진입과 위험계수에 적용하지 않습니다.",
    ),
    _v9_spec_source(
        "SRC-INTRAWEEK-PERIODICITY-JUMP",
        "Intraweek periodicity and jump-detection study",
        "2010",
        "10.1016/j.jempfin.2010.11.004",
        "Time-of-week periodicity가 jump 검출을 왜곡하는지 검증합니다.",
        "현재 주를 제외한 8주 이상 완료 자료 전에는 Jump를 미보정으로 차단합니다.",
    ),
    _v9_spec_source(
        "SRC-CRYPTO-ASYMMETRIC-PERSISTENCE-2026",
        "Crypto asymmetric persistence study",
        "2026",
        "10.1016/j.frl.2026.109913",
        "Downside mean reversion·upside persistence를 router 가설로 사용합니다.",
        "직접 방향을 만들지 않고 기존 전략의 PAPER 필터로만 비교합니다.",
    ),
    _v9_spec_source(
        "SRC-COPULA-CRYPTO-PAIRS-2025",
        "Conditional-copula crypto pairs study",
        "2025",
        "10.1186/s40854-024-00702-7",
        "Cointegrated spread의 conditional copula mispricing 가설에 사용합니다.",
        "Multi-leg·legging·funding 엔진과 OOS 검증 전에는 BLOCKED_ENGINE로 유지합니다.",
    ),
    _v9_spec_source(
        "SRC-FDR-BH-1995",
        "Controlling the False Discovery Rate",
        "1995",
        "10.1111/j.2517-6161.1995.tb02031.x",
        "Batch 다중검정의 BH FDR 진단에 사용합니다.",
        "경제성·OOS·비용 gate를 대체하지 않고 BY 진단과 함께 보고합니다.",
    ),
    _v9_spec_source(
        "SRC-FALSE-DISCOVERIES-FINANCE-2020",
        "False discoveries in finance",
        "2020",
        "10.1111/jofi.12951",
        "Finance 다중검정과 Type I·II 비용 관리에 사용합니다.",
        "Harvey–Liu double-bootstrap이 구현되기 전에는 primary FDR gate 통과를 주장하지 않습니다.",
    ),
    _v9_spec_source(
        "SRC-PHACKING-TRADING-STRATEGIES",
        "p-Hacking: Evidence from Two Million Trading Strategies",
        "SSRN 3017677",
        None,
        "대규모 후보탐색의 거짓 발견 위험을 trial 수에 반영합니다.",
        "버린 후보도 검정 family와 append-only 이력에 포함합니다.",
    ),
    _v9_spec_source(
        "SRC-E-BH-2022",
        "E-values and the e-BH procedure",
        "2022",
        "10.1111/rssb.12489",
        "임의 의존성에서 e-value FDR 선별을 연구합니다.",
        "e-BH 결과만으로 ACTIVE를 만들지 않습니다.",
    ),
    _v9_spec_source(
        "SRC-EVALUE-DYNAMIC-VOLATILITY-2025",
        "E-value dynamic-volatility calibration study",
        "2025",
        "10.1016/j.spl.2025.110515",
        "실시간 anytime-valid calibration monitoring 가설에 사용합니다.",
        "고정된 non-overlapping PAPER opportunity만 순차 근거로 사용합니다.",
    ),
    _v9_spec_source(
        "SRC-ANYTIME-VALID-2026",
        "Anytime-valid sequential testing study",
        "2026",
        "10.1093/jrsssb/qkag050",
        "Optional stopping에도 유효한 순차 검정 가설에 사용합니다.",
        "사전등록 상한과 고정 점수를 유지하며 중간 성과로 자동 승격하지 않습니다.",
    ),
    _v9_spec_source(
        "SRC-HIERARCHICAL-SHRINKAGE-2013",
        "Hierarchical shrinkage forecasting study",
        "2013",
        "10.1016/j.ijforecast.2012.05.006",
        "희소 전략·종목 소표본 성과를 family 수준으로 보수화합니다.",
        "수축값이 원시 손실을 이익으로 바꾸거나 단독 승격 근거가 되지 않게 합니다.",
    ),
    _v9_spec_source(
        "SRC-DC-MULTIOBJECTIVE-2026",
        "Directional-change multi-objective optimization study",
        "2026",
        "10.1007/s10462-025-11390-9",
        "수익·tail risk·비용·안정성을 Pareto 목표로 분리합니다.",
        "단일 승률이나 Sharpe 점수로 숨은 가중합을 만들지 않습니다.",
    ),
)
_SOURCES.update({row.source_id: row for row in _V9_SOURCES})


def _v10_source(
    source_id: str,
    title: str,
    publisher: str,
    date: str,
    url: str | None,
    idea_used: str,
    our_modification: str,
    *,
    metadata_status: str = "OFFICIAL_PRIMARY_SOURCE",
) -> ResearchSourceMetadata:
    """V10 후보의 공식 출처와 검증 대기 출처를 명시적으로 구분한다."""

    return ResearchSourceMetadata(
        source_id=source_id,
        title=title,
        publisher=publisher,
        date=date,
        url=url,
        idea_used=idea_used,
        our_modification=our_modification,
        metadata_status=metadata_status,
    )


_V10_SOURCES = (
    _v10_source(
        "SRC-CFTC-TFF-FUTURES-ONLY",
        "Commitments of Traders Short Report: Financial Traders in Markets",
        "U.S. Commodity Futures Trading Commission",
        "상시 갱신",
        "https://www.cftc.gov/dea/futures/financial_lf.htm",
        "TFF Futures Only의 Bitcoin 133741과 Micro Bitcoin 133742를 구분합니다.",
        "CFTC 계약시장 코드를 상품 ticker로 바꾸지 않고 보고서 기준일·수집시각·"
        "응답 hash를 함께 보존합니다.",
    ),
    _v10_source(
        "SRC-CFTC-COT-RELEASE-SCHEDULE",
        "Commitments of Traders Release Schedule",
        "U.S. Commodity Futures Trading Commission",
        "상시 갱신",
        "https://www.cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm",
        "COT의 공식 연도별 공개일과 휴일 지연을 availability gate에 사용합니다.",
        "America/New_York 15:30 예정시각과 실제 first-observed 시각을 분리하고 "
        "단순 화요일+3일 계산을 금지합니다.",
    ),
    _v10_source(
        "SRC-CFTC-COT-HISTORICAL-VIEWABLE",
        "Commitments of Traders Historical Viewable",
        "U.S. Commodity Futures Trading Commission",
        "상시 갱신",
        "https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalViewable/index.htm",
        "보관 페이지 날짜가 report date이며 release timestamp가 아님을 구분합니다.",
        "관측 이력이 없는 archive 날짜를 실제 공개시각으로 소급 사용하지 않습니다.",
    ),
    _v10_source(
        "SRC-CME-CRYPTO-24X7-LAUNCH-2026",
        "CME Group Announces Launch of 24/7 Cryptocurrency Futures and Options Trading",
        "CME Group",
        "2026-06-01",
        "https://investor.cmegroup.com/news-releases/news-release-details/cme-group-announces-launch-247-cryptocurrency-futures-and",
        "2026-05-29의 실제 CME crypto 24/7 전환을 시장구조 epoch로 사용합니다.",
        "전환 이후 Friday-close·Sunday-reopen 고정 gap 가설을 현재 전략에서 제외합니다.",
    ),
    _v10_source(
        "SRC-CME-GLOBEX-CRYPTO-24X7-20260525",
        "CME Globex Notice: May 25, 2026",
        "CME Group",
        "2026-05-25",
        "https://www.cmegroup.com/notices/electronic-trading/2026/05/20260525.html",
        "2026-05-29 16:00 America/Chicago 전환과 주간 maintenance 예외를 고정합니다.",
        "주말 trade date와 달력일을 합치지 않고 최소 2시간 maintenance를 무시하지 않습니다.",
    ),
    _v10_source(
        "SRC-CME-CRYPTO-24X7-REGIME-2026",
        "Aligning Cryptocurrency Derivatives with Spot Markets: "
        "Measuring the 24/7 Trading Opportunity",
        "CME Group",
        "2026",
        "https://www.cmegroup.com/articles/2026/aligning-cryptocurrency-derivatives-with-spot-markets-measuring-the-247-trading-opportunity.html",
        "전통적 금요일 폐장·일요일 재개 구간이 24/7 도입으로 바뀐 범위를 확인합니다.",
        "전환 전·후 microstructure epoch를 혼합하지 않으며 모든 주말 불연속이 "
        "사라졌다고 과장하지 않습니다.",
    ),
    _v10_source(
        "SRC-CRYPTO-FUTURES-RISK-FACTORS-2023",
        "An empirical investigation on risk factors in cryptocurrency futures",
        "V10 통합마스터지침 등록 출처",
        "2023",
        None,
        "Futures basis와 횡단면 수익의 관계를 검증할 연구가설로만 등록합니다.",
        "원문·DOI 검증과 walk-forward sign stability 전에는 방향 부호나 수익성을 "
        "주장하지 않습니다.",
        metadata_status="REGISTERED_FROM_V10_SPEC_UNVERIFIED",
    ),
    _v10_source(
        "SRC-DYNAMIC-CRYPTO-TSMOM-2021",
        "Dynamic time series momentum of cryptocurrencies",
        "V10 통합마스터지침 등록 출처",
        "2021",
        None,
        "다중 시간축 crypto momentum을 V10 스윙 연구가설로만 등록합니다.",
        "원문 메타데이터와 현재 PAPER OOS를 검증하기 전에는 성과를 이전하지 않습니다.",
        metadata_status="REGISTERED_FROM_V10_SPEC_UNVERIFIED",
    ),
    _v10_source(
        "SRC-CRYPTO-MOMENTUM-REVERSAL-2021",
        "Cryptocurrency Momentum and Reversal",
        "V10 통합마스터지침 등록 출처",
        "2021",
        None,
        "Momentum·reversal 분리를 스윙 및 잔차강도 연구가설로만 등록합니다.",
        "논문 방향·기간 결과를 현재 crypto PAPER 수익성으로 일반화하지 않습니다.",
        metadata_status="REGISTERED_FROM_V10_SPEC_UNVERIFIED",
    ),
)
_SOURCES.update({row.source_id: row for row in _V10_SOURCES})


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
