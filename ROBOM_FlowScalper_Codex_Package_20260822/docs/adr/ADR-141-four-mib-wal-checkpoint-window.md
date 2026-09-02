# ADR-141. 4MiB WAL 체크포인트 실행 구간

- 상태. 채택·배포·단기 실행구간 검증 완료. 장시간 검증은 미실행.
- 일자. 2026-09-03.
- 범위. LIVE_PUBLIC PAPER 활성 원장의 PASSIVE WAL 체크포인트에 적용한다.
- 수익성 영향. 없음. 전략과 PAPER 체결 계약은 바꾸지 않는다.

## 배경

ADR-140의 8MiB 기준을 적용한 release
`58bdabdd9af938882ba86e2fef5a853faa974bcd`를 기존
`run-2b7135a972dd`에 설치했다. 첫 두 checkpoint는 3.766초·1.501초였지만,
후속 checkpoint 중 하나가 41.142초로 30초 상한을 넘었다. 동시 flush,
WAL fault·busy, 저장 fault·drop, event drop은 0이었고 event·전략평가는
전진했다. 그러나 실행 영향이 작았다는 이유로 명시적 30초 gate를
PASS로 바꾸지 않는다.

같은 외장 APFS에서 저장소 검색과 증거 수집이 함께 진행됐다. 이것이
41.142초의 단일 원인인지는 확정하지 않는다. 외부 I/O가 없는 좋은
조건만 가정하지 않고 한 번의 checkpoint 작업량을 더 줄인다.

## 결정

1. 미확정 WAL checkpoint 실행 기준을 8MiB에서 4MiB로 낮춘다.
2. PASSIVE, 별도 process, 배타 `storage_io_priority_gate`, foreground I/O,
   `synchronous=FULL`, 250-event flush와 64MiB fail-closed는 유지한다.
3. 전략, 후보, 비용, 레버리지, 수량, 진입, TP1·TP2·SL, PAPER 호가
   체결과 기존 원장 행은 변경하지 않는다.
4. 4MiB release 설치 후 최소 10회의 신규 checkpoint를 관찰한다. 하나라도
   30초를 넘거나 concurrent flush가 0이 아니면 FAIL로 유지한다.

## 검증 계약

- dashboard의 `wal_checkpoint_soft_bytes` 4MiB를 확인한다.
- 신규 checkpoint 10회의 최대 시간이 30초 이내이고 current·last·max
  concurrent flush delta가 0이어야 한다.
- event·전략평가는 전진하고 queue·buffer는 fail-closed 상한 아래에서
  회복하며 persistence fault·buffer drop·event drop·실제주문·인증은 0이어야 한다.
- 6시간·24시간을 채우지 않으면 `NOT_RUN`, 수익성은 `NOT_PROVEN`, 실자금
  준비는 `NOT_READY`다.

## 실제 검증 결과

불변 release `6d63e6f63ebda3023336e5cabbabf1009c77eb9d`를 기존
`run-2b7135a972dd`에 설치하고 CAS revision 108→109 일시정지,
109→110 재개로 `RUNNING`·`ENTRY_ENABLED`를 복원했다. 2026-09-03 KST
08:27:33부터 08:37:46까지 신규 checkpoint 10회는
0.752·0.766·1.011·1.252·0.503·1.254·1.001·0.755·1.506·0.753초였고,
current·last·max concurrent flush delta는 모두 0이었다. event는 58,512건,
전략 평가는 53,940건 전진했으며 fault·drop·비계획 재연결·gap·resync는 0이었다.

후속 15회까지의 누적 최대는 17.874초로 30초 상한 안이었다. 일부 후속
체크포인트 전후 지연 보호가 자동 안전 대기를 만들었지만 시장 관찰은 계속됐고,
개입 없이 `RUNNING`·신규 PAPER 진입 허용으로 복구한 연속 안정 표본 3회를
확인했다. 이 단기 PASS를 6시간·24시간 안정성이나 수익성으로 확대 해석하지 않는다.
기계판독 근거는 `evidence/WAVE155_FOUR_MIB_WAL_CHECKPOINT_POSTINSTALL.json`이다.
