# Wave 110. 외부 연구 대조와 중복 후보 차단

## 상태

`RESEARCH_REVIEW_COMPLETE_IMPLEMENTATION_GATES_PENDING`이다. 2026-08-29에 공식 Binance 공개시장
문서와 원 논문을 현재 Registry 11전략, 100후보 funnel, Wave 105B·106 사전등록과 대조했다.
외부 연구의 정확도나 다른 시장 결과를 ROBOM PAPER 승률 또는 수익성으로 사용하지 않았다.

## 이미 구현되었거나 사전등록된 가설

| 외부 아이디어 | 현재 ROBOM 대응 | 판단 |
|---|---|---|
| OFI와 짧은 자기회귀 수익률 결합 | `OFI_RETURN_CONFLUENCE_V1` | 새 전략으로 복제하지 않음 |
| L1·L5 imbalance, microprice, trade flow 합의 | Queue·Aggressor·Multilevel·Depth-adjusted 전략 | 가중치만 바꾼 복제 금지 |
| 방향별 호가깊이 기울기 | `BOOK_SLOPE_ASYMMETRY_V1` | 기존 J 전략 유지 |
| 공격체결 뒤 depth·spread 복원력 | Wave 106 adverse-flow/capacity veto | 동일입력 baseline 뒤 구현 우선 |
| 추세·반전·VWAP·돌파·변동성 확장 | F03~F16 및 기존 CBR·VWAP | 완료봉 기간 부족을 먼저 해결 |
| 비용·TP1 도달 가능성 | Wave 105B gate | 새 신호가 아닌 veto로 먼저 비교 |

Schmalz의 BTC/USDT 연구는 OFI 단독의 OOS 결과가 표본 확대와 파이프라인 수정에 따라 두 번
뒤집혔고, 약 17일 자료에서 OFI와 AR(1)을 함께 쓴 경우가 각각보다 나았다고 보고한다. 이것은
현재 G 전략의 결합가설과 장기간·purge·embargo 요구를 지지하지만 수익성 증거는 아니다.

Xu 외의 호가장 복원력 연구는 공격적 시장가 충격 뒤 가격 반전과 덜 공격적인 충격 뒤 연속이
다르게 나타날 수 있고 spread·depth가 약 20개 best-limit update 안에 회복할 수 있음을 보였다.
다른 시장의 결과이므로 고정 수익규칙으로 복사하지 않고 Wave 106의 cancel/refill·흡수여력 veto
검증근거로만 쓴다.

## 중복되지 않는 추가 수집가설

### HYP-W110-INTRADAY-LIQUIDITY-QUARANTINE-V1

Mercik·Bedowska-Sojka는 Binance와 Coinbase의 BTC·ETH 고빈도 자료에서 변동성이 spread 변화의
주요 설명요인이며 시간대 주기성도 추가 설명력을 가진다고 보고한다. ROBOM에서는 고정된
좋은 시간대를 임의로 고르지 않는다. 최소 7개의 완전한 UTC 날짜와 각 시간 bucket의 충분한
Train 표본을 먼저 동결한 뒤, depth-adjusted spread·실행가능 깊이가 Train 하위구간인 시간대의
기존 `QUALIFIED` 진입만 거부하는 후보로 제한한다.

- 신호·방향·거래 수를 만들 수 없다.
- Validation·OOS를 본 뒤 시간 bucket이나 분위수를 바꾸지 않는다.
- 현재 동결 데이터는 이 가설의 날짜·시간대 분산을 입증하기에 부족하므로
  `DEFERRED_INSUFFICIENT_FROZEN_DAYS`다.

### HYP-W110-LIQUIDATION-SHOCK-QUARANTINE-V1

Binance의 공개 USDⓈ-M `forceOrder` stream은 1,000ms마다 모든 청산이 아니라 가장 큰 청산주문
한 건만 제공한다. 따라서 이 feed를 완전한 청산량이나 방향 예측기로 사용하면 안 된다. 별도
공개시장 event로 저장·replay할 수 있게 된 뒤, 큰 강제청산과 spread 확대·지지깊이 붕괴가 함께
나타난 동안 기존 진입을 fail-closed로 잠그는 데이터안전 후보만 검토한다.

