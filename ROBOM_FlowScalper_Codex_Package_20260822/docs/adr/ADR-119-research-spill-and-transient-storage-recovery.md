# ADR-119. 대용량 연구 임시공간과 일시 저장압력 복구 분리

- 상태. 채택.
- 일자. 2026-08-30.
- 범위. LIVE_PUBLIC PAPER 영속화와 100후보 오프라인 선별에 적용한다.

## 배경

100후보 V2 bounded benchmark가 20시간 Train 파티션을 수신순으로 정렬하며
DuckDB spill을 LIVE 아카이브와 같은 32GB 볼륨에 작성했다. 임시 파일이 늘어나며
여유공간이 안전선 아래로 내려갔고, Parquet worker가 `StoragePressureError`를
발생시켰다. LIVE 이벤트·큐·호가 지연은 정상이었지만 기존 런타임은 이 일시
저장압력을 SQLite·WAL 무결성 오류와 같은 영구 fault로 처리했다. 누적 사고횟수를
worker 실행 조건으로 사용해 저장공간이 회복돼도 flush를 다시 시도하지 않았고,
버퍼가 계속 늘어나는 재현 가능한 운영 결함이었다.

이 구간은 재시작 전 메모리 버퍼를 원장에 모두 확정했다는 증거가 없으므로 전략 승격
표본에서 제외한다. 다만 사전에 동결한 Wave 117 cut과 워밍업은 이 사고 이전의
불변 manifest로, 파일 identity가 일치하는 경우에만 연구 입력으로 계속 사용한다.

## 결정

1. `persistence_fault_count`는 사고의 누적 감사값으로만 유지한다.
2. worker 재개 여부는 별도의 `persistence_fault_active`로 판단한다.
3. `StoragePressureError`는 신규 PAPER 진입만 일시 잠그고 메인 계좌를 영구 fault로
   바꾸지 않는다.
4. archive와 ledger 모두 다시 안전선을 넘으면 회복 횟수·시각·직전 오류를 남기고
   누적 버퍼 flush를 자동 재개한다.
5. 실제 SQLite, WAL, atomic commit, schema와 checksum 오류는 기존처럼 영구 fail-closed로
   유지하며 UI resume로 풀지 않는다.
6. 활성 저장장애 동안 market buffer 10,000건과 candle buffer 5,000건 상한을 적용하고,
   상한 초과는 drop 진단에 모두 누적한다.
7. DuckDB spill은 `ROBOM_RESEARCH_SPILL_ROOT`로 LIVE 아카이브와 다른 충분한 볼륨에
   명시적으로 바인딩한다. 500개 이상 archive 파일은 경로가 미설정되면
   시작 전에 fail-closed한다. 소규 격리 테스트만 기존 위치 fallback을 사용한다.
8. replay 안전감시는 일시 사고가 복구돼도 누적 fault 증가를 봤으면 해당 연구를
   중단한다. 자동 복구는 수익성 증거를 승계하지 않는다.

## 검증 계약

- 저장공간이 안전선 아래인 동안 fault는 활성·복구가능으로 남아야 한다.
- 안전선 회복 뒤에는 누적 fault count가 0으로 조작되지 않은 채 worker가 다시
  저장해야 한다.
- 영구 OSError·WAL 실패는 기존 테스트처럼 risk fault와 영구 진입잠금을 유지해야 한다.
- 연구 spill 경로 바인딩은 동결 archive 파일 선택과 수신순 정렬 결과를 바꾸지 않아야 한다.
- 100후보 benchmark 재실행 중 LIVE event·flush가 전진하고 queue·drop·활성 fault가
  0이어야 한다.

## 한계

이 변경은 연구와 LIVE PAPER의 자원 충돌을 줄이는 운영 수정이다. 후보 전략의 승률,
기대값이나 실거래 수익성을 입증하지 않는다. 6시간·24시간 관찰은 실제 시간을 채우기 전까지
`NOT_RUN`이다.
