# Strategy League 백엔드 명세

## 1. 범위

- FlowScalper 0.2.0-paper의 기존 공개시장 PAPER 런타임을 확장한다.
- 실제 주문, private API, API Key, secret, wallet, 인증은 없다.
- main Shared Capital Benchmark와 Strategy League는 같은 후보·호가 체결 엔진을 사용한다.

## 2. 전략 Registry

| 구분 | strategy_id | 한국어 이름 | 기본 mode | exit style |
|---|---|---|---|---|
| A | `LSA_REVERSAL_V1` | 급락·급등 쓸기 반전 | OFF | REVERSION_70_30 |
| B | `CBR_CONTINUATION_V1` | 압축 돌파 재가속 | SHADOW | TREND_40_60 |
| C | `VWAP_EXHAUSTION_REVERSION_V1` | VWAP 과도이탈 평균복귀 | SHADOW | REVERSION_70_30 |
| D | `OFI_CONTINUATION_PULLBACK_V1` | OFI 추세 눌림 지속 | OFF | TREND_40_60 |
| E | `QUEUE_MICROPRICE_MOMENTUM_V1` | 호가 쏠림 순간추세 | OFF | TREND_40_60 |
| F | `AGGRESSOR_FLOW_CONTINUATION_V1` | 강한 체결 흐름 지속 | OFF | TREND_40_60 |
| G | `MULTILEVEL_MICROPRICE_MOMENTUM_V1` | 다중호가 공정가 추세 | OFF | TREND_40_60 |
| H | `DEPTH_ADJUSTED_OFI_IMPULSE_V1` | 깊이보정 OFI 충격 | OFF | TREND_40_60 |
| I | `OFI_RETURN_CONFLUENCE_V1` | OFI·단기수익률 동행 | OFF | TREND_40_60 |
| J | `BOOK_SLOPE_ASYMMETRY_V1` | 호가 기울기 비대칭 | OFF | TREND_40_60 |
| K | `HOURLY_MOMENTUM_BREAKOUT_V1` | 시간봉 추세 돌파 | OFF | TREND_40_60 |
| L | `TREND_PULLBACK_RECLAIM_15M_V2` | 15분 추세 눌림 재상승 | SHADOW | TREND_40_60 |
| M | `BREAKOUT_RETEST_15M_V2` | 15분 돌파 후 재확인 | SHADOW | TREND_40_60 |
| N | `BREAKOUT_RETEST_30M_V2` | 30분 돌파 후 재확인 | SHADOW | TREND_40_60 |
| O | `MULTISPEED_TREND_RECLAIM_30M_V2` | 30분·1시간 추세 재합류 | SHADOW | TREND_40_60 |

- 모든 전략은 LONG·SHORT를 독립적으로 허용하거나 끌 수 있다.
- OFF는 평가하지 않고, ACTIVE는 main과 League, SHADOW는 League에만 후보를 제공한다. 사용자는 current entry variant를 `SHADOW` 또는 `OFF`로만 제어하고, `ACTIVE`는 사전등록 gate를 통과한 Governor만 설정한다.
- 모든 전략은 LONG·SHORT 제어를 유지한다. V6 current entry variant B/C/L/M/N/O는 SHADOW다. 비용후 시간순 검증에 실패한 A/D/E/H와 고정된 독립 과거구간 147일·166거래에서 재현에 실패한 K는 RETIRED·OFF다. 독립 entry가 아닌 legacy F/G/I/J는 RESEARCH·OFF다. RETIRED·legacy 전략은 과거 원장과 계좌를 보존하지만 별도 사전등록 연구와 코드 변경 없이는 다시 켤 수 없다.
- 공동계좌 ACTIVE 대표 전략은 기본값으로 두지 않는다. Strategy Governor는 비용후 기대값·PF, OOS 하한, DSR, PBO, 강건성, 독립기간, 위험과 운영건강의 공통 gate에 더해 사전등록된 family별 승률·payoff gate를 적용한다. 작은 표본의 100%도 자동 승격하지 않는다.
- 모든 전략에 70% 관측승률을 요구하거나 70% 미만이라는 이유만으로 RETIRED·QUARANTINED로 보내는 공통 규칙은 제거한다. 성능 격리와 승격은 family 형태, 비용후 기대값과 충분한 고유기회 증거를 함께 요구하며 거래와 근거를 보존한다.