- 실제 주문·인증·private API 없이 공개 stream만 허용한다.
- stream 누락·throttling을 고려해 강제청산 부재를 안전 신호로 해석하지 않는다.
- 임계값을 정하기 전에는 데이터 수집 가설이며 전략 Registry에 등록하지 않는다.
- tail event가 드물어 30개 독립기회와 여러 레짐이 쌓이기 전에는 `NOT_PROVEN`이다.

### HYP-W110-PERP-CROWDING-QUARANTINE-V1

Binance 공개시장 문서는 mark price·funding rate·open interest·basis를 계좌 인증 없이 조회할 수
있음을 명시한다. 그러나 funding rate 자체의 예측 가능성은 다음 가격수익률 또는 비용 후 거래
수익성을 뜻하지 않는다. 실제로 공개 Binance 자료로 OHLCV·funding 신호를 purged walk-forward
평가한 최근 실패 연구는 약한 양의 정보계수가 비용 후 손실로 바뀔 수 있음을 보고했다. 따라서
funding·basis·open interest를 방향 신호로 바로 복제하지 않고, 과도한 레버리지 쏠림과 basis
이탈이 기존 신호 방향과 같은 경우 신규 진입을 거부하는 crowding 후보로만 사전등록한다.

- 공개 `markPrice`, funding history, basis와 open-interest statistics만 허용한다.
- 공개시장 자료이더라도 새 event schema·보존·replay parity가 먼저 구현되어야 한다.
- funding settlement 경계와 평시를 모두 포함한 최소 21일 Train, 별도 Validation·OOS가 필요하다.
- 임계값은 Train에서만 동결하고 Validation·OOS 결과를 본 뒤 바꾸지 않는다.
- 이 후보도 신호를 만들거나 방향을 뒤집지 못하며 현재 상태는 `DATA_COLLECTION_CONTRACT_REQUIRED`다.

Hawkes event-intensity와 딥러닝 LOB 연구도 추가 확인했지만 현재 Queue·Aggressor·MLOFI 계열과
입력정보가 크게 겹친다. 분류 정확도를 거래승률로 오해할 위험과 지연·다중시험 비용이 커서,
단순 결합전략이 같은 입력에서 비용 후 gate를 통과하기 전에는 새 Registry 전략으로 추가하지
않는다.

## 추가 신속 탐색과 계산 절약

2026-08-29에 검색된 최근 1차 연구도 현재 100후보와 대조했다. 전략 수를 늘리는 것보다 동일 가설과 비용에서
즉시 탈락할 후보를 리플레이 전에 차단하는 것이 더 빠르고 선택편향도 줄인다.

### 15분 직전봉 반전은 독립 전략으로 추가하지 않음

Kitron·Wengrowicz는 183개 Binance pair의 동결 6개월 holdout에서 15분 직전 수익률 방향을 거스르는 신호가 광범위하게
관측된다고 보고했다. 하지만 최대 gross edge가 거래당 약 1.3bp인 반면 논문의 왕복 비용 기준은 5bp여서 저자들도
비용을 넘지 못한다고 보고했다. ROBOM의 사전등록 BASE 13bp·STRESS 25bp보다도 현저히 작다.

따라서 이를 `HYP-W110-SIGNED-15M-REVERSAL-NEGATIVE-CONTROL-V1`로 기록하되 새 Registry 전략이나 5개 청산 변형으로 실행하지
않는다. 현재 청산·비용 계약에서 구조적으로 열세인 후보를 사전 제거해 최소 5개 시험 계산을 절약한다. 이는 우리 데이터의
성과 판정이 아니라 외부 가설의 비용 불합격을 사전 기록한 것이다.

### 15분 시작 주기는 새 신호가 아니라 기존 시간대 검증의 계층으로 제한

Kim·Hansen은 6개 Binance perpetual의 정각·5분·15분 경계에서 변동성·거래량 급증과 15분 시작 구간의 OOS 예측가능성을
보고했다. 해당 결과는 우리의 비용 후 승률을 입증하지 않고 현재 F14 세션 범위 가설과 겹친다. 새 방향 전략으로 복제하지 않고,
`HYP-W110-INTRADAY-LIQUIDITY-QUARANTINE-V1`의 사전등록 시간-phase 계층으로만 둔다. 최소 7개 완전한 UTC 날짜 전에는 실행하지
않고 Validation을 본 뒤 15분 위상을 고르지 않는다.

### perpetual basis는 방향 신호가 아니라 변동성 위험 후보로 제한

