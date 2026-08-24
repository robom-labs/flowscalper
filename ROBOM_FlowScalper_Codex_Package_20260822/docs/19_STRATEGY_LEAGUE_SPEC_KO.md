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
- 기본값은 6개 전략 모두 OFF가 아니고 LONG·SHORT가 켜진 상태다. A/B는 ACTIVE, C~F는 SHADOW를 유지한다.

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
- TREND 전략 B/D/E/F 구조계획은 최소 0.30% stop 거리와 3.2R target을 사용해 공통 비용 gate를 낮추지 않는다.
- robust statistic은 현재 시점 이전 prefix만 사용하며 Replay도 같은 timestamp 규칙을 쓴다.

## 7.1 공통 비용후 계획과 A~D 시간 조건

- REVERSION 전략 A/C는 최소 0.80%, TREND 전략 B/D/E/F는 최소 0.30%의 구조 stop 거리를 사용한다.
- 이 거리는 손실예산을 늘리지 않는다. main은 현재자산의 0.1%, League는 0.5% 위험예산에 맞춰 수량을 줄인다.
- 최종 CandidatePlanner는 실제 bid·ask, worst entry, 양방향 fee, 예상 exit slippage와 분할청산 비중을 다시 계산하고 순손익비 1.20 미만을 거부한다.
- A의 refill·범위 재진입과 C의 구조 재진입은 실제 event timestamp로 지속시간을 계산한다.
- B/D의 눌림 시간·최대 되돌림·현재 재가속은 현재 이전 가격 prefix만 사용하고, 재가속 정렬이 끊기면 확인시각을 초기화한다.
- 자세한 근거와 한계는 `docs/adr/ADR-013-cost-viable-event-time-strategy-gates.md`를 따른다.

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

## 10. 2차 UI·제어 연결

- `league_accounts` 12행은 6개 전략 카드와 BASE/STRESS 상세의 계좌 원본이다.
- `league_positions`는 BASE/STRESS 필터, 종목·방향 필터와 차트 계획선의 원본이다.
- ACTIVE는 main Shared Capital Benchmark와 League, SHADOW는 League만 유지한다.
- 표시 mode를 바꾸는 UI는 기존 Registry 설정 API를 사용하며 별도 체결 엔진을 만들지 않는다.
- 차트 지표는 화면 참고용이고 A-F 진입 threshold를 변경하지 않는다.
- 시작·새 Run은 비동기 operation으로 제어하되 12계좌 위험·복구·원장 경계는 이 문서 1차 계약을 그대로 유지한다.

## 11. 3차 사용자 표현과 종목별 성과

- 사용자 메뉴와 화면에서는 `전략`으로 줄여 표시하고, 내부 Registry·DB·개발문서의 Strategy League 식별자는 호환을 위해 유지한다.
- 전략 설정은 6개 compact 행과 쉬운 ACTIVE·SHADOW·OFF 의미를 사용한다. A-F threshold와 위험값은 바꾸지 않는다.
- `전략별 종목 성과`는 BASE/STRESS를 분리하고 실제 완료 PAPER 거래만 집계한다. 30건 미만 조합은 관찰 표본이며 순위에서 제외한다.
- 전략 통계는 독립 Strategy League 거래만 집계하고 공동계좌 거래를 같은 전략 표본에 중복 합산하지 않는다.
- 상세 화면은 승·패·보합, 승률 95% 범위, 기대값, Profit Factor, 비용, 낙폭과 보유시간을 표시한다.
- 포지션 집중 selector는 BASE를 먼저 정렬하지만 모든 계좌는 독립 회계와 최대 5배 위험 상한을 그대로 유지한다.
