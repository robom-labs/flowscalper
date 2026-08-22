# 09. Dashboard UI/UX

## 9.1 Design direction

Create a polished, modern dark trading dashboard with original visual design. It may take inspiration from professional trading terminals but must not copy a proprietary site's branding or exact layout.

Korean is the default UI language.

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

Fields:

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