Lim은 BTC·ETH·SOL·DOGE·AVAX의 18개월 시간봉에서 절대 basis가 후행 실현변동성을 예측했지만, 강한 HAR-RV benchmark보다는 OOS
개선이 유의하지 않았고 가격 방향 수익을 주장하지 않았다. 따라서 기존 `HYP-W110-PERP-CROWDING-QUARANTINE-V1`의 변동성·진입위험
특성으로만 보존하고 롱·숏 방향을 만드는 새 전략으로 추가하지 않는다.

### Volume Profile·Tape Speed는 현재 VWAP 계열과 바로 복제하지 않음

Perera는 SOL/USDT perpetual 5년 자료에서 전일 Value Area·POC와 tape speed를 결합한 반전가설을
평가했지만, 0.15% 손절 구성에서는 거래비용이 위험예산의 93%를 소모해 수학적으로 부적합하다고
보고했다. 현재 ROBOM의 VWAP 반전·공격체결·비용 veto와 입력 및 행동이 크게 겹치므로 이름만
다른 전략으로 추가하지 않는다. 훗날 POC·Value Area를 독립 feature contract로 보존하고 더 넓은
손절구조를 Train에서 사전등록할 수 있을 때만 `HYP-W110-VOLUME-PROFILE-TAPE-SPEED-V1`을 다시
검토한다. 현재 상태는 `DEFERRED_FEATURE_OVERLAP_AND_COST_RISK`다.

### 대량 백테스트보다 사전등록·독립 OOS를 우선

Nefedov는 137개 Binance USDT perpetual과 6개 표준 factor를 엄격한 nested walk-forward·비용·DSR로
재평가했을 때 기준 구성에서 어느 factor도 deflation을 통과하지 못했고 4개는 OOS Sharpe가 음수로
바뀌었다고 보고했다. Castellanos Macias의 사전등록 연구도 매우 큰 전략공간에서 과거성과 선택의
독립 forward 전이 상관이 거의 0에 가까웠다고 보고했다. 두 결과는 새 신호를 제공하지 않지만,
현재 `parameter_fingerprint`, 30개 독립기회, BASE·STRESS, bootstrap·DSR·PBO 및 최대 10개 생존후보
정책을 더 엄격히 유지할 근거다. 따라서 전략 수를 목표로 무작정 후보를 만드는 방식은 채택하지 않는다.

### 비용 인지 ML은 장기 데이터가 갖춰진 뒤 별도 연구선으로만 등록

Bysik·Slepaczuk은 약 7만 개 시간봉과 27-fold walk-forward에서 단순 방향 ML 거래는 10bp 비용 후
실패하지만 예측크기가 비용 임계값을 넘을 때만 거래하는 구성 일부는 살아남았다고 보고했다. 이
가설은 현재 초단기 Registry와 시간축·모델 수명주기가 달라 즉시 섞으면 안 된다. 공개시장 장기
시간봉 dataset version, frozen feature schema, 모델 artifact checksum, purged walk-forward, latency·비용
계약이 준비된 뒤 `HYP-W110-HOURLY-COST-AWARE-ML-V1`을 별도 PAPER 연구선에서만 검토한다. 현재
상태는 `DEFERRED_LONG_DATA_MODEL_CONTRACT`이며 runtime AI 주문판단을 허용하지 않는다.

Bieganowski·Slepaczuk의 2022~2025 Binance Futures 1초 LOB 연구는 OFI·spread·VWAP 편차의
비선형 중요도 모양이 여러 자산에서 유사할 수 있음을 CatBoost와 time-series cross validation으로
보였다. 그러나 해당 입력은 현재 ROBOM의 OFI·microprice·aggressor·VWAP feature family와 겹치고,
모델 artifact·학습구간·flash-crash 검증 계약은 아직 없다. 따라서
`HYP-W110-NONLINEAR-UNIVERSAL-MICROSTRUCTURE-V1`은 기존 선형·규칙 후보가 공통 baseline에서
비용 후 gate를 통과한 다음에만 검토하는 `DEFERRED_FEATURE_OVERLAP_AND_MODEL_LIFECYCLE`로 둔다.
외부 분류·backtest 결과를 현재 PAPER 승률로 옮기지 않는다.

### 달러거래량 event bar의 OFI 지속성은 별도 시간축 후보로 사전등록