## 3. 독립 계좌

- 15개 등록 전략마다 BASE·STRESS를 두어 총 30개 계좌를 생성한다.
- 비용후 재현에 실패한 5개 RETIRED와 legacy RESEARCH 4개의 계좌·거래는 보존하고, current entry variant 6개만 `SHADOW`에서 같은 공개시장 입력으로 신규 후보를 평가한다.
- account ID는 `STRATEGY_ID:PROFILE`이다.
- 각 계좌는 1,000 USDT로 시작한다.
- 자산, 손익, 수수료, 슬리피지, 위험과 fault를 섞지 않는다.
- 다른 전략은 같은 종목의 반대 방향을 동시에 보유할 수 있다.
- 같은 계좌·종목에 pending과 open을 중복 생성하지 않는다.
- 전략 계좌는 서로 다른 종목을 최대 3개까지 보유한다.
- main은 기존 전역 중재와 최대 1개 계약을 유지한다.

## 4. 전략 계좌 위험

- 포지션당 위험은 현재 자산의 0.5%다.
- open과 pending의 계획위험 합은 1.5%를 넘지 않는다.
- 하루 거래횟수·하루 손실·주간 손실·연속손실 cooldown은 연속 PAPER 연구에서 신규 진입을 잠그지 않는다.
- peak 대비 drawdown 8%는 해당 계좌의 신규 진입을 잠근다.
- 수량은 stop 거리, 양방향 fee, 예상 exit slippage로 먼저 계산한다.
- 선택 PAPER 배수는 기본 10배이며 1·2·3·5·10·20·25·50·75·100배 중 고른다.
- 계좌 총 명목금액은 선택 배수까지, 주문은 실행가능 깊이의 2%까지로 제한한다.
- 선택 배수는 강제 주문크기가 아니라 `실제 진입 명목금액 ÷ PAPER 증거금`이다. 수량은 기존 위험예산과 호가깊이로 정하고 수수료·손익은 실제 체결 명목금액으로 계산한다.
- 설정 변경은 새 진입부터 적용하며 열린 포지션과 완료 거래는 진입 당시 배수·명목금액·증거금을 보존한다.
- partial fill은 실제 수량의 위험·명목금액·fee만 open으로 옮긴다.
- 시장데이터·저장·복구의 시스템 fault는 30계좌 모두의 신규 진입을 잠근다.

## 5. 비용과 체결

- BASE는 entry·exit 6bp, 250ms 지연, 기본 slippage를 사용한다.
- STRESS는 entry·exit 12bp, 500ms 지연, slippage 2배를 사용한다.
- fee는 실제 PAPER fill notional에 진입·청산 각 1회만 적용한다.
- LONG 진입은 ask, SHORT 진입은 bid를 소진하며 청산은 반대쪽 호가를 쓴다.
- sequence invalid, stale, 다른 venue의 book을 체결에 사용하지 않는다.
- IOC full·partial·zero fill과 dust 거부를 기존 `PaperExecutionEngine`으로 처리한다.

## 6. Exit style

- 현재 REVERSION C는 최근 2분 이탈 극값 바깥을 손절, 과도이탈 전 실제 가격대 중앙을 TP1, 반대편 피벗·실제 이탈폭 대칭 범위를 TP2로 쓴다. 10초 micro-VWAP은 목표가가 아니라 구조 재진입 확인에만 쓰며 TP1 70%, TP2 30%다.
- 현재 TREND B는 최근 10초 실거래 충격·눌림 구조로 손절·TP1·TP2를 정하고, TP1 40% 후 남은 60%를 실제 눌림폭으로 추적한다.
- 현재 TREND L∼O는 완성 15·30분·1시간 피벗, 이전 일 고저, 돌파 측정폭과 ATR 채널에서 TP1·TP2를 정한다. TP1 40% 후 남은 60%를 완성봉 ATR Chandelier로 추적한다.
- 모든 현재 전략은 실제 호가·수수료·슬리피지 후 순손익비가 부족하면 임의 R 배수로 TP를 늘리지 않고 진입을 거부한다.
- TP1 후 stop과 추적선은 이익 보호 방향으로만 움직이며 불리한 방향으로 넓히지 않는다.
- 기존 미시구조 전략은 진입 후 30초 유예, 서로 다른 불리 근거 2개, 비용대보다 큰 실제 가격 악화와 3초 지속을 모두 확인한 뒤에만 `EDGE_DECAY`로 종료한다.
- 신규 L~O 중단기 추세 전략은 일반 미시구조 `EDGE_DECAY`와 경과시간 청산을 사용하지 않는다. 진입 전에 고정한 구조 손절, TP1·TP2, 이익보호 방향의 손절 단축과 데이터·시스템 안전종료만 사용한다.
- 최초 stop은 불리한 방향으로 넓히지 않는다.

