# 10. Storage, Replay and Analytics

## 10.1 Storage layers

### SQLite

Use transactional tables for:

- app settings;
- Runs;
- universe snapshots;
- candidates;
- paper orders;
- fills;
- positions;
- trades;
- risk locks;
- system incidents;
- persisted state-machine snapshots.

### Parquet

Partition compressed market and feature data by:

```text
venue/date/symbol/hour/event_type
```

### DuckDB

Use for analytical queries across Parquet and exported reports.

## 10.2 Data retention

Suggested defaults:

- full deep-book events: 7 days;
- candidate/trade windows: retain indefinitely or until user deletes;
- aggregated 1s features/candles: 90 days;
- trade records and Run summaries: indefinite;
- automatic disk-pressure warning and entry pause before storage exhaustion.

Retention must be configurable and visible.

## 10.3 Trade capture window

For every candidate that reaches ARMED or execution:

- retain pre-event market window, initially 2–5 minutes;
- retain entire holding period;
- retain post-exit window, initially 30–60 seconds;
- retain feature and decision snapshots;
- retain book data needed for fill reconstruction.

## 10.4 Replay determinism

A replay should use:

- recorded events;
- recorded Run configuration;
- recorded strategy version;
- recorded fee/latency model;
- deterministic clock and RNG seed.

Expected result: the same recorded events and version reproduce the same decision and fill path, subject to explicitly versioned migrations.

## 10.5 Metrics

Per trade:

- gross/net PnL;
- fees;
- signal-to-arrival slippage;
- depth-walk slippage;
- stop slippage;
- R multiple;
- MAE/MFE;
- entry/exit latency;
- holding time;
- exit reason;
- ambiguity flags;
- data-health incidents.

Per Run:

- net return;
- peak and maximum drawdown;
- profit factor;
- expectancy;
- trade count;
- win/loss size distributions;
- cost burden;
- strategy/symbol/regime contribution;
- consecutive losses;
- downtime and gaps;
- BASE/STRESS divergence.

## 10.6 Statistical honesty

- Always display sample size with win rate.
- Distinguish in-sample, validation, out-of-sample and live-paper periods.
- Do not annualize short samples by default.
- Do not hide fees or excluded trades.
- Preserve rejected candidates for selection-bias analysis.
- Clearly mark assumptions versus observed values.

## 10.7 Export

Provide export to:

- CSV trade list;
- JSON Run summary;
- HTML performance report;
- compressed replay bundle;
- diagnostic logs.

No personal credentials exist in exports.

## 10.8 Phase 03 trade focus and strategy-symbol analytics

- `focus_positions` normalizes actual entry, initial/current stop, TP1/TP2, quantities, planned loss, fee/slippage, net PnL, account equity, stage, data health and permanent PAPER flags.
- Strategy×symbol reports group completed ledger trades by strategy, profile and symbol. Ranking is withheld below 30 samples and always shows costs and sample status.
- A replay focus request uses at least 20 minutes pre-roll and 5 minutes post-roll where stored events exist. Frames are capped at 50,000; state changes and first/last frames are preserved while market-only frames may be downsampled.
- Replay markers are cursor-bounded. Entry, partial fill and exit information cannot appear before its event timestamp.
- `ReplayClock` uses `performance.now`, frame timestamp deltas and allowed speeds 0.5/1/2/5/10/20/40/80. Speed changes presentation only.

## 10.9 Phase 03 latency and replay hardening

- New market-event files partition by `venue/run/date/symbol/hour/event_type`; the Run dimension prevents an active Run from repeatedly scanning or appending into another Run's dense partition.
- The live persistence worker writes at 2,000-event thresholds, records flush count/last/max milliseconds and flushes a final sub-threshold batch on shutdown.
- Binance trade coalescing is exact within symbol, aggressor side and 250ms bucket. Quantity and notional are summed, price is VWAP and source/output counts remain observable.
- Focus replay inserts deterministic `PAPER_LEDGER_TRANSITION` frames at the stored entry and exit timestamps. These frames originate from the immutable PAPER trade/fill ledger, never from invented market prices, and guarantee an honest CLOSED review even when post-roll market events are absent.

## 10.10 Current strategy-version performance scope