Li의 CME Ether futures 연구는 시간봉이 아니라 dollar-volume event bar로 OFI를 집계하고, 짧은
동시 가격충격과 그 뒤 수분 단위 지속·하루 안 반전을 구분했다. 보고된 OOS 성과는 Binance USDⓈ-M,
현재 ROBOM 비용계약 또는 현재 공개시장 자료의 성과가 아니다. 다만 500ms 미세구조 신호와 5~15분
완료봉 사이의 중간 시간축이라는 점은 현재 Registry의 단순 파라미터 복제와 다르다.

`HYP-W110-OFI-DOLLAR-VOLUME-PERSISTENCE-V1`로만 사전등록하고 다음 조건 전에는 Registry 전략으로
추가하지 않는다.

- 각 종목의 과거 정보만으로 고정한 dollar-volume bar 경계를 event-time으로 재현한다.
- 현재 `OFI_RETURN_CONFLUENCE_V1`과 동일 기회가 되는 비율을 먼저 계산하고 중복기회는 독립표본으로 세지 않는다.
- 1분·3분·10분 horizon은 하나의 다중시험 family로 묶고 임의의 최적 horizon만 숨겨 선택하지 않는다.
- executable bid·ask, BASE 13bp·STRESS 25bp, 최대보유와 조기종료를 포함한 동일 PAPER 경로를 사용한다.
- 최소 30개 독립 OOS 기회와 독립 forward가 없으면 `NOT_PROVEN`이며 현재 상태는
  `PREREGISTERED_DATASET_CONTRACT_REQUIRED`다.

Vafin의 설계논문과 Schmalz의 확대 표본 연구도 OFI가 같은 구간의 기계적 가격충격을 설명하는 것과
미래 수익률을 예측하는 것을 분리해야 한다고 강조한다. 특히 Schmalz 결과가 표본 확대 중 두 번
뒤집힌 사실은 짧은 양수 결과로 위 후보를 승격하지 않을 직접적인 반증조건으로 사용한다.

### 2026년 7~8월 신규 연구의 범위 부적합도 사전에 제거

Dünnes·Eckberg의 Ethereum demand-side fee flow는 10~60일 수익률 가설이고, Kim·Lim의
predictability-to-tradability 연구는 일간 OHLCV와 장기 portfolio 평가다. 둘 다 비용·고정 OOS를
강조한다는 점은 채택하지만 현재 500ms~수분 PAPER scalper와 보유시간·입력정보가 다르다.
`HYP-W110-LONG-HORIZON-CRYPTO-FUNDAMENTAL-V1`로 별도 범위기록만 남기고 현재 Registry에
추가하지 않는다. 상태는 `REJECTED_CURRENT_PRODUCT_HORIZON_MISMATCH`다.

Sun·Wang·Zhang의 prospective public-information nowcasting은 사후 데이터누수를 줄이는 설계를
보였지만 매일 web-enabled model이 판단을 새로 만드는 구조다. FlowScalper의 runtime AI 주문판단 0,
동결 artifact·결정성 replay·공개시장 미시구조 입력 계약과 충돌하므로
`HYP-W110-AGENTIC-NOWCAST-V1`은 `REJECTED_RUNTIME_AI_AND_HORIZON_SCOPE`로 둔다. 논문 결과를
고정 규칙처럼 복제하거나 현재 승률로 옮기지 않는다.

Lau의 funding carry는 Binance·Bybit와 Hyperliquid 사이에 양쪽 포지션을 동시에 두는 delta-neutral
cross-venue 전략이다. 단일 공개 Binance USDⓈ-M 입력과 내부 PAPER 계좌만 사용하는 현재 제품에
넣으면 체결·담보·funding·venue failure를 허위로 단순화한다. `HYP-W110-CROSS-VENUE-FUNDING-CARRY-V1`은
`DEFERRED_SEPARATE_MULTI_VENUE_PAPER_ENGINE_REQUIRED`로 두고 현재 scalper 전략 수에 포함하지 않는다.
이 세 사전제거로 전략 이름만 늘리지 않고 검증 가능한 OFI dollar-volume 한 가설에 계산을 집중한다.

## 채택하지 않은 지름길