## 7. 신규 전략 E·F·G·H·I·J

- E는 spread, top5·top10 imbalance, 250ms·3s OFI, 1s 체결, microprice 변위를 본다.
- F는 방향보존 signed notional robust z, 3s·10s 체결, OFI, 가격반응, microprice를 본다.
- E·F는 strategy·symbol·side별 정렬 시작 event timestamp를 저장한다.
- 정렬이 500ms 이상 실제로 지속될 때만 통과하고, 조건이 깨지면 시각을 초기화한다.
- G는 top10 가격 간격과 수량을 함께 반영한 공정가, 최우선 microprice, OFI, 체결과 가격반응을 750ms 확인한다.
- H는 3s OFI를 top10 평균 깊이로 보정한 bp와 과거 prefix robust z, OFI, 체결, microprice와 가격반응을 500ms 확인한다.
- I는 깊이보정 OFI robust z, 250ms·3s OFI와 prefix 기반 3초 수익률의 방향 동행을 1,000ms 확인한다. 기준가격은 목표 시각보다 최대 1.5초까지만 오래될 수 있고 미래 timestamp는 제외한다.
- J는 top10 가격거리 1bp당 누적 명목깊이의 매수·매도 기울기를 계산한다. 반대호가 하위 15%, 지지호가 중앙값 이상, 기울기비 1.5배 이상과 OFI·체결·microprice·가격반응을 1,000ms 확인한다.
- 현재 TREND B는 최근 10초 실거래 충격·눌림의 실제 가격 구조를 쓴다. OFF인 D∼J의 이전 고정 geometry는 과거 표본 설명으로만 보존하며 현재 진입에 사용하지 않는다.
- robust statistic은 현재 시점 이전 prefix만 사용하며 Replay도 같은 timestamp 규칙을 쓴다.

## 7.1 전략 K 완성 시간봉 추세

- K는 인증 없는 Binance USDⓈ-M 공개 REST에서 deep universe 12종목의 완성 1시간봉을 불러오고 종목마다 200개 이상을 준비한다. 진행 중 봉은 사용하지 않는다.
- LONG은 EMA20>EMA50, EMA80>EMA200, EMA80 상승 기울기, 24시간 수익률 +2% 이상, 직전 20봉 Donchian 상단 돌파, ADX 20 이상, 상대거래량 1.1 이상을 함께 요구한다. SHORT는 대칭 조건이다.
- 동일 완성봉을 반복 진입신호로 사용하지 않으며 새 완성봉 뒤 5초 이내의 sequence-valid 실제 bid·ask가 있을 때만 계획을 만든다.
- TP1은 2.2R에서 40%, TP2는 4.5R에서 60%다. 예상 보유는 1~36시간이고 `maximum_holding_ms`는 36시간이다.
- Wave 41의 같은 기간 진단은 일부 양수였지만 bootstrap 하한·DSR·PBO와 독립성 gate를 충족하지 못했다. 조건을 고정한 뒤 다운로드한 앞선 147일에서 166건을 재검증한 Wave 46 결과는 BASE 기대값 -18.263bp·PF 0.856, STRESS 기대값 -30.263bp·PF 0.775였다. 따라서 K는 RETIRED·OFF이며 수익성은 `NOT_PROVEN`이다.

## 7.2 공통 비용후 계획과 event-time 조건

