# 09. Dashboard UI/UX

## 9.1 Design direction

Create a polished, modern dark trading dashboard with original visual design. It may take inspiration from professional trading terminals but must not copy a proprietary site's branding or exact layout.

Korean is the default UI language.

일반 화면은 초보자가 바로 판단해야 하는 핵심 정보만 펼쳐서 보여준다. Run ID, trade ID,
strategy ID, 원시 종료 코드, BASE/STRESS 같은 내부 용어와 연구 통계는 쉬운 한국어로
번역하거나 접힌 `기술 정보`·`고급 통계`에 둔다. 원장 정밀도와 원시 코드는 삭제하지 않는다.

## 9.2 Permanent safety banner

Every trading screen must prominently show:

```text
실시간 시장데이터
페이퍼 계좌 전용
실제 주문 없음
시작자산 1,000 USDT
```

Use unmistakable badges:

- `LIVE DATA` only when verified;
- `PAPER` always for execution;
- `OFFLINE FIXTURE` when applicable;
- venue badge;
- latency/health badge.

## 9.3 Navigation

Required pages:

1. 라이브
2. 거래내역
3. 리플레이
4. 성과분석
5. 위험관리
6. 시스템

## 9.4 Live page layout

### Top bar

- mode/data badges;
- venue;
- connection health;
- data lag p50/p95;
- current equity;
- today net PnL;
- accumulated fees;
- drawdown;
- open position count;
- pause button;
- paper emergency close.

### Left scanner

Columns:

- rank;
- symbol;
- universe/deep status;
- regime;
- strategy;
- direction;
- candidate score;
- net R:R;
- expected cost;
- spread;
- data health;
- status/rejection reason.

Allow search, sorting and filters. Do not overload the table with fake probabilities during calibration.

### Center chart

- candlestick interval selector: 1s, 5s, 15s, 1m;
- bid, ask, mid, microprice;
- micro-VWAP;
- structure levels;
- sweep/pullback zones;
- entry, TP and SL horizontal lines;
- candidate and fill markers;
- zoom and pan;
- tooltip with local and UTC time.

### Right current-trade panel

- symbol and venue;
- side and strategy;
- signal time;
- planned and actual entry;
- TP and SL;
- quantity and notional;
- risk budget and maximum planned loss;
- current gross/net PnL;
- fees and slippage;
- elapsed time;
- expected resolution diagnostic;
- structure/flow/liquidity/edge health;
- current management reason;
- paper close button.

### Bottom event log

Structured timeline with filters:

- market data;
- candidate;
- rejection;
- fill;
- protection;
- exit;
- risk;
- connection;
- error.

## 9.5 Candidate explanation

Generate Korean explanations from deterministic templates, for example:

```text
SOLUSDT 롱 후보
- 15분 구조 저점 하향 이탈 후 범위 복귀
- 매도 체결 강도 Z 2.1
- 하락 가격반응 효율이 낮아 흡수 가능성 확인
- 매수호가 보충과 OFI 반전 지속 700ms
- 예상 총비용 13.4bp
- 구조적 순손익비 1.27
```

For rejection:

```text
진입 거부
- 현재 스프레드 9.2bp > 허용 6.5bp
- 목표폭에서 비용 비중 41% > 허용 30%
```

## 9.6 Trade history

기본 목록에 펼쳐서 표시할 필드:

- symbol and Korean side;
- Korean strategy/account label;
- net PnL with gross PnL and total cost summary;
- Korean exit reason and short explanation;
- holding time and replay action.

다음 원장 필드는 거래 상세의 접힌 `기술 정보`에 보존한다:

- Run;
- venue;
- trade ID;
- symbol;
- strategy;
- side;
- entry/exit time;
- planned/actual entry;
- TP/SL;
- exit reason;
- gross PnL;
- fees;
- slippage;
- net PnL;
- R multiple;
- MAE/MFE;
- holding time;
- replay link.

작은 화면에서는 가로 스크롤로 잘린 표 대신 행별 카드로 재배치한다.

## 9.7 Replay

Replay controls:

- play/pause;
- speed;
- step event;
- jump to candidate/entry/exit;
- show order book/feature panel;
- show decision snapshot;
- compare expected and actual simulated fill.