- The default strategy, profile and strategy×symbol reports include only independent `LIVE_PUBLIC` shadow trades whose full `strategy_version` equals the current implementation revision.
- Prior-version trades remain immutable and queryable. The current UI and API disclose how many prior-version samples were excluded instead of deleting or silently mixing them.
- Legacy shadow payloads are checksum-verified first and may be enriched in memory from their immutable Run `config_json` and `config_hash`; the stored payload and checksum are never rewritten.
- New completed shadow trades persist both the Run `config_hash` and full `strategy_version`.
- `DEMO_FIXTURE` and `REPLAY` samples never enter current LIVE_PUBLIC win rate, expectancy, Profit Factor, cost, drawdown or holding-time statistics.
- See `docs/adr/ADR-017-current-strategy-version-performance-scope.md` for the decision and regression boundaries.

## 10.11 Large replay isolation and focus cache

- While LIVE public observation is active, full Run replay, timeline reads and trade-focus replay share one process lock and execute in a low-priority child process with independent SQLite and Parquet readers.
- A `nice(19)` child process applies a one-core 5% cooperative CPU budget to each checkpoint interval across archive decoding, strategy ingestion, event sorting, duplicate checks and streaming SHA-256. The interval calculation prevents old high-load work from creating unbounded later sleep debt. Replay completion time is secondary to uninterrupted LIVE ingestion.
- Replay checksum schema 3 length-prefixes each normalized event and decision-path item into separate streaming SHA-256 digests. The final canonical material contains only those digests, counts, config, version and final state, so it does not duplicate the full event list in memory.
- New archive batches expose `venue_ts_ms`, `symbol`, `event_type` and `batch_checksum` columns. Time-bounded UI reads select relevant manifests, verify the complete selected batch checksum before filtering and decode only matching rows; truncated batches fail even when the remaining filtered rows look valid. Legacy batches keep the full checksum-compatible fallback.
- Trade-focus reads are bounded to the configured pre/post trade window. Completed sessions are zlib-compressed in schema v7 `replay_focus_cache` and verified by SHA-256 before reuse.
- Full Run replay, timeline and trade-focus requests share one lock. A concurrent request receives HTTP 409 `REPLAY_BUSY` instead of waiting behind a long replay and appearing frozen.
- The default LIVE history view includes only main PAPER trades whose `sample_type` is `LIVE_PUBLIC` and whose strategy implementation version equals the current build. Older immutable trades remain stored and are reported as excluded.
- LIVE event lag uses a public venue-time offset estimated from the minimum-RTT sample. The process never changes the operating-system clock and never adds credentials to the public time request.
- See `docs/adr/ADR-018-replay-cpu-budget-focus-cache-and-venue-clock.md` for the decision and failure boundaries.

## 10.12 Bounded active ledger persistence

- The active SQLite ledger and the immutable public-market Parquet archive can live on different volumes. Entry safety checks both volumes and fails closed when either free-byte or free-ratio threshold is breached.
- Every execution audit row remains append-only. Rejection-only audit batches do not duplicate the complete recovery payload because they do not mutate an order, pending entry, position, protection, fill or account risk state.
- A recovery snapshot is written after a state-mutating audit. Strategy-account history writes only the shadow accounts named by those mutations instead of all strategy/profile accounts.
- The in-memory `CandleBuilder` continues to provide every supported chart interval. SQLite persists canonical 1-second candles and the 180-second replay focus interval only; the other chart intervals are deterministic derivatives and are not duplicated permanently.
- On macOS, the active ledger, immutable releases, Python base and venv, bytecode and tool caches, temp files, stage results and service logs all live on the mounted external APFS volume under `05_RUNTIME/ROBOM_FlowScalper`. The only internal file is the small user LaunchAgent plist required by macOS. `ROBOM_ACTIVE_LEDGER_DIR` or `ROBOM_DB_PATH` may override the default only when the service installer still verifies an external runtime contract.
- The LaunchAgent directly calls the immutable runner on the already mounted external APFS volume and writes both logs there. macOS privacy can reject a background Agent that attaches the sparsebundle from the outer drive, so a missing APFS mount is an explicit unavailable state rather than an internal fallback or a successful localhost service.
- Before SQLite or service-mode startup, a WAL larger than 64MiB requires zero external handles, APFS `clonefile(2)` preservation of DB/WAL/SHM and a closed `wal_checkpoint(TRUNCATE)` to 0byte. Failure keeps the recovery snapshot and prevents service startup. See `docs/adr/ADR-132-external-only-runtime-bootstrap-and-oversized-wal-recovery.md`.
- An isolated persistence `BrokenWorkerProcess` preserves the pending batch and locks only new PAPER entries while storage safety is rechecked. A safe recovery retries through a replacement process; an existing hard fault remains fail-closed. See `docs/adr/ADR-133-external-persistence-worker-recovery-and-release-retention.md`.
- The external runtime keeps the current immutable release and one manifest-verified rollback release. Git history and GitHub Release preserve older source and packages; unknown or unverifiable runtime directories are never deleted automatically.
- Existing ledgers are never silently deleted or rewritten. A migration must stop the service, copy and checksum the closed SQLite files, run `PRAGMA quick_check` and foreign-key checks, retain a recoverable pre-migration copy, then restart and verify the recovered Run.
- See `docs/adr/ADR-024-bounded-active-ledger-and-volume-safety.md`.

