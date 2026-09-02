# ADR-139. WAL 체크포인트 전용 I/O 구간

- 상태. 채택, 설치 서비스 검증 대기.
- 일자. 2026-09-03.
- 범위. LIVE_PUBLIC PAPER 원장과 공개시장 아카이브 영속화 worker에 적용한다.
- 수익성 영향. 없음. 전략 수익성은 계속 `NOT_PROVEN`이다.

## 배경

ADR-111은 큰 WAL의 `PASSIVE` 체크포인트를 별도 process로 옮기고 그동안 시장 원장 flush를
계속 실행했다. 이벤트 루프를 직접 막지는 않았지만, 같은 외장 APFS 장치에서 체크포인트와
archive·SQLite `synchronous=FULL` 쓰기가 동시에 진행됐다.

2026-09-03 설치 서비스 관찰에서는 저장 오류와 유실이 0인 상태에서도 체크포인트가
62.766초, 48.181초, 82.687초 걸렸고 체크포인트 한 번과 겹친 persistence flush는 최대
19회였다. 같은 구간에 시장 저장 buffer, 처리 지연과 임계 지연 사고가 함께 증가했다.
따라서 별도 process라는 실행 분리만으로 외장 디스크 경합을 해결하지 못했고, 동시 쓰기가
체크포인트 전진을 굶기고 후속 flush까지 늦춘다는 구현 가설을 재현 가능한 회귀검사로
고정할 필요가 있다.

## 결정

1. 논리 미처리 WAL 16MiB 기준, 네 flush 확인 주기, 별도 process, `PASSIVE`, 64MiB
   fail-closed와 버퍼 복원 계약은 유지한다.
2. 체크포인트 child는 CPU niceness를 낮게 유지하되 macOS background I/O 제한을 해제한다.
   짧고 중요한 checkpoint I/O가 background archive 쓰기에 밀리지 않게 한다.
3. 체크포인트는 `storage_io_priority_gate(..., exclusive=True)` 안에서 실행해 replay 읽기와
   다른 저장 process가 같은 물리 저장장치를 동시에 점유하지 않게 한다.
4. 체크포인트 task가 진행되는 동안 persistence worker는 시장 event flush와 universe snapshot
   flush를 새로 시작하지 않는다. 공개시장 수신과 PAPER 포지션 관리는 계속 실행하고,
   들어온 이벤트는 기존 bounded buffer에 보존한다.
5. 체크포인트가 끝나면 250-event batch로 대기 buffer를 즉시 순차 배출한다. 기존 10,000-event
   backlog 진입 잠금과 2,000-event 복구 기준은 바꾸지 않아 저장장치가 실제로 따라오지 못하면
   신규 진입을 계속 fail-closed한다.
6. 정상 종료도 실행 중 체크포인트부터 기다린 뒤 마지막 buffer를 확정한다. 종료 flush와
   체크포인트가 다시 겹치지 않게 한다.
7. `wal_checkpoint_current_concurrent_flush_delta`, 최근값과 최대값은 계속 노출한다. 새 릴리스의
   체크포인트 구간에서는 현재값과 최근값이 0이어야 한다.
8. 전략 조건, 후보 선정, 실제 호가 PAPER 체결, 수수료, 슬리피지, 레버리지, 진입 수량,
   TP1·TP2·SL과 열린 포지션의 불변 계획은 변경하지 않는다.

## 교체한 ADR-111 결정

ADR-111 결정 5~7의 “체크포인트 중 persistence flush 계속 실행”과 background I/O 우선순위는
이 ADR로 교체한다. 작은 논리 WAL 연기, 물리 WAL과 논리 미처리 frame 분리, 불완전·과대 WAL
안전판정과 진단 계측은 그대로 유효하다. 당시 관찰과 ADR-111 문서는 과거 결정 기록으로
수정하지 않는다.

## 검증 계약

- 체크포인트를 의도적으로 대기시킨 단위검사에서 첫 flush 뒤 750개 event가 buffer에 남고,
  체크포인트와 겹친 추가 flush 수는 0이어야 한다.
- 체크포인트를 해제하면 네 번의 flush로 1,000개 event를 모두 원장에 기록하고 buffer가 0이
  되어야 한다.
- child의 checkpoint 구간은 background I/O를 명시적으로 해제해야 한다.
- 작은 WAL 연기, 논리 frame 판정, 불완전 WAL 재시도, 과대 WAL fail-closed와 원장 불변성
  회귀검사가 계속 통과해야 한다.
- 실제 설치 검증은 열린 PAPER 포지션과 pending entry가 모두 0인 자연스러운 flat 시점에만
  수행한다. 설치 뒤 실제 체크포인트 완료시간, 동시 flush 0, buffer 회복, 지연, 유실 0과
  SQLite 무결성을 다시 관찰하기 전까지 서비스 검증은 `NOT_RUN`이다.

## 한계

전용 I/O 구간은 체크포인트 동안 시장 이벤트를 메모리 buffer에 잠시 쌓는다. 체크포인트 자체가
계속 느리면 기존 backlog 안전잠금이 신규 진입을 차단하며, 이 경우 완료로 간주하지 않고 저장
구조를 다시 조사한다. 이 변경은 저장 지연 결함을 줄이기 위한 운영 수정이지 승률이나 미래
수익을 높였다는 증거가 아니다.
