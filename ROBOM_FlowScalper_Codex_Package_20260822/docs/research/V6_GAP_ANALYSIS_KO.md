# V6 현재 구현 격차 분석

## 판정 범위

이 문서는 Git `ac5634a53da623721dc3bb6113427a32d4a677db`, 설치 릴리스
`50c3e8ae7af08667546e8a1f2e4a70890e92d0f6`, 마지막 관찰 Run
`run-2b7135a972dd`를 기준선으로 삼는다. V6 감사 중 서비스는 안전하게 중지됐고 다시 시작하지
않았다. 동적 runtime 값은 마지막 관찰값과 현재 중지 상태를 분리한다.

기준선은 15개 Registry 전략, BASE·STRESS 30개 독립 PAPER 계좌, ACTIVE 0개, SHADOW
10개, RETIRED·OFF 5개다. 마지막 관찰의 열린 포지션은 0개, 현재버전 원장은 32개 비용결과와
16개 고유 진입기회였다. 실제 주문과 인증은 false였다. 이는 작동 기준선이며 수익성 증거가
아니다.

## 현재 구현된 기능

- 공개시장 데이터와 executable bid·ask 기반 PAPER 체결, 비용·부분체결·안전잠금이 있다.
- Registry의 15개 전략과 전략별 BASE·STRESS 계좌, 과거 원장, replay가 있다.
- 시장, 전략, 기록, 분석, 설정의 기존 5개 navigation 계약이 있었다.
- 전략 설명, 전략 성과, 진행 포지션, 완료 기록, replay, 위험 및 시스템 진단이 각각 존재한다.
- 현재 기록 화면은 BASE와 STRESS를 한 진입기회로 묶고 상세에서 비용결과를 분리할 수 있다.
- 현재 source에는 4-page route, split REST/WS, family detail·conditions telemetry와
  `ORDERFLOW_CONFIRMATION_FILTER_V2` CAS UI가 배선돼 있다. 최신 동시 변경 뒤 통합 재검증은 아직
  완료되지 않았으므로 이 문장의 상태는 source 구현이며 browser `PASS`가 아니다.
- 작은 표본은 `NOT_PROVEN`으로 남기고 실제 주문, private API, API Key, wallet은 0이다.

## 사용자에게 중복되던 기능

- 전략 목록과 성과 화면이 표본, 승률, 순손익, 상태를 중복 표시했다.
- 진행 포지션, 완료 기록, replay가 서로 다른 진입점으로 분산됐다.
- 위험관리와 시스템 화면이 동일한 안전 상태와 진단 원인을 반복했다.
- 시장 기본화면과 별도 라이브 화면이 연결·지연·포지션 상태를 중복 표시했다.
- 같은 가설의 V1·V2와 여러 order-flow 전략이 독립 방향전략처럼 한 목록에 섞였다.

V6 기본 navigation은 `시장`, `전략`, `거래`, `설정` 네 개다. 성과는 전략에, 진행·완료·replay는
거래에, 위험·시스템은 설정에 합친다. 사용자가 보는 기본 route에서 제거된 wrapper를 병행 유지하지
않는다.

## 기본 화면에서 숨길 기술정보

- 원시 Run ID, checksum, 내부 reason code, queue와 WAL의 전체 카운터를 기본 카드에서 숨긴다.
- raw strategy condition payload와 모든 diagnostics를 WebSocket 매 tick마다 보내지 않는다.
- BASE·STRESS 내부 계좌 합계를 두 거래 수처럼 기본 요약에 노출하지 않는다.
- legacy·superseded variant의 개별 order-flow entry와 미구현 multi-leg 엔진은 기본 순위에서
  숨긴다. 가상 current 주문흐름 필터 자체는 ON/OFF와 상태를 보여 주되 entry 순위에는 섞지 않는다.
- raw 관측 승률만으로 기본 순위를 만들지 않고 Wilson 95% 하한과 고유기회 수를 먼저 표시한다.

진단 원문은 삭제하지 않는다. 문제가 있을 때 또는 사용자가 고급 진단을 펼쳤을 때만 읽기 전용으로
제공한다.

## 반드시 남길 정보

- 현재 자산, 비용후 실현·미실현 손익, 공개시장 연결, 신규진입 허용 여부와 쉬운 대기 이유를 남긴다.
- 열린 PAPER 포지션의 entry, initial/current stop, TP, trail, 잔량과 계좌 profile을 남긴다.
- 완료 거래의 fills, fee, spread, slippage, exit reason, MFE, MAE, giveback과 replay를 남긴다.
- strategy family의 현재 variant, 조건별 기준·현재값·통과 여부, 출처와 이전 version을 남긴다.
- 원장, replay engine, 과거 전략, 이전 version, 실패 증거와 PAPER 안전 이력을 삭제하지 않는다.