## 10.13 늦은 공개 체결의 저장과 실행 분리

- 500ms보다 늦게 도착한 공개 aggregate trade도 원본성 있는 시장 사건이므로 immutable archive에는 보존한다.
- 같은 이벤트를 현재 candle·체결흐름·전략 피처에 뒤늦게 적용하지 않는다. 해당 종목은 신선한 trade가 도착할 때까지 전략입력 `data_healthy=false`를 유지한다.
- replay는 저장 당시의 stale 표식과 reason flag를 보존해 LIVE와 동일한 유효성 경계를 재현한다.
- See `docs/adr/ADR-026-executable-book-trade-lag-and-strategy-visibility.md`.

## 10.14 Research manifest and chronological intraday reports

- Every research output binds the exact code commit, configuration hash, fixed seed, dataset Run IDs, event counts, time ranges and per-Run SHA-256 checksums before recording the final result checksum.
- Train, Validation and OOS Run IDs are fixed before execution. Horizon-specific maximum holding time is used as purge and embargo around chronological boundaries.
- Partial Run or maximum-event diagnostics are labeled `PARTIAL_DIAGNOSTIC_NOT_EVIDENCE`; only the complete preregistered archive may be considered for OOS assessment.
- The intraday report retains all 180 preregistered hypotheses, including no-signal rows. The 60 mechanical mirrors are baselines, while 120 ORIGINAL and separate reverse hypotheses count toward multiple-testing correction.
- JSON is the machine-readable source. HTML is a human-readable projection of the same result. A hash or deterministic replay PASS proves reproducibility, not profitability.
- Research outputs never modify current Registry settings, PAPER accounts or immutable execution ledgers.

## 10.15 Observable replay operations and bounded UI timeline

- Full strategy replay is a persisted-audit background operation. POST returns 202 immediately; status and cancellation endpoints expose ordered states, elapsed time, scope, estimated event count and terminal result.
- LIVE replay cancellation propagates into the isolated process call. Refreshing the browser reattaches to an active operation, while server shutdown cancels it before closing the ledger.
- Replay result history loads after the Run list and preview. A slow historical result scan cannot leave Run and symbol controls blank, and subsequent reads use the verified in-process cache.
- Interactive timeline reads at most the most recent 100 checksum-verified events and only the 1-second candles inside that event window. It walks backward through only the newest manifests that contain the selected symbol, verifies every archive batch it actually uses and restores canonical event order before returning. The full stored count remains visible, and the screen explicitly labels this as a recent display window.
- Full strategy validation is a separate persisted operation and continues to process every stored event in the fixed selected-symbol scope. When active SQLite rows and immutable Parquet archives coexist, its canonical replay order is `(receive_ts_ms, receive_monotonic_ns, venue_ts_ms, event_id)`. Exchange time remains the market-time filter and chart axis, but an event may enter the strategy information set only in the order it was actually received. The wall-clock receive time keeps order valid across process or machine restarts, while monotonic time resolves events received in the same millisecond. Full or historical-range research requests therefore verify and merge every relevant archive batch before applying their declared scope.
- See `docs/adr/ADR-043-observable-cancellable-bounded-replay.md`.
- See `docs/adr/ADR-108-replay-preview-live-reader-isolation.md` for the recent interactive window boundary.

