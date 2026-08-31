# ADR-V6. Strategy family와 네 페이지 사용자 인터페이스

- 상태. `ACCEPTED`.
- 결정일. 2026-08-31.
- 범위. 기존 15개 전략의 family·variant 해석, Governor, 거래 집계, 사용자 navigation과 상세 조회.
- 제외. 실제 주문, private API, 자격 증명, wallet, 실자금 승격과 결과 뒤 V3 조정.

## 맥락

기존 제품은 전략·성과, 진행 포지션·기록·replay, 위험·시스템 정보를 여러 route에 중복했다. 15개
strategy ID도 entry, filter, legacy, 연구후보가 한 목록에 섞였다. 기존 공통 70% 승률 gate는
낮은 승률과 큰 payoff가 정상인 추세 runner를 기대값과 무관하게 퇴역시킬 수 있었다. BASE와
STRESS 비용결과를 두 거래로 더하면 표본도 부풀 수 있었다.

## 결정

1. 사용자 기본 navigation은 `시장`, `전략`, `거래`, `설정` 네 페이지로 고정한다. 시장이 기본이다.
2. 성과는 전략에, 진행·완료·replay는 거래에, 위험·시스템은 설정에 합친다. raw diagnostics와 연구
   상세는 on-demand다.
3. 기존 strategy ID와 원장은 보존하면서 8개 family로 해석한다. Family는 `TREND_PULLBACK`,
   `BREAKOUT_RUNNER`, `ORDERFLOW_CONFIRMATION`, `EXHAUSTION_REVERSION`,
   `POSITIONING_LIQUIDATION`, `MARKET_REGIME_FILTERS`, `SESSION_PROFILE`,
   `MARKET_NEUTRAL`이다.
4. 모든 descriptor는 `family_id`, `role`, `variant_id`, `is_current_variant`, `supersedes`,
   `superseded_by`, 기본 노출·모의평가·순위 적격성을 명시한다. Family별 current variant는 최대
   하나다.
5. Entry가 아닌 FILTER, ROUTER, MARKET_NEUTRAL_MULTI_LEG, LEGACY는 독립 entry 순위에 넣지
   않는다. 검증되지 않은 multi-leg engine은 OFF다.
   서로 다른 family의 같은 symbol·같은 방향 충돌은 evidence tier, STRESS 비용후 기대값,
   cost coverage, liquidity quality, setup freshness, diversification 순으로 하나만 선택한다.
   반대 방향은 router가 없거나 명확하지 않으면 shared trade를 만들지 않는다.
   `ORDERFLOW_CONFIRMATION_FILTER_V2`는 Registry 밖의 family API/UI 가상 current `FILTER`다.
   기본 OFF, final-ranking 제외이며 15개 Registry 전략과 30개 계좌를 늘리지 않는다. 이 필터는
   score·9개 구성요소·500ms 지속·data health를 기록하지만 `CandidatePlan`과 거래를 만들지 않고,
   ON/OFF는 expected-revision CAS로만 변경한다.
6. 기존 V2의 의미를 같은 ID에서 변경하지 않는다. V3와 새 V2 후보는 별도 ID로 사전등록하고 같은
   동결 입력·비용으로 비교한다. 자료가 없으면 `NOT_PROVEN`, promotion은 false다.
7. 공통 70% 관측승률 승격·퇴역·격리 gate를 제거한다. 비용후 기대값, PF, OOS 하한, DSR, PBO,
   parameter robustness, 독립기간, 위험계약과 운영건강은 공통 gate로 유지하고 승률·payoff는
   family별로 결과 전에 고정한다.
8. BASE와 STRESS는 한 진입기회의 비용결과다. 기본 표본 key는
   `(run_id, strategy_id, strategy_version, opportunity_id, symbol, side)`이며 부분 exit도 새
   기회로 세지 않는다. 계정은 여섯 key에 추가하지 않고 같은 기회 내부의 `(account_scope,
   account_id)`별 `account_groups`로 MAIN과 LEAGUE를 분리한다. 각 그룹의 BASE/STRESS와 부분행은
   따로 보존하고 기본 MAIN 표시가 LEAGUE 결과를 덮어쓰지 않는다. 연결을 검증할 수 없는 legacy
   결과는 unresolved `NOT_PROVEN`으로 남긴다.
9. 기본 전략 정렬은 표본 적격 후보의 Wilson 95% 하한 내림차순이다. 30건 미만의 관측 100%는
   순위에서 제외한다.
10. `/api/ui/summary`는 작은 실시간 요약이고 settings, diagnostics, family catalog/detail/
    conditions와 trades는 분리된 REST API다. `/ws/ui`는 최초 `snapshot` 뒤 `summary_delta`,
    `position_delta`, `strategy_row_delta`, `selected_detail_delta` 또는 unchanged `heartbeat`만
    전송한다. 클라이언트 `select_family`는 선택 상세를 갱신하고 history·conditions·entry rules는
    WS가 아니라 on-demand REST로 읽는다. Fixture 기준 summary 직렬화 크기는 `/api/dashboard`의
    50% 미만이어야 한다.
11. 실제 주문, private API, API Key, secret, 인증, wallet, 입출금, runtime AI 주문판단은 계속
    0이다. `FUNDING_READINESS`는 `NOT_READY`다.

## Family별 사전등록 gate

