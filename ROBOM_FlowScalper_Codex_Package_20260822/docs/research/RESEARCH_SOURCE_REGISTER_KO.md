# 전략·Trailing 연구 출처와 채택 경계

기계판독 원본은
[RESEARCH_SOURCE_REGISTER.json](../../evidence/RESEARCH_SOURCE_REGISTER.json)에 있다.

2026-08-29의 최신 외부 연구와 현재 11전략·100후보 중복 대조, 추가 데이터 수집가설은
[Wave 110 외부 연구 대조](WAVE110_EXTERNAL_RESEARCH_DEDUPLICATION_KO.md)에 분리해 기록했다.

## 원칙

- 공식 문서와 원 논문은 가설·상태 머신·검증방법의 출처다.
- 다른 시장과 시간축의 통계관계는 crypto 단타 수익성 증거가 아니다.
- GitHub 저장소의 코드를 복사하거나 dependency로 추가하지 않았다.
- upstream에 실제 주문 기능이 있어도 FlowScalper에는 public market과 내부 PAPER 경로만 둔다.

## 공식 문서

| Source | 참고 개념 | 채택 경계 |
|---|---|---|
| Bybit Trailing Stop | activation, favorable high·low, distance·rate | PAPER 상태 머신 공식 참고만 사용 |
| Binance exchangeInfo | tickSize, stepSize, min quantity·notional | 공개 symbol filter 검증 |
| Binance local order book | snapshot, U·u·pu sequence, resync | 공개 depth 무결성 검증 |
| Freqtrade lookahead·recursive | 미래누수와 warmup 의존 검출 | 테스트 설계만 참고 |
| Freqtrade trailing offset | initial protection 뒤 profit activation | 상태 머신 비교근거 |
| Backtrader StopTrail·slippage | 단조 trail과 미체결 가능성 | executable book과 다른 점을 명시 |

## 학술 근거

PBO와 DSR은 100개 중 우연히 가장 좋아 보인 후보를 고르는 선택편향을 통제하는 gate로 쓴다.
Time Series Momentum, OFI, queue imbalance, MLOFI 연구는 각각 F15·F17·F18과 기존
microstructure 가설의 출처다. 주식·선물 또는 다른 시장에서 관측된 관계를 Binance USDⓈ-M
수익성으로 일반화하지 않고 동일한 비용·OOS·STRESS 검증을 새로 수행한다.

## GitHub 참고 프로젝트

| Repository | 고정 commit | License | 참고 | 코드 가져옴 |
|---|---|---|---|---:|
| freqtrade/freqtrade | `997ef6e36ad0` | GPL-3.0 | dry-run, 누수 검사 | 아니요 |
| polakowo/vectorbt | `34b6d5935e3e` | Apache-2.0 + Commons Clause | columnar screening | 아니요 |
| nautechsystems/nautilus_trader | `f2b2addb9952` | LGPL-3.0 | event-driven 구조 | 아니요 |
| jesse-ai/jesse | `d53f6d16446e` | MIT | research interface | 아니요 |
| mementum/backtrader | `b853d7c90b67` | GPL-3.0-or-later | StopTrail·slippage | 아니요 |

vectorbt의 비표준 Commons Clause와 GPL/LGPL 프로젝트의 결합 위험을 피하기 위해 아이디어와
문서만 참고했고 소스·dependency를 가져오지 않았다.