## 10.16 활성 writer와 거래 상세 재생 cache

- 거래 상세 재생의 candle·프레임·entry·TP·SL·종료 정보는 불변 원장과 공개시장 archive에서 먼저 완성하고 checksum을 계산한다.
- `replay_focus_cache`는 같은 결과의 다음 조회를 빠르게 하기 위한 선택적 압축 cache다. cache 쓰기가 활성 외부 writer의 SQLite `locked` 또는 `busy`와 충돌하면 완성된 재생 세션을 그대로 반환하고 cache만 생략한다.
- lock·busy가 아닌 무결성·직렬화·스키마 오류는 숨기지 않고 실패시킨다. 원본 원장 읽기나 checksum 검증이 실패한 경우에도 cache 정책으로 성공 처리하지 않는다.
- UI는 거래 상세 API 실패를 빈 화면으로 오해하지 않도록 명시적 실패 문구와 `거래 차트 다시 시도` 버튼을 표시한다.
- See `docs/adr/ADR-046-best-effort-focus-cache-under-durable-writer.md`.

## 10.17 대형 활성 원장의 닫힌 전수 무결성 검증

- full `PRAGMA quick_check`와 `foreign_key_check`는 활성 writer 연결이나 같은 물리 I/O device의 사본에서 직접 실행하지 않는다.
- 유지관리 전 LIVE·PAPER·RUNNING, 동일 Run, 포지션 0, queue·임계지연·저장·재연결 안전선과 실제주문·인증 false를 확인한다.
- LaunchAgent는 최소 60초 종료 유예로 persistence worker를 기다린다. process handle 0, WAL busy 0·0byte 후에만 macOS `clonefile(2)`로 닫힌 사본을 고정한다.
- 활성 writer와 source-device I/O를 분리하기 위해 서비스가 아직 닫힌 동안 clone을 제한 chunk로 다른 device의 임시 검증 경로에 전송하고 양쪽 SHA-256을 대조한다. 일치한 뒤 source clone을 제거한다.
- 전송 완료 뒤 불변 서비스를 재기동해 동일 Run 복구를 확인한다. 다른 device 사본만 `mode=ro&immutable=1`로 전수검사하므로 활성 writer lock과 source-device read 경쟁이 없다.
- 전수검사 동안 event 전진, queue, 실행 p95, planned·unplanned reconnect, gap, resync, drop, persistence fault, buffer drop, critical incident, 포지션과 PAPER 안전경계를 별도 thread로 감시한다.
- 실패하거나 안전상한을 넘으면 검사를 중단하고 서비스를 복구한다. PASS 후 외장 clone과 별도 device 임시 사본을 모두 제거한다.
- 자세한 중단·회전·HTTP 감시 계약은 `docs/adr/ADR-049-closed-cross-device-ledger-integrity.md`를 따른다.

## 10.18 Trailing runner 감사와 복구

- PAPER portfolio recovery payload schema 5는 trailing policy, 여덟 상태, transition history,
  최근 중복방지 event ID, favorable executable bid/ask, 단조 trail과 외부 감사 cursor를 보존한다.
- `TRAILING_STATE_TRANSITION`, `TRAILING_MARK_UPDATED`, `TRAILING_EDGE_STATE_UPDATED`,
  `TRAIL_EXIT_PENDING`는 모두 상태변경 감사로 분류돼 checksum 보호 recovery snapshot을 쓴다.
- 단순 화면용 미실현손익 변화는 snapshot을 쓰지 않는다. 새로운 favorable mark 또는 실제
  보호 trail 변화만 저장해 writer 부담을 제한한다.
- 복구는 transition 연결·허용 전이·시간·식별자·결정적 transition ID뿐 아니라 strict
  boolean, adverse 사유 목록·개수·지속시각, 단조 trail과 수수료 반영 보호경계를 검증하고
  불일치 시 fail-closed한다.
- ATR·구조 reference는 연속 완성봉만 사용하고, 산출 ATR·구조 stop·마지막 완성시각·시간구간을
  `CandidatePlan`과 recovery snapshot에 고정한다. replay도 같은 고정값을 사용한다.