## 8개 strategy family와 중복 가설

| Family | 현재 variant | 기존 variant 처리 | 기본 역할 |
|---|---|---|---|
| `TREND_PULLBACK` | `TREND_PULLBACK_RECLAIM_15M_V2` | 30분 재합류 V2는 비교 variant | ENTRY |
| `BREAKOUT_RUNNER` | `BREAKOUT_RETEST_30M_V2` | CBR·15분 retest는 variant, hourly V1은 LEGACY | ENTRY |
| `ORDERFLOW_CONFIRMATION` | `ORDERFLOW_CONFIRMATION_FILTER_V2` 가상 current | OFI·queue·microprice·aggressor·book-slope는 독립 순위가 아닌 확인 계층 | FILTER |
| `EXHAUSTION_REVERSION` | `VWAP_EXHAUSTION_REVERSION_V1` | LSA는 과거를 보존한 LEGACY | ENTRY |
| `POSITIONING_LIQUIDATION` | 없음 | OI·funding·basis·청산 연구용 | FILTER |
| `MARKET_REGIME_FILTERS` | 없음 | trend·range·deleveraging router용 | ROUTER |
| `SESSION_PROFILE` | 없음 | POC·VAH·VAL 연구용 | FILTER |
| `MARKET_NEUTRAL` | 없음 | 검증 전 multi-leg 엔진은 OFF | MARKET_NEUTRAL_MULTI_LEG |

Family는 기존 strategy ID와 원장을 지우는 이름 변경이 아니다. 한 family의 current variant는 최대
하나며 legacy·superseded variant는 기본 ON과 최종 entry 순위에서 제외한다.

주문흐름 current는 Registry descriptor가 아니라 family API/UI에서 합성한다. 따라서 Registry
`current_by_family`는 없음이 정확하고 사용자 family current는 filter V2가 정확하다. 이 필터는 기본
OFF, final-ranking 제외이며 영향 대상은 15분 추세 눌림과 30분 돌파 retest 두 entry다. 9개
구성요소 score·통과 수·500ms 지속·data health와 uplift `NOT_PROVEN`을 제공하지만 Registry 전략,
계좌, `CandidatePlan`과 거래를 각각 0개만큼 늘린다. ON/OFF는 CAS revision으로 보존한다.

서로 다른 family가 같은 symbol·같은 방향 entry를 만들면 evidence tier, STRESS 비용후 기대값,
cost coverage, liquidity quality, setup freshness, diversification 순으로 하나만 선택한다. 반대
방향은 router가 없거나 명확하지 않으면 shared trade를 만들지 않는다.

## old/current version 충돌

- 기존 V2 의미를 같은 ID에서 바꾸지 않는다. `TREND_PULLBACK_RECLAIM_15M_V3`,
  `MULTISPEED_TREND_RECLAIM_30M_V3`, `BREAKOUT_RETEST_30M_V3`는 별도 offline 후보다.
- 소진 평균복귀의 새 의미는 `EXHAUSTION_VWAP_REENTRY_V2`로 사전등록한다.
- V3 후보는 동결 입력 비교 전 Registry current, runtime 등록, LIVE SHADOW, promotion이 모두 false다.
- 기존 V1·V2의 거래, replay와 settings revision은 보존한다.

## Governor 문제와 V6 계약

기존 공통 70% 관측승률은 낮은 승률·높은 payoff의 breakout runner를 구조적으로 배제한다. V6는
공통 70% 승격·퇴역·격리 규칙을 제거하고 다음 공통 gate를 유지한다.

- BASE·STRESS 비용후 기대값과 순손익, Profit Factor.
- OOS 하한, bootstrap, DSR, PBO, parameter robustness와 독립기간.
- 위험계약, 수동잠금, 데이터·운영 건강, atomic champion replacement.

승률·payoff는 결과 전에 family별로 고정한다. `TREND_PULLBACK`은 고유기회 150, raw 승률
0.40, Wilson 하한 0.32, payoff 1.50, PF 1.20을 요구한다. `BREAKOUT_RUNNER`는 승률 하한 대신
고유기회 150, payoff 2.00, PF 1.25, 양의 왜도와 단일 최대거래 기여 10% 미만을 요구한다.
`EXHAUSTION_REVERSION`은 고유기회 150, Wilson 하한 0.38, payoff 1.30, PF 1.15를 요구한다.
독립 micro alpha로 평가할 때는 고유기회 1,000, 비용회수 4.0, PF 1.15를 요구한다.

