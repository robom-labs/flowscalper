# HYP-116L. 24개 중단기 추세 후보 병렬 토너먼트 사전등록

- 상태. `PREREGISTERED_BEFORE_EXECUTION`.
- 등록일. 2026-08-30.
- 연구범위. Binance USDⓈ-M 공개 완성 5분봉을 15분·30분봉으로 집계한 PAPER 연구다.
- 성과 상태. `NOT_PROVEN`.
- 실자금 준비. `NOT_READY`.

## 연구 질문

상승·하락 추세의 초입, 첫 눌림, 돌파 후 재확인, 변동성 압축 뒤 확장처럼 서로 다른 구조를 결과를 보기 전에 고정하고 같은 공개시장 입력과 비용으로 동시에 비교하면, 시간순 검증구간과 보수적 비용 뒤에도 재현 가능한 양의 순기대값 후보가 남는가?

승률 70%는 사용자의 장기 목표를 표시하는 진단값이다. 승률만 높고 평균손실이 크거나 비용 뒤 기대값이 음수인 후보는 통과시키지 않는다.

## 고정 후보 24개

| candidate_id | 계열 | 주기 | lookback | 24시간 모멘텀 | ADX | 상대거래량 | TP1·TP2 |
|---|---|---:|---:|---:|---:|---:|---:|
| `T116L_PULLBACK_15M_BALANCED` | EMA 눌림 회복 | 15분 | 20 | 0.8% | 16 | 0.75 | 1.0R·2.4R |
| `T116L_PULLBACK_15M_SELECTIVE` | EMA 눌림 회복 | 15분 | 32 | 1.5% | 22 | 1.00 | 1.4R·3.0R |
| `T116L_PULLBACK_30M_BALANCED` | EMA 눌림 회복 | 30분 | 16 | 0.8% | 16 | 0.75 | 1.0R·2.4R |
| `T116L_PULLBACK_30M_SELECTIVE` | EMA 눌림 회복 | 30분 | 24 | 1.5% | 22 | 1.00 | 1.4R·3.0R |
| `T116L_RETEST_15M_BALANCED` | Donchian 돌파 재확인 | 15분 | 20 | 1.0% | 18 | 0.90 | 1.1R·2.6R |
| `T116L_RETEST_15M_SELECTIVE` | Donchian 돌파 재확인 | 15분 | 40 | 1.8% | 24 | 1.20 | 1.4R·3.2R |
| `T116L_RETEST_30M_BALANCED` | Donchian 돌파 재확인 | 30분 | 16 | 1.0% | 18 | 0.90 | 1.1R·2.6R |
| `T116L_RETEST_30M_SELECTIVE` | Donchian 돌파 재확인 | 30분 | 32 | 1.8% | 24 | 1.20 | 1.4R·3.2R |
| `T116L_COMPRESSION_15M_BALANCED` | 변동성 압축 후 돌파 재확인 | 15분 | 20 | 0.6% | 15 | 1.00 | 1.0R·2.5R |
| `T116L_COMPRESSION_15M_SELECTIVE` | 변동성 압축 후 돌파 재확인 | 15분 | 32 | 1.2% | 20 | 1.30 | 1.3R·3.0R |
| `T116L_COMPRESSION_30M_BALANCED` | 변동성 압축 후 돌파 재확인 | 30분 | 16 | 0.6% | 15 | 1.00 | 1.0R·2.5R |
| `T116L_COMPRESSION_30M_SELECTIVE` | 변동성 압축 후 돌파 재확인 | 30분 | 24 | 1.2% | 20 | 1.30 | 1.3R·3.0R |
| `T116L_MULTISPEED_15M_BALANCED` | 다중속도 추세 재합류 | 15분 | 20 | 0.6% | 16 | 0.70 | 1.0R·2.4R |
| `T116L_MULTISPEED_15M_SELECTIVE` | 다중속도 추세 재합류 | 15분 | 32 | 1.2% | 22 | 0.95 | 1.4R·3.0R |
| `T116L_MULTISPEED_30M_BALANCED` | 다중속도 추세 재합류 | 30분 | 16 | 0.6% | 16 | 0.70 | 1.0R·2.4R |
| `T116L_MULTISPEED_30M_SELECTIVE` | 다중속도 추세 재합류 | 30분 | 24 | 1.2% | 22 | 0.95 | 1.4R·3.0R |
| `T116L_TWO_LEG_15M_BALANCED` | 2단 눌림 뒤 반전 | 15분 | 20 | 0.8% | 16 | 0.80 | 1.0R·2.4R |
| `T116L_TWO_LEG_15M_SELECTIVE` | 2단 눌림 뒤 반전 | 15분 | 32 | 1.5% | 22 | 1.05 | 1.3R·3.0R |
| `T116L_TWO_LEG_30M_BALANCED` | 2단 눌림 뒤 반전 | 30분 | 16 | 0.8% | 16 | 0.80 | 1.0R·2.4R |
| `T116L_TWO_LEG_30M_SELECTIVE` | 2단 눌림 뒤 반전 | 30분 | 24 | 1.5% | 22 | 1.05 | 1.3R·3.0R |
| `T116L_INSIDE_15M_BALANCED` | 인사이드바 추세확장 | 15분 | 20 | 0.8% | 16 | 0.90 | 1.0R·2.4R |
| `T116L_INSIDE_15M_SELECTIVE` | 인사이드바 추세확장 | 15분 | 32 | 1.5% | 22 | 1.20 | 1.3R·3.0R |
| `T116L_INSIDE_30M_BALANCED` | 인사이드바 추세확장 | 30분 | 16 | 0.8% | 16 | 0.90 | 1.0R·2.4R |
| `T116L_INSIDE_30M_SELECTIVE` | 인사이드바 추세확장 | 30분 | 24 | 1.5% | 22 | 1.20 | 1.3R·3.0R |