## 9.8 Performance

기본 화면은 현재 자산·이번 실행 순손익·완료 표본·승률·거래당 기대값·총비용·낙폭·
표본상태를 먼저 보여준다. Profit Factor, R/bp 기대값, Omega, Sortino, Calmar, MAE/MFE와
추적 익절 상세는 접힌 고급 통계에서 제공한다.

Charts and metrics:

- equity curve;
- drawdown;
- gross versus net PnL;
- cumulative fees/slippage;
- win rate with sample size;
- profit factor;
- expectancy;
- strategy/symbol/regime breakdown;
- holding-time distribution;
- MAE/MFE;
- rejected-candidate analysis;
- BASE versus STRESS comparison;
- probability calibration only when enabled and valid.

## 9.9 Risk page

- immutable current Run assumptions;
- current locks and cooldowns;
- create new Run dialog;
- paper pause/resume;
- symbol denylist;
- warning that changes do not affect prior Runs.

## 9.10 System page

- venue endpoints and connection state;
- subscribed streams;
- processing lag;
- queue depth;
- CPU/memory;
- disk usage/retention;
- reconnect/gap counters;
- app version and commit;
- network diagnostics;
- logs download.

## 9.11 Accessibility and responsiveness

- keyboard navigation;
- adequate contrast;
- text labels in addition to color;
- responsive for common desktop widths;
- no critical controls hidden behind hover only;
- confirmation for destructive paper reset/close actions.

## 9.12 Phase 02 current screen contract

The current beginner navigation is `시장`, `전략`, `진행 거래`, `거래 기록`, `과거 재생`,
`성과`, `안전 설정`, `고급진단`, `시스템`. The home separates the Registry-derived BASE
account total from the 1,000 USDT Shared Capital Benchmark and always explains that the
larger League total is a comparison of independent accounts, not shared or real money.

The Strategy League derives every row and BASE/STRESS account total from the backend
Registry payload. `ACTIVE` is `공동·독립 모의 중`, `SHADOW` is `독립 모의 중`, and
`OFF` is `꺼짐`. The current safe default is B ACTIVE, C/F/G/I/J SHADOW and A/D/E/H
RETIRED/OFF, with LONG/SHORT controls preserved for all registered strategies. The drawer
shows horizon, expected holding, signal half-life, required input intervals, exit model,
cost version, lifecycle evidence and revision history before BASE/STRESS analytics. League
positions default to BASE and contain no manual real buy or sell action.

Strategy performance uses only each independent League account. Shared benchmark trades
are not added to the same strategy sample. The drawer shows wins, losses, breakevens,
Wilson 95% win-rate range, expectancy, Profit Factor, costs, drawdown and holding-time
distribution. Ledger precision is preserved while the UI uses magnitude-aware decimals.

The advanced terminal owns scanner, chart, current PAPER position and recent events. The
scanner uses stable venue/symbol row keys, fixed 64px rows, fixed columns, rate-limited rank
updates and a fixed drawer that never changes chart dimensions. The chart uses candles,
volume, MA5/20 by default and optional MA10/60, EMA20, VWAP, Bollinger, bid/ask/microprice,
RSI and MACD panes. Indicator choices do not change strategy entry criteria. Pan, return to
real time, crosshair KST tooltip and fullscreen/CSS fallback are required.

All visible buttons and selects are at least 48px high. Desktop 1408×900, tablet 820×1180
and mobile 390×844 must have no document-level horizontal overflow.

## 9.13 Phase 03 compact market and focus contract

- User navigation is `시장`, `전략`, `기록`, `분석`, `설정`; market is the default. Old user-facing League and advanced-terminal wording is removed.
- At 1408×900 the market rail is 260px and the chart uses the remaining width without root scroll. Tablet and mobile use an overlay market sheet.
- The default chart is 3-minute, 200 historical candles, MA10/MA20 and volume overlay. RSI and MACD panes are created only while enabled and the popover never resizes the chart.
- An actual new PAPER `trade_id` fill may open focus mode. Candidate, qualified signal and pending entry never do. The user can lock the current trade or select another BASE/STRESS position.
- Focus desktop is 176px plan rail, flexible central chart and 208px PnL rail. Tablet/mobile keep the chart width and open plan/PnL details as sheets.
- Focus has no buy, sell, real-order or API-key controls. Missing funding is omitted rather than displayed as a fake zero cost.