- 50~500ms 분류정확도 또는 딥러닝 정확도를 PAPER 거래승률로 바꾸지 않는다.
- 현재 100후보와 같은 feature의 임계값·이름만 바꾼 후보를 추가하지 않는다.
- 한 종목·한 사건·한 시간대 결과를 전체 50종목에 일반화하지 않는다.
- 표시 depth를 실제 유동성의 완전한 값으로 보지 않는다. PAPER 체결은 계속 실제 수신 bid·ask
  깊이를 보수적으로 소모하되 STRESS 비용과 tail 위험을 별도로 본다.
- 자연 거래가 적다는 이유로 전략·비용·TP·SL·최대손실·조기종료 기준을 낮추지 않는다.

## 우선 실행순서

1. Wave 110의 `NONE` 공통 13-Run baseline을 한 번만 안전하게 완성한다.
2. 같은 commit·manifest·archive byte·이벤트 순서로 Wave 105B 후보를 target별 비교한다.
3. Wave 106 q75 calibration과 veto를 구현해 같은 baseline을 재사용한다.
4. 현재 Run의 불변 완료 구간을 삭제 없이 누적하고, 충분한 완료봉 기간이 생기면 F03~F16의
   기존 70개 후보를 새 dataset version에서 실행한다.
5. 위 세 추가 가설은 데이터 요구조건을 먼저 충족한 뒤 별도 다중시험 ID로 평가한다.

## 1차 출처

- Binance USDⓈ-M
  [Diff Book Depth Streams](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Diff-Book-Depth-Streams)
- Binance USDⓈ-M
  [Aggregate Trade Streams](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Aggregate-Trade-Streams)
- Binance
  [Derivatives Change Log](https://developers.binance.com/docs/derivatives/change-log)
- Cont, Kukanov, Stoikov,
  [The Price Impact of Order Book Events](https://arxiv.org/abs/1011.6402)
- Xu et al.,
  [Limit-order book resiliency after effective market orders](https://arxiv.org/abs/1602.00731)
- Schmalz,
  [Order Flow Imbalance and Short-Horizon BTC/USDT Returns](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7227998)
- Mercik, Bedowska-Sojka,
  [When Markets Never Sleep](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6401099)
- Guo,
  [Cross-coin Heterogeneity in Liquidation Cascade Dynamics](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6883362)
- Hansen, Kim, Kimbrough,
  [Periodicity in Cryptocurrency Volatility and Liquidity](https://arxiv.org/abs/2109.12142)
- Inan,
  [Predictability of Funding Rates](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5576424)
- Fayez Junior,
  [Failure of Cross-Sectional Alpha Screening on Cryptocurrency Perpetual Futures](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6701738)
- Cestari et al.,
  [Hawkes-based cryptocurrency forecasting via Limit Order Book data](https://arxiv.org/abs/2312.16190)
- Kitron, Wengrowicz,
  [Short-horizon mean reversion in cryptocurrency markets](https://arxiv.org/abs/2608.21888)
- Kim, Hansen,
  [The Quarter-Hour Effect](https://arxiv.org/abs/2607.09426)
- Lim,
  [The Information Content of Perpetual Basis](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6697060)
- Perera,
  [Volume Profile Mean Reversion Strategy with Tape Speed Confirmation](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6932998)
- Nefedov,
  [How Much Sharpe is Illusory?](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7350238)
- Castellanos Macias,
  [Anatomy of a Null Result](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7085378)
- Bysik, Slepaczuk,
  [Machine Learning-Based Bitcoin Trading Under Transaction Costs](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6795938)
- Bieganowski, Slepaczuk,
  [Explainable Patterns in Cryptocurrency Microstructure](https://arxiv.org/abs/2602.00776)
- Li,
  [Order Flow Imbalance and the Decay of Price Impact in CME Ether Future](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6772279)
- Vafin,
  [Order-Flow Imbalance and Short-Horizon Return Predictability in Cryptocurrency Markets](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6938742)
- Zhang,
  [Funding Rate Mechanism in Perpetual Futures](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6185958)
- Dünnes, Eckberg,
  [Demand-Side Fee Flows and Return Predictability on Ethereum](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7003998)
- Kim, Lim,
  [From Predictability to Tradability](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7115197)
- Sun, Wang, Zhang,
  [Agentic AI Nowcasting and Cryptocurrency Return Predictability](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7231679)
- Lau,
  [The Funding Carry and a Cross-Venue Spread on Perpetual Futures](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6993978)

모든 출처는 후보 선택과 검증 설계의 근거다. 현재 ROBOM의 비용 후 수익성이나 70% 승률을
증명하지 않는다.
