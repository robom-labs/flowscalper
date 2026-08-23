# Strategy League 백엔드 명세

## 1. 범위

- FlowScalper 0.2.0-paper의 기존 공개시장 PAPER 런타임을 확장한다.
- 실제 주문, private API, API Key, secret, wallet, 인증은 없다.
- main Shared Capital Benchmark와 Strategy League는 같은 후보·호가 체결 엔진을 사용한다.

## 2. 전략 Registry

| 구분 | strategy_id | 한국어 이름 | 기본 mode | exit style |
|---|---|---|---|---|
| A | `LSA_REVERSAL_V1` | 급락·급등 쓸기 반전 | ACTIVE | REVERSION_70_30 |
| B | `CBR_CONTINUATION_V1` | 압축 돌파 재가속 | ACTIVE | TREND_40_60 |
| C | `VWAP_EXHAUSTION_REVERSION_V1` | VWAP 과도이탈 평균복귀 | SHADOW | REVERSION_70_30 |
| D | `OFI_CONTINUATION_PULLBACK_V1` | OFI 추세 눌림 지속 | SHADOW | TREND_40_60 |
| E | `QUEUE_MICROPRICE_MOMENTUM_V1` | 호가 쏠림 순간추세 | SHADOW | TREND_40_60 |
| F | `AGGRESSOR_FLOW_CONTINUATION_V1` | 강한 체결 흐름 지속 | SHADOW | TREND_40_60 |

- 모든 전략은 LONG·SHORT를 독립적으로 허용하거나 끌 수 있다.
- OFF는 평가하지 않고, ACTIVE는 main과 League, SHADOW는 League에만 후보를 제공한다.

## 3. 독립 계좌

- 6개 전략마다 BASE·STRESS를 두어 총 12개 계좌를 생성한다.
- account ID는 `STRATEGY_ID:PROFILE`이다.
- 각 계좌는 1,000 USDT로 시작한다.
- 자산, 손익, 수수료, 슬리피지, 위험, cooldown, fault를 섞지 않는다.
- 다른 전략은 같은 종목의 반대 방향을 동시에 보유할 수 있다.
- 같은 계좌·종목에 pending과 open을 중복 생성하지 않는다.
- 전략 계좌는 서로 다른 종목을 최대 3개까지 보유한다.
- main은 기존 전역 중재와 최대 1개 계약을 유지한다.

## 4. 전략 계좌 위험

- 포지션당 위험은 현재 자산의 0.5%다.
- open과 pending의 계획위험 합은 1.5%를 넘지 않는다.
- 하루 2%, 주간 5%, peak 대비 drawdown 8%에서 해당 계좌만 신규 진입을 잠근다.
- 수량은 stop 거리, 양방향 fee, 예상 exit slippage로 먼저 계산한다.
- 계좌 총 명목금액은 자산의 5배, 주문은 실행가능 깊이의 2%를 상한으로 둔다.
- 5배는 강제 레버리지가 아니라 위험기반 수량의 최대 상한이다.
- partial fill은 실제 수량의 위험·명목금액·fee만 open으로 옮긴다.
- 시장데이터·저장·복구의 시스템 fault는 12계좌 모두의 신규 진입을 잠근다.

## 5. 비용과 체결

- BASE는 entry·exit 6bp, 250ms 지연, 기본 slippage를 사용한다.
- STRESS는 entry·exit 12bp, 500ms 지연, slippage 2배를 사용한다.
- fee는 실제 PAPER fill notional에 진입·청산 각 1회만 적용한다.
- LONG 진입은 ask, SHORT 진입은 bid를 소진하며 청산은 반대쪽 호가를 쓴다.
- sequence invalid, stale, 다른 venue의 book을 체결에 사용하지 않는다.
- IOC full·partial·zero fill과 dust 거부를 기존 `PaperExecutionEngine`으로 처리한다.

## 6. Exit style

- REVERSION은 TP1 70%, TP2 30%다.
- 유효한 micro-VWAP은 REVERSION TP1이고, 아니면 1.2R이다.
- REVERSION TP2는 전략의 구조 target을 쓴다.
- TREND는 TP1 40%를 1.5R, TP2 60%를 3.0R에 둔다.
- TREND TP1 후 stop은 fee-adjusted break-even보다 불리하게 옮기지 않는다.
- 추세·OFI·microprice edge가 2회 연속 무너지면 runner를 `EDGE_DECAY`로 종료한다.
- 최초 stop은 불리한 방향으로 넓히지 않는다.

## 7. 신규 전략 E·F

- E는 spread, top5·top10 imbalance, 250ms·3s OFI, 1s 체결, microprice 변위를 본다.
- F는 방향보존 signed notional robust z, 3s·10s 체결, OFI, 가격반응, microprice를 본다.
- E·F는 strategy·symbol·side별 정렬 시작 event timestamp를 저장한다.
- 정렬이 500ms 이상 실제로 지속될 때만 통과하고, 조건이 깨지면 시각을 초기화한다.
- E·F 구조계획은 0.20% stop 거리와 3.2R target을 사용해 공통 비용 gate를 낮추지 않는다.
- robust statistic은 현재 시점 이전 prefix만 사용하며 Replay도 같은 timestamp 규칙을 쓴다.

## 8. Recovery와 출력

- recovery schema v2는 Registry, snapshot timestamp, 계좌별 risk, pending map, position map, order·trade를 보존한다.
- schema v1의 `pending_entry`, `position`, `SHADOW:` account ID를 새 map과 account ID로 변환한다.
- v1에 없던 E·F 계좌는 1,000 USDT 빈 계좌로 생성한다.
- 복구한 모든 open·pending symbol은 fresh public book 재검증 전까지 신규 진입을 잠근다.
- dashboard backend는 `league_accounts` 12행과 열린 `league_positions`를 제공한다.
- 전략·profile별 자산, 손익, 비용, 승패, 명목금액, 레버리지, drawdown, 잠금 상태를 출력한다.

## 9. 검증 명령

```bash
uv run pytest backend/tests/test_strategies.py backend/tests/test_strategy_registry_shadow.py backend/tests/test_strategy_league_signals.py backend/tests/test_candidate_paper_portfolio.py backend/tests/test_strategy_league_portfolio.py backend/tests/test_execution_and_risk.py backend/tests/test_v02_runtime_recovery.py -q
uv run ruff check backend
uv run mypy
uv run pytest backend/tests -q
```

- UI, browser, network, 30분·6시간·24시간 soak는 1차 Strategy League 백엔드 범위가 아니다.