## 성과집계 문제와 정정

BASE와 STRESS는 한 `opportunity_id`의 서로 다른 비용결과다. 기본 거래 수는 다음 key의 고유 수다.

```text
(run_id, strategy_id, strategy_version, opportunity_id, symbol, side)
```

부분 TP와 runner fill도 새 진입기회로 세지 않는다. 완료 거래 기본표는 고유기회 한 행 안에 BASE와
STRESS 결과를 나란히 둔다. 계정은 위 여섯 key에 포함하지 않는다. 같은 기회 안의 MAIN과 LEAGUE는
`(account_scope, account_id)`별 `account_groups`로 나눠 각 BASE/STRESS와 부분행을 보존하며,
기본 MAIN profile이 LEAGUE 결과를 덮어쓰지 않는다. 연결이 검증되지 않는 legacy 행은 삭제하거나
추정 병합하지 않고 unresolved `NOT_PROVEN`으로 남긴다. 마지막 기준선의 32개 raw 결과는 16개
고유기회이며 이를 32건 성과표본으로 부풀리지 않는다.

전략 기본 정렬은 raw 승률이 아니라 표본 gate를 통과한 Wilson 95% 하한 내림차순이다. 작은 표본의
100%는 `순위 제외`이며 수익성은 `NOT_PROVEN`이다.

## 화면구조 문제와 제거·통합 계획

1. 시장은 상태·자산·PnL·chart·진행 포지션·신규진입 대기 이유만 먼저 보여 준다.
2. 전략은 8개 family와 current variant, 모의평가 ON/OFF, Wilson·EV·PF·net을 합친다.
3. 거래는 진행 중, 완료, replay를 탭으로 합치고 BASE·STRESS를 한 기회로 표시한다.
4. 설정은 신규진입 pause/resume와 위험·시스템 요약을 합치고 raw diagnostics는 접는다.
5. 기존 Live, LeaguePositions, Performance, Replay, Risk, System, StrategySymbol wrapper와 전용
   route·CSS·test는 같은 변경에서 제거한다. 원장과 replay engine은 유지한다.
6. `/api/ui/summary`는 `/api/dashboard` 직렬화 크기의 50% 미만을 목표로 하고 상세는 on-demand로
   조회한다.

REST는 summary, settings, diagnostics, family catalog/detail/conditions와 trades로 분리한다.
`/ws/ui`는 최초 snapshot 뒤 summary, position, strategy-row, selected-detail delta 또는 변경 없는
heartbeat만 보낸다. 클라이언트 `select_family`는 선택 상세를 갱신하지만 history·전체 conditions·
entry rule은 WebSocket payload에 섞지 않는다. 전략 상세 conditions는 evaluator의 기준·현재값·
상태·이유를 표시하며 측정이 없을 때는 `WAITING_DATA`로 남긴다.

## 검증 상태

- 최신 frontend 전체 test는 15개 파일·92개 test가 `PASS`했고 lint, typecheck, build도 각각
  `PASS`했다.
- `make e2e-simple-user-flow`는 격리된 8876 fixture에서 desktop·tablet·mobile 3개 project 모두
  `PASS`했다. 네 페이지 핵심 흐름, console·page error 0건, 가로 overflow 없음까지 확인했다. 이
  결과는 fixture UI 경로의 증거이며 설치된 8870 실서비스 검증을 대신하지 않는다.
- 4페이지·family·Governor·고유기회·상세 on-demand·payload target의 source와 집중 검증을
  최신 공유 코드 기준으로 다시 실행했다. backend 전체 suite는 1,004개 test, frontend 전체
  suite는 92개 test가 `PASS`했다. 수정 전 E2E `FAIL_PRESERVED`나 그 이전 PASS는 최신 판정으로
  재사용하지 않았다.
- V2 대 V3 동결 입력 비교는 입력 자료가 없으므로 `NOT_RUN`, 결과는 `NOT_PROVEN`, promotion은
  false다.
- V6 설치 뒤 실서비스와 30분, 6시간, 24시간 안정성은 실제 설치·경과한 실행만 PASS로 쓸 수 있으며
  모두 `NOT_RUN`이다.
- release·설치·설치된 8870 browser 검증·remote push는 아직 수행하지 않아 `NOT_RUN`이다.
- 실제 주문, private API, API Key, wallet은 0을 유지한다.
- 수익성은 `NOT_PROVEN`, `FUNDING_READINESS`는 `NOT_READY`다.
