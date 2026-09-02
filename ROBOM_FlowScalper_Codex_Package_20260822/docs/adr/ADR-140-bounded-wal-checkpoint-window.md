# ADR-140. 8MiB WAL 체크포인트 실행 구간

- 상태. 채택 후 후속 checkpoint 30초 초과로 ADR-141에서 기준 교체.
- 일자. 2026-09-03.
- 범위. LIVE_PUBLIC PAPER 활성 원장의 PASSIVE WAL 체크포인트에 적용한다.
- 수익성 영향. 없음. 전략과 PAPER 체결 계약은 바꾸지 않는다.

## 배경

ADR-139의 배타 I/O 구간을 적용한 불변 release
`917c624269cf74f8341c7e7ee99c4e4d29ac0823`을 같은
`run-2b7135a972dd`에 설치했다. 실제 체크포인트와 겹친 시장 원장 flush는
두 번 모두 0이었고 event와 전략평가도 전진했지만, 16MiB 미확정 WAL을
한 번에 처리한 첫 두 구간은 36.908초와 35.902초가 걸렸다. 이는 서비스 관찰의
30초 상한을 넘는다. 저장 fault·drop이 0이고 후속 체크포인트가 9.779초와
18.069초로 회복했다는 사실로 초기 실패를 지우지 않는다.

## 결정

1. 미확정 WAL 체크포인트 실행 기준을 16MiB에서 8MiB로 낮춰 한 번의 외장
   APFS 랜덤 쓰기 구간과 그 동안 쌓이는 bounded buffer를 줄인다.
2. 네 flush 확인 주기, PASSIVE, 별도 process, 배타 `storage_io_priority_gate`,
   `synchronous=FULL`, 250-event 배치와 64MiB fail-closed는 유지한다.
3. 서비스 관찰기는 runtime이 노출한 `wal_checkpoint_soft_bytes`를 사용해 작은 WAL
   연기를 판정한다. 16MiB 상수를 별도로 중복하지 않는다.
4. 30초를 넘는 신규 체크포인트나 checkpoint 중 동시 flush가 한 번이라도 관찰되면
   soak는 FAIL이다. 이전의 “느리지만 동시 flush이면 허용” 예외를 제거한다.
5. 전략, 후보 선정, 비용, 레버리지, 수량, 진입, TP1·TP2·SL, PAPER 호가
   체결과 기존 원장 행은 변경하지 않는다.

## 검증 계약

- dashboard의 soft threshold가 8MiB이고, 그보다 작은 논리 WAL은 불필요한 checkpoint를
  시작하지 않아야 한다.
- 실제 설치 후 적어도 두 번의 신규 checkpoint가 각각 30초 이내에 완료되고,
  current·last·max concurrent flush delta가 0이어야 한다.
- event·전략평가는 전진하고 queue·buffer는 상한 아래에서 회복하며, persistence
  fault·buffer drop·event drop·실제주문·인증은 0이어야 한다.
- 수정 후 6시간·24시간을 실제로 채우기 전에는 `NOT_RUN`, 수익성은 `NOT_PROVEN`,
  실자금 준비는 `NOT_READY`다.

## 교체 범위

ADR-139 결정 1의 16MiB soft threshold만 8MiB로 교체한다. ADR-139의 배타
I/O 구간과 나머지 안전 계약은 그대로 유효하다.

## 배포 검증과 후속 실패

불변 release `58bdabdd9af938882ba86e2fef5a853faa974bcd`를 기존
`run-2b7135a972dd`에 설치하고 `RUNNING`·`ENTRY_ENABLED`를 복원했다.
8MiB 기준의 첫 두 checkpoint는 3.766초·1.501초로 30초 이내였고,
concurrent flush delta는 0이었다. 그러나 후속 checkpoint 중 하나가
41.142초로 30초 gate를 넘었다. event·전략 평가는 전진했고 저장 fault·
buffer drop·event drop은 0이었지만 Wave 154 실행구간을 PASS로 판정하지
않는다. 기계판독 근거는 `evidence/WAVE154_BOUNDED_WAL_CHECKPOINT_POSTINSTALL.json`에
보존하고 4MiB 후속 결정은 ADR-141로 옮긴다.