- 현재 REVERSION C와 TREND B는 실제 가격 경로의 극값·눌림 구조 밖에 호가차 완충을 더해 stop을 정한다. OFF인 A·D∼J의 이전 최소 거리 계약은 과거 표본에만 적용한다.
- 이 거리는 손실예산을 늘리지 않는다. main은 현재자산의 0.1%, League는 0.5% 위험예산에 맞춰 수량을 줄인다.
- 최종 CandidatePlanner는 실제 bid·ask, worst entry, 양방향 fee, 예상 exit slippage와 분할청산 비중을 다시 계산하고 순손익비 1.20 미만을 거부한다.
- A의 refill·범위 재진입과 C의 구조 재진입은 실제 event timestamp로 지속시간을 계산한다.
- B/D의 눌림 시간·최대 되돌림·현재 재가속은 현재 이전 가격 prefix만 사용하고, 재가속 정렬이 끊기면 확인시각을 초기화한다.
- 자세한 근거와 한계는 `docs/adr/ADR-013-cost-viable-event-time-strategy-gates.md`를 따른다.

## 7.3 신규 L~O 완성봉 추세 V2

- L은 완성 15분 EMA20·EMA80와 완성 1시간 추세, 24시간 모멘텀이 정렬된 상태에서 EMA20 눌림과 이전 고저 회복을 기다린다.
- M과 N은 각각 완성 15분 32봉, 완성 30분 24봉 구조 돌파를 즉시 추격하지 않는다. 다음 완성봉이 돌파선을 지지·저항으로 재확인한 뒤에만 평가한다.
- O는 완성 30분과 1시간 추세가 같은 방향일 때 30분 EMA20 조정 뒤 이전 고저 회복을 확인한다.
- 네 전략은 새 완성봉 뒤 5초 이내 sequence-valid 실제 bid·ask에서 1초 이상 OFI·aggressor 체결·microprice가 같은 방향일 때만 후보가 된다.
- 구조 손절 거리는 0.65~3.0 ATR 범위여야 하고 최종 `CandidatePlanner`의 실제 호가·수수료·슬리피지 후 순손익비 1.20 gate를 다시 통과해야 한다.
- L∼O는 완성봉 구조 가격에서 TP1·TP2를 정하고 TP1 후 완성봉 ATR runner를 쓴다. 네 전략의 `maximum_holding_ms`는 `null`이며 시간만으로 종료하지 않는다. 데이터 이상은 별도 안전종료로 처리한다. 세부 결정은 ADR-138을 따른다.
- 이전 V1 연구와 달리 돌파 추격 대신 완성봉 재확인과 현재 공개 주문흐름을 함께 요구한다. 네 전략은 사전등록된 SHADOW PAPER 가설이며 수익성은 `NOT_PROVEN`이다.

## 8. Recovery와 출력

- recovery schema v5는 Registry, snapshot timestamp, 계좌별 risk, pending map, position map, order·trade, 계획별 최대보유시간, 구조 손절·TP1·TP2 근거, 확인 시간구간과 계좌·종목별 PAPER 생명주기 revision cursor를 보존한다.
- schema v1~v3는 복구한 pending·position의 실제 상태에서 새 revision cursor를 시작하며, 과거 snapshot에 없던 revision을 추정하지 않는다. schema v4의 cursor·상태·마지막 전환이 불일치하면 fail-closed한다.
- schema v1의 `pending_entry`, `position`, `SHADOW:` account ID를 새 map과 account ID로 변환한다.
- 과거 snapshot에 전혀 없던 신규 Registry 전략의 BASE·STRESS 계좌는 additive extension으로 각각 1,000 USDT 빈 계좌를 생성한다. 기존 전략의 한 profile만 누락된 불완전 snapshot은 계속 fail-closed한다.
- 복구한 모든 open·pending symbol은 fresh public book 재검증 전까지 신규 진입을 잠근다.
- dashboard backend는 `league_accounts` 30행과 열린 `league_positions`를 제공한다.
- 전략·profile별 자산, 손익, 비용, 승패, 명목금액, 레버리지, drawdown, 잠금 상태를 출력한다.

## 9. 검증 명령