| Family | 고유기회 | 승률 신뢰 | Payoff | PF | 추가 조건 |
|---|---:|---|---:|---:|---|
| `TREND_PULLBACK` | 150 | raw 0.40, Wilson 하한 0.32 | 1.50 | 1.20 | 공통 강건성 |
| `BREAKOUT_RUNNER` | 150 | 승률 하한 없음 | 2.00 | 1.25 | 양의 왜도, 최대거래 기여 10% 미만 |
| `EXHAUSTION_REVERSION` | 150 | Wilson 하한 0.38 | 1.30 | 1.15 | 공통 강건성 |
| 독립 micro alpha | 1,000 | 없음 | 비용회수 4.0 | 1.15 | filter가 아닌 경우만 적용 |

## Migration

- 기존 15개 ID와 30개 BASE·STRESS 계좌, 거래, replay, settings revision을 그대로 보존한다.
- Family metadata는 additive다. 의미가 달라지는 전략은 새 version ID를 사용한다.
- 5개 navigation route의 저장값은 `market`, `strategies`, `trades`, `settings` 중 대응 페이지로 한
  번 변환한다. 알 수 없는 값은 `market`으로 fail-safe한다.
- 제거 대상은 old page wrapper, route, 전용 copy·CSS·test다. replay engine, 원장과 과거 전략은
  제거하지 않는다.
- 열린 PAPER 포지션은 전략 표시 version과 원 계좌로 끝까지 보호한다. Migration을 이유로 강제
  청산하지 않는다.

## Rollback

Rollback은 설치 릴리스 단위로 수행한다. V6가 쓴 원장과 기존 strategy/account ID는 변경하지 않아
이전 릴리스가 기존 계좌를 계속 읽을 수 있다. V6 전용 family preference는 실행결정과 분리된 로컬
설정이며 이전 릴리스가 무시해도 PAPER 원장은 손상되지 않는다. 열린 포지션이 있으면 배포·rollback을
미루며 서비스 강제 종료를 정당화하지 않는다.

## 검증과 증거 경계

- Backend는 family 완전성, current 최대 하나, legacy 기본 OFF, family Governor와 고유기회 집계를
  검증한다.
- Frontend는 정확히 네 primary item, 시장 기본, obsolete route 부재, family·거래·설정 통합과
  desktop·tablet·mobile 접근성을 검증한다.
- Payload benchmark는 fixture 직렬화 구조만 증명하며 장기 성능이나 수익성을 증명하지 않는다.
- V2·V3 비교, 30분, 6시간, 24시간은 실제 실행하지 않으면 `NOT_RUN`이다.
- 최신 frontend 전체 test 15개 파일·92개 test와 lint·typecheck·build는 각각 `PASS`했다.
- 최신 격리 8876 fixture E2E는 desktop·tablet·mobile 3개 project가 모두 `PASS`했다. 네 페이지
  핵심 흐름, console·page error 0건, 가로 overflow 없음까지 확인했지만 설치된 8870 실서비스의
  browser 증거로 확대 해석하지 않는다.
- 최신 변경을 포함한 backend 전체 suite는 `NOT_RUN_AFTER_LATEST_CHANGE`다. release·설치·설치된
  8870 실서비스·30분 soak·remote push도 별도 증거 전에는 `NOT_RUN`이다. 이전 browser 실패나
  그 이전 PASS를 최신 판정으로 재사용하지 않는다.
- 코드·UI test PASS는 수익성 증거가 아니다. 수익성은 `NOT_PROVEN`, 실자금은 `NOT_READY`다.

## 대체 관계

이 ADR은 사용자 navigation에 관해 ADR-010의 5개 메뉴 결정을 대체하고, 공통 70% gate에 관해
ADR-087, ADR-091, ADR-093, ADR-098의 해당 조항을 대체한다. 최소표본·OOS·강건성·DSR·PBO를
요구하는 ADR-038은 유지한다.

## 외부 근거 적용 경계

- TradingView의 [strategy FAQ](https://www.tradingview.com/pine-script-docs/faq/strategies/)와 [repainting 설명](https://www.tradingview.com/pine-script-docs/concepts/repainting/)은 완료봉·현재 이전 prefix만 사용하는 no-lookahead 규칙의 참고 근거로 사용한다. 특정 Pine 전략의 수익 주장은 가져오지 않는다.
- Freqtrade의 [lookahead analysis](https://www.freqtrade.io/en/stable/lookahead-analysis/), [recursive analysis](https://www.freqtrade.io/en/stable/recursive-analysis/), [backtesting](https://www.freqtrade.io/en/stable/backtesting/) 문서는 미래참조·초기 history 의존성·백테스트 체결 가정 점검표의 참고 근거다. FlowScalper의 executable bid·ask, depth, fee, spread, slippage 계약을 대체하지 않는다.
- GOV.UK의 [details](https://design-system.service.gov.uk/components/details/), [service navigation](https://design-system.service.gov.uk/patterns/navigate-a-service/), [table](https://design-system.service.gov.uk/components/table/)과 GitHub Primer의 [progressive disclosure](https://primer.style/product/ui-patterns/progressive-disclosure/)는 네 페이지·기본 단순화·상세 on-demand·비교표 구조의 참고 근거다. 제3자 branding과 문구는 복제하지 않는다.
- Bailey 등의 [Probability of Backtest Overfitting](https://escholarship.org/uc/item/4w1110bb)과 [Deflated Sharpe Ratio](https://doi.org/10.3905%2Fjpm.2014.40.5.094)는 PBO·DSR·multiple-testing 보정의 연구 근거다. 이 지표 하나나 테스트 통과만으로 수익성 또는 승격을 주장하지 않는다.
