# 07. Position and Exit Management

## 7.1 Core principle

Short-horizon entry does not imply a fixed 120-second liquidation. The position remains open only while the original thesis remains valid and the expected remaining edge is positive after exit cost.

## 7.2 Exit hierarchy

1. Data/system safety exit.
2. Structural stop.
3. Edge-decay or thesis-invalidation exit.
4. Take-profit.
5. Profit-protection exit.
6. Emergency stale-position exit.

The implementation must define deterministic conflict resolution and choose the conservative path when events are ambiguous.

## 7.3 Initial stop invariants

- Computed before entry.
- Tied to the structural invalidation point plus noise buffer.
- Never moves farther from entry.
- May remain unchanged or tighten.
- A stop update is persisted and explained.

## 7.4 Position health vector

Calculate continuously:

- `structure_health`;
- `flow_health`;
- `microprice_alignment`;
- `liquidity_health`;
- `spread_health`;
- `opposite_aggression`;
- `data_health`;
- `remaining_edge`;
- `current_R` and `MFE/MAE`.

Do not collapse all safety checks into a single opaque score. Maintain components and reason codes.

## 7.5 Edge-decay exit

An early exit may occur before TP/SL when all configured persistence requirements pass, for example:

- OFI reverses and remains adverse;
- microprice is persistently on the wrong side of mid;
- aggressive counter-flow moves price efficiently;
- refill behavior supporting the trade disappears;
- price returns through the setup's confirmation boundary;
- spread widens and remaining target no longer covers cost;
- empirical/calibrated remaining edge falls to zero or below.

The PAPER default uses three separate guards against single-update churn:

- a 10,000 ms grace period after the fill for ordinary edge-decay exits;
- at least two simultaneous adverse health reasons;
- 3,000 ms of continuous event-time persistence after the grace period.

Initial SL/TP and data/system safety policies remain active during the grace period. Once MFE
reaches +0.8R, profit protection may bypass the grace period, but it still requires two adverse
reasons and 3,000 ms persistence. See `ADR-014`.

## 7.6 Profit protection

Optional initial logic:

- after MFE reaches approximately +0.8R, monitor edge decay more aggressively;
- after +1.0R and sufficient persistence, stop may move to cost-adjusted breakeven or a structure-protecting level;
- never tighten so aggressively that normal one-tick noise guarantees exit;
- initial version uses one full-position exit, not complex multi-level scaling.

All thresholds are configurable and validated in replay.

## 7.7 Expected resolution time

Estimate likely setup resolution horizons such as:

- 5 s;
- 15 s;
- 30 s;
- 60 s;
- 120 s;
- 300 s.

In cold start, derive from structural/volatility heuristics and label as diagnostic. After sufficient data, use empirical distributions.

This value is **not** a forced exit deadline. It is used for monitoring and stale detection.

## 7.8 Emergency stale-position policy

The purpose is to prevent an unintended scalp from becoming an indefinite position after software/data failure.

Policy:

```text
if sufficient history:
    emergency_age = min(15 minutes, strategy-symbol-regime holding-time q99.5 × safety_multiplier)
else:
    emergency_age = 15 minutes
```

Do not automatically exit at emergency age if a deterministic, healthy management policy still has clear positive edge unless the product contract requires a hard ceiling. For v0.1, 15 minutes is the final safety ceiling and must be labeled `EMERGENCY_STALE_LIMIT`, not normal strategy time.

## 7.9 Data-gap behavior

When critical data becomes stale while holding a paper position:

- prohibit new entries;
- freeze non-safety strategy updates;
- preserve existing stop/TP plan;
- attempt same-venue reconnect;
- on first valid book, evaluate conservative gap fill and safety state;
- record gap duration and outcome.

Never substitute another venue mid-position.

## 7.10 Trade completion

A trade is not complete until:

- simulated position quantity is zero;
- all simulated contingent orders are canceled/finalized;
- all fills and fees are posted;
- state is reconciled;
- trade record is immutable;
- cooldown begins;
- replay slice is finalized.

## 7.11 Activated trailing runner contract

An explicitly configured PAPER candidate may use the eight-state runner lifecycle from
`ENTRY_PENDING` through `CLOSED`. Existing strategies receive no implicit trailing policy.

- LONG favorable movement and the trailing trigger use a fresh, sequence-valid executable
  best bid. SHORT uses the corresponding best ask.
- A percentage, fixed-distance, ATR Chandelier, completed-structure or preregistered
  edge-adaptive policy may tighten protection, but never widen it.
- ATR·Chandelier·structure 기준은 신호 전에 끝난 연속 완성봉에서 산출해 불변 계획에
  고정한다. 누락봉·미완성봉·한 시간구간보다 오래된 참조는 신규 PAPER 진입을 거부한다.
- Edge-adaptive 축소는 건강한 데이터에서 서로 다른 adverse 근거 두 개 이상이 3초
  지속된 뒤에만 활성화한다. 한 번의 OFI·체결흐름 변화로 즉시 좁히지 않는다.
- TP1-triggered policies arm only from the TP1 execution path. An R-multiple policy cannot
  be armed merely because TP1 was requested.
- A rejected or partially filled PAPER exit keeps the remaining runner protected and uses
  the existing delayed depth-walk execution path.
- State transitions are append-only. A favorable mark or trail change without a state
  transition is also persisted as `TRAILING_MARK_UPDATED` so restart recovery does not
  restore an older stop.
- Adaptive adverse 시작시각·사유·활성상태는 `TRAILING_EDGE_STATE_UPDATED`로 저장하며,
  재시작 뒤 같은 지속시간 상태를 복원한다.
- 거래가 완전히 닫히면 activation 시각, 최고 실행가능 미실현손익, peak giveback,
  비용을 배분한 runner 순기여, trigger 이후 실제 depth 체결까지의 추가 가격차이 비용과
  최종 trailing checksum을 불변 거래행에 함께 고정한다.
- The implementation is research infrastructure, not promotion or profitability evidence.
  ACTIVE remains zero until every preregistered gate passes.