- 같은 저장 event와 정책을 replay했을 때 transition 경로와 최종 state checksum이 같아야 한다.
  이 검증을 실행하기 전 상태는 `NOT_RUN`이며 수익성 증거가 아니다.
- 완료 거래 payload는 `trailing_activation_ts_ms`, `peak_unrealized_usdt`,
  `giveback_usdt`, `runner_net_pnl_usdt`, `trail_trigger_slippage_usdt`와
  `trailing_state_checksum`을 메인·BASE·STRESS 계좌에 같은 의미로 저장한다.
- 전략 성과 API는 현재 전략버전의 독립 `LIVE_PUBLIC` 거래만 사용해 activation 표본,
  runner 순기여, 평균 giveback과 trailing trigger 체결차이 비용을 계산한다. 거래 상세와
  전략별 통계 화면은 이 값을 표시하되 표본이 없으면 수익성 숫자를 만들어내지 않는다.

## 10.19 고정 파라미터 walk-forward와 holdout

- 네 Validation fold에서 anchored는 모든 이전 fold, rolling은 직전 fold를
  training 창으로 사용하고 다음 fold를 평가한다.
- trial parameter는 사전등록값으로 고정하며 창별 결과로 재튜닝하지 않는다.
- symbol·venue·regime·volatility·bull/bear/range·BASE/STRESS cost를 독립
  leave-one-group-out 진단으로 남긴다. 그룹이 하나뿐이거나 신호 시점
  라벨이 누락되면 성공으로 표시하지 않는다.
- 변동성은 신호 시점 완료본의 fast/slow 실현변동성 비율로
  `LOW < 0.75`, `NORMAL <= 1.5`, `HIGH > 1.5`를 고정한다.
- 결과는 Validation 진단이며 Final OOS를 열거나 수익성·승격을 입증하지 않는다.
- 상세 경계는 `docs/adr/ADR-081-fixed-parameter-walk-forward-and-holdouts.md`를 따른다.

## 10.20 연구 spill과 LIVE 저장공간 격리

- 대용량 DuckDB 수신순 정렬은 `ROBOM_RESEARCH_SPILL_ROOT`로 지정한 충분한 별도
  볼륨에 임시파일을 작성한다. 500개 이상 archive 파일은 이 경로가 없으면
  실행하지 않는다.
- `StoragePressureError`는 누적 사고로 기록하되 현재 fault 활성상태와 분리한다.
  저장공간이 안전선을 회복하면 버퍼 flush를 자동 재개한다.
- SQLite·WAL·atomic commit 결함은 가역적 저장압력으로 재분류하지 않고 영구
  fail-closed로 유지한다.
- 세부 결정과 제외 표본 경계는 `docs/adr/ADR-119-research-spill-and-transient-storage-recovery.md`를
  따른다.

## 10.21 V6 고유기회와 작은 UI read model

- 완료 PAPER 결과는 원시 fill·BASE·STRESS 행을 삭제하거나 다시 쓰지 않는다.
- 성과 표본 key는 `(run_id, strategy_id, strategy_version, opportunity_id, symbol, side)`다. BASE·STRESS와 부분 exit는 같은 opportunity의 세부 결과다.
- 현재 strategy version 필터는 과거 version을 삭제하지 않고 기본 순위·요약에서만 제외한다.
- `/api/trades`는 opportunity 한 행 안에 profile 결과와 replay 가능성을 제공한다. 원시 비용행과 fill은 상세에서 조회한다.
- `/api/ui/summary`는 상태·자산·PnL·열린 포지션·bounded scanner만 제공한다. Family detail, 조건, 거래상세와 diagnostics는 별도 on-demand read다.
- Fixture payload benchmark는 summary 직렬화 크기가 기존 `/api/dashboard`의 50% 미만인지 측정한다. 이 수치는 LIVE 지연, 장기 안정성이나 수익성 증거가 아니다.
- 마지막 기준선 32개 raw 현재버전 행은 16개 고유기회다. 서비스 중지 뒤 동적 cache는 다시 관찰하기 전 `UNKNOWN`이며 과거 ready 값을 현재값으로 만들지 않는다.