각 후보는 LONG·SHORT를 대칭으로 평가한다. `BALANCED`와 `SELECTIVE`는 단순 이름 복제가 아니라 lookback, 모멘텀, ADX, 상대거래량, 구조 허용폭, 손절 buffer와 TP 손익비가 다르다.

## 진입과 청산 계약

- 신호는 완성된 15분·30분봉과 그 시점까지 완성된 1시간봉만 사용한다.
- 진입은 신호봉 다음 봉 시가다. 신호봉 종가에 체결된 것으로 가정하지 않는다.
- 최초 손절은 눌림 저점·고점, 돌파 재확인선, inside 구조처럼 진입 근거가 무효화되는 가격 밖에 둔다.
- 구조 손절 거리는 0.65~3.0 ATR 범위만 허용한다.
- TP1은 수량 40%, TP2는 나머지 60%를 청산한다.
- TP1 뒤 손절은 STRESS 왕복비용을 덮는 방향으로만 줄이고 넓히지 않는다.
- 한 봉에서 손절과 목표가가 모두 닿으면 과대평가를 막기 위해 손절이 먼저 체결된 것으로 본다.
- 고정 최대보유시간과 900초 강제청산은 없다.
- 연구 데이터 종료까지 TP·손절이 닿지 않은 포지션은 `CENSORED_OPEN`으로 보존하고 승패·손익 통계에서 제외한다.
- 실제 런타임의 데이터 단절·원장 결함·시스템 안전종료는 전략 손익판정과 별도다. 안전종료를 제거하지 않는다.

## 입력과 비용

- 고정 입력범위는 2025-12-01 00:00 UTC부터 2026-08-25 00:00 UTC 미만이다.
- 대상은 BTC, ETH, SOL, BNB, XRP, DOGE, ADA, AVAX, LINK, DOT, LTC, BCH의 USDT 무기한 공개 완성봉이다.
- BASE 왕복비용은 13bp, STRESS 왕복비용은 25bp다.
- 각 후보는 같은 입력을 독립적으로 받으며 후보별 최대 동시 포지션은 2개, 하루 신규진입은 4개다.
- 공개 과거 kline에는 당시 실행가능 bid·ask 깊이가 없으므로 역사 토너먼트는 후보 제거용 보수 진단이다. 통과 후보도 실제 공개 호가 BASE·STRESS SHADOW 미래표본을 별도로 쌓아야 한다.
- 실제 주문, private API, API Key, 인증, secret, wallet과 입출금 경로는 0이다.

## 시간순 판정 계약

- 전체 기간을 50% train, 20% validation, 30% 진단 OOS로 고정한다.
- 구간 경계에는 48시간 embargo를 둔다.
- development는 닫힌 거래 60건 이상, validation은 20건 이상이어야 한다.
- development BASE·STRESS 기대값과 Profit Factor가 양수이고 validation STRESS도 양수인 후보만 서로 다른 계열에서 최대 3개를 OOS로 보낸다.
- 진단 OOS는 닫힌 거래 40건 이상, BASE·STRESS 양의 기대값, BASE PF 1.15 이상, STRESS PF 1 초과를 요구한다.
- bootstrap 95% 기대값 하한은 0 초과, DSR은 0.95 이상, PBO는 0.20 이하를 요구한다.
- 양의 성과가 한 종목에 50% 넘게 집중되면 실패한다.
- 24회 후보시도를 다중검정 trial 수로 기록한다.
- 70% 승률 도달 여부는 별도 표시하지만 위 강건성 gate를 대신하지 않는다.

## 실행 전 변경 금지

이 문서와 후보 fingerprint를 소스와 함께 먼저 커밋한 뒤 토너먼트를 실행한다. 결과를 본 뒤 같은 후보 ID의 threshold, 비용, 손절, TP, lookback, 표본 gate 또는 split을 바꾸지 않는다. 결함 수정이 필요하면 실패 결과를 보존하고 새 버전·새 가설로 분리한다.

## 연구 출처와 경계

- [Trend-following Strategies for Crypto Investors](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4551518)는 암호화폐 추세와 거래비용 민감도를 가설로 삼는 근거다.
- [AdaptiveTrend](https://arxiv.org/abs/2602.11708)는 변동성·레짐·위험제어를 결합하는 연구 방향의 근거다.
- [Machine Learning Bitcoin Returns under Transaction Costs](https://arxiv.org/abs/2606.00060)는 비용을 무시한 신호가 실제 성과로 이어지지 않을 수 있다는 경계 근거다.
- [An Empirical Investigation of Trend Following Investing in Cryptocurrencies](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3697981)는 walk-forward 추세 가설 출처다.
- [Cryptocurrency Return Dispersion and State-Dependent Momentum](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6648082)는 시장상태에 따라 모멘텀을 분리해야 한다는 가설 출처다.
- [Exponential Moving Average Strategy under Transaction Costs](https://arxiv.org/abs/1308.5658)는 이동평균 규칙도 비용 뒤에 평가해야 한다는 방법론 참고다.

외부 논문과 과거 수익률은 이 24개 후보의 수익성 증거가 아니다. 외부 코드를 복사하지 않았고 연구 아이디어를 결정적 PAPER 가설로만 변환했다.