## 9.14 시작·작동·안전 대기 상태 계약

- 시장 첫 화면은 큰 운영 상태 패널에서 프로그램 결과를 즉시 보여준다. `자동 관찰 시작`을 누른 뒤에는 `연결 중`과 서버의 실제 단계 문구를 표시하고 중복 시작을 막는다.
- `작동 중`은 공개시장 관찰과 새 PAPER 진입 경로가 모두 작동 중일 때만 사용한다.
- 지연이나 일시적인 안전잠금에서는 `작동 중 · 안전 대기`로 표시하고 `시장 관찰 계속 작동`, `새 PAPER 진입 안전 대기`, `자동 복구 켜짐`을 함께 보여준다. 이 상태에 수동 재개 버튼을 표시하지 않는다.
- 사용자 버튼으로 멈춘 경우에만 `사용자가 일시정지`와 `새 진입 다시 시작`을 표시한다.
- 저장 실패나 복구 불일치처럼 자동 해제하지 않는 잠금은 `작동 중 · 안전 확인 필요`로 표시하고 고급진단 확인을 안내한다.
- 데이터 지연 p95는 같은 패널에 밀리초로 표시하며, 아직 표본이 없을 때는 `측정 대기`로 표시한다.
- 모든 상태에서 PAPER 전용과 실제 주문 0 표시는 유지한다.

## 9.15 현재 PAPER와 전략 감시 상태 계약

- 시장 화면은 열린 모든 PAPER 포지션을 종목·방향·전략·BASE/STRESS·단계·순손익으로 표시하고 선택하면 해당 종목 차트로 이동한다.
- 선택 종목에 열린 포지션이 있으면 차트 안에서 방향, 전략, 비용 프로필, entry, TP1, SL과 같은 종목의 추가 진행 건수를 보여준다. 포지션 종료 뒤 오래된 banner를 남기지 않는다.
- 모든 등록 전략은 각각 `꺼짐`, `확인 필요`, `안전 대기`, `PAPER 진입 중`, `진입 조건 감지`, `준비 중`, `정상 감시 중` 중 한 상태를 표시한다. 현재 10개 SHADOW 감시와 5개 퇴역 기록을 구분하고, 퇴역 전략은 비용후 검증 실패와 재활성화 잠금을 함께 표시한다.
- 정상 감시 중인 전략은 최근 조건 대기 이유와 평가경로 수를 함께 표시한다. 거래가 없다는 사실만으로 전략 오류나 미실행으로 표현하지 않는다.
- 시스템 기본 화면은 실행호가·체결 p95를 함께 표시하고 wide scanner p95는 고급진단에서 `진입판정 아님`이라고 구분한다.

## 9.16 전략 생명주기와 변경 이력

- 전략 표는 `연구 중`, `독립 검증 중`, `도전자`, `현재 대표`, `안전 격리`, `퇴역·보존`을 쉬운 한국어로 표시한다.
- 상세 drawer는 마지막 평가 시각, 현재 대표, 비용후 근거 부족 이유, 다음 평가까지 필요한 자연표본·기간과 `NOT_PROVEN` 상태를 표시한다.
- 승률 하나만으로 꺼졌다고 설명하지 않는다. OOS 하한, BASE/STRESS, 강건성, DSR/PBO, cooldown 또는 두 평가 주기 악화 같은 실제 reason code를 한국어로 해석한다.
- 70% 운영 후보 정책은 `기본 비용 승률`, `보수 비용 승률`, `30건까지 남은 표본`을 쉬운 한국어로 설명한다. 원시 reason code는 고급 진단 안에만 두며, 30건 미만의 100% 표본을 검증 완료처럼 표시하지 않는다.
- 사용자 고정과 자동 변경 가능 상태를 구분한다. 변경 이력은 revision, 생명주기, 주체, 이유와 시각을 보존한다.
- `직전 설정으로 복원`은 확인 후 과거 설정을 새 revision으로 복원하며 기존 이력을 삭제하지 않는다.