```bash
uv run pytest backend/tests/test_strategies.py backend/tests/test_strategy_registry_shadow.py backend/tests/test_strategy_league_signals.py backend/tests/test_candidate_paper_portfolio.py backend/tests/test_strategy_league_portfolio.py backend/tests/test_execution_and_risk.py backend/tests/test_v02_runtime_recovery.py -q
uv run ruff check backend
uv run mypy
uv run pytest backend/tests -q
```

- UI, browser, network, 30분·6시간·24시간 soak는 1차 Strategy League 백엔드 범위가 아니다.

## 10. 2차 UI·제어 연결

- `league_accounts` 30행은 15개 등록 전략 카드와 BASE/STRESS 상세의 계좌 원본이다.
- `league_positions`는 BASE/STRESS 필터, 종목·방향 필터와 차트 계획선의 원본이다.
- ACTIVE는 main Shared Capital Benchmark와 League, SHADOW는 League만 유지한다.
- 표시 mode를 바꾸는 UI는 기존 Registry 설정 API를 사용하며 별도 체결 엔진을 만들지 않는다.
- 차트 지표는 화면 참고용이고 등록 전략의 진입 threshold를 변경하지 않는다.
- 시작·새 Run은 비동기 operation으로 제어하되 30계좌 위험·복구·원장 경계는 이 문서의 PAPER 계약을 그대로 유지한다.

## 11. 3차 사용자 표현과 종목별 성과

- 사용자 메뉴와 화면에서는 `전략`으로 줄여 표시하고, 내부 Registry·DB·개발문서의 Strategy League 식별자는 호환을 위해 유지한다.
- 전략 설정은 current entry variant를 family 중심 compact 행으로 표시한다. 사용자는 `SHADOW`와 `OFF`만 선택하고 `ACTIVE`는 Governor 전용임을 설명한다. current SHADOW 6개, 기록 보존용 RETIRED 5개, legacy RESEARCH·OFF 4개를 구분하며, RETIRED·legacy 행의 재활성화 제어는 비활성화한다.
- `전략별 종목 성과`는 BASE/STRESS를 분리하고 실제 완료 PAPER 거래만 집계한다. 30건 미만 조합은 관찰 표본이며 순위에서 제외한다.
- 전략 통계는 독립 Strategy League 거래만 집계하고 공동계좌 거래를 같은 전략 표본에 중복 합산하지 않는다.
- 상세 화면은 승·패·보합, 승률 95% 범위, 기대값, Profit Factor, 비용, 낙폭, 보유시간과 진입→TP1·TP2·손절 중앙시간 및 각 표본 수를 표시한다.
- 포지션 집중 selector는 BASE를 먼저 정렬하지만 모든 계좌는 독립 회계와 진입 당시 선택 PAPER 배수를 그대로 유지한다.

## 12. V6 family와 고유기회 계약

- 15개 strategy ID는 원장 호환을 위해 유지하고 8개 family의 variant로 묶는다. Family별 current variant는 최대 하나다.
- Entry가 아닌 order-flow FILTER, ROUTER, LEGACY와 미검증 MARKET_NEUTRAL_MULTI_LEG는 기본 entry 순위와 거래 수에서 제외한다.
- BASE와 STRESS는 독립 회계를 유지하지만 한 `opportunity_id`의 두 비용결과다. 기본 표본은 `(run_id, strategy_id, strategy_version, opportunity_id, symbol, side)`의 고유 수다.
- 부분 TP·runner fill은 같은 고유기회 안에 남긴다. 기본 거래표는 opportunity 한 행 안에 BASE·STRESS를 나란히 표시하고 원시 원장은 상세에서 보존한다.
- 기본 전략 순위는 적격 표본의 Wilson 95% 하한 내림차순이다. Raw win rate만으로 순위·퇴역·승격하지 않는다.
- V3 후보는 기존 V2를 덮어쓰지 않고 offline 사전등록으로 남긴다. 같은 동결입력 비교가 없으면 `NOT_PROVEN`, promotion false다.
- 실제 주문·private API·API Key·wallet은 0이며 수익성 `NOT_PROVEN`, `FUNDING_READINESS=NOT_READY`다.
