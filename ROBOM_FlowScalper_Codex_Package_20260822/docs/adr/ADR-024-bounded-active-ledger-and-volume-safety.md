# ADR-024. 활성 원장 증가 제한과 원장 볼륨 안전검사

## 상태

Accepted, 2026-08-25.

## 맥락

외장 Parquet archive는 충분한 공간을 유지했지만 자동 서비스의 활성 SQLite는 내장 Application Support에 있었다. 실제 점검에서 SQLite는 2,115,887,104 bytes까지 증가했고 내장 볼륨 여유는 약 4GiB, 1.7%였다. 그런데 기존 storage guard는 Parquet root만 검사해 `storage_entry_allowed=true`를 반환했다. 문서가 약속한 원장 공간 fail-closed와 실제 코드가 불일치했다.

`dbstat` 대조에서 `candles`와 색인이 약 1.26GB, 전체 포트폴리오 `snapshots`가 약 523MB, `strategy_account_snapshots`가 약 77MB였다. `CandleBuilder`의 10개 시간구간을 모두 SQLite에 기록했고, `LEAGUE_RISK_REJECTED` 같은 상태 비변경 감사에도 전체 recovery payload와 최대 20개 전략계정을 반복 저장한 것이 주된 증가 원인이었다. 감사·거래 불변성은 유지하면서 결정적으로 다시 만들 수 있는 파생 자료와 상태 비변경 복제만 제한해야 했다.

초기 ADR-008의 내장 원장 결정은 One Touch 직접 경로 권한 거부와 APFS sparsebundle checkpoint 실패를 근거로 했다. 현재 canonical 작업공간은 직접 마운트된 APFS 볼륨이다. 같은 WAL·`synchronous=FULL` 조건에서 250,000행 synthetic 쓰기와 checkpoint를 다시 측정한 결과 내장 0.21초, 직접 외장 APFS 1.54초였고 양쪽 모두 `quick_check=ok`였다. 외장은 약 7.3배 느리므로 저장량 제한과 실제 서비스 지연 재검증을 이동 조건으로 둔다.

## 결정

1. 한 개 `ParquetEventStore`의 동일 임계값으로 archive root와 활성 `SQLiteLedger.path.parent`를 독립 검사한다. 어느 한쪽이라도 부족하면 LIVE 신규 PAPER 진입을 잠근다.
2. 진단에는 통합 최저 여유공간과 archive·ledger 각각의 bytes·ratio를 표시한다. 서로 다른 볼륨일 때 잠금 원인은 `ARCHIVE_` 또는 `LEDGER_`로 구분한다.
3. 모든 실행 감사행은 기존처럼 append-only로 저장한다. 감사 배치에 pending entry, 주문, 체결, 포지션, 보호·청산 또는 계정 위험상태 변경이 없으면 전체 recovery snapshot은 쓰지 않는다.
4. 상태 변경이 있으면 checksum으로 보호된 전체 포트폴리오 snapshot을 즉시 기록한다. 전략계정 이력은 해당 감사에 명시된 shadow account만 기록하며 main 변경을 이유로 20개 shadow 행을 복제하지 않는다.
5. `CandleBuilder`와 차트 API는 기존 10개 시간구간을 계속 제공한다. SQLite 영구 저장은 원본 성격의 1초봉과 거래 집중 replay가 우선 사용하는 180초봉으로 제한한다. 나머지는 같은 틱에서 결정적으로 생성되는 파생값이다.
6. 외장 APFS 프로젝트의 macOS 서비스는 활성 원장을 mount의 `05_RUNTIME/ROBOM_FlowScalper/active-ledger`에 둔다. 외장 mount가 아닌 설치는 Application Support를 유지하며 환경변수 override를 허용한다.
7. 기존 활성 원장은 자동 삭제하거나 조용히 축약하지 않는다. 서비스 종료 뒤 닫힌 파일을 복사해 SHA-256·`quick_check`·foreign key·핵심 row count를 대조하고, 원본을 복구 가능한 migration archive로 옮긴 뒤에만 새 경로를 사용한다.
8. 실제 주문, private API, 인증, API Key, secret과 wallet 경로는 계속 0이다.

## 검증

- archive는 정상이고 별도 ledger volume만 임계 미달인 가짜 filesystem을 주입해 `LEDGER_FREE_BYTES_BELOW_LIMIT`, entry lock과 자동 안전대기를 검사한다.
- 10개 완성 캔들을 넣어 persistence buffer에 1초와 180초만 남는지 검사한다.
- `LEAGUE_RISK_REJECTED`는 audit만 증가하고 snapshot은 증가하지 않으며, `LEAGUE_CANDIDATE_ARMED`는 snapshot 1개와 해당 계정 행 1개를 추가하는지 검사한다.
- 기존 recovery, 실제 체결, replay, fixture와 운영안전 회귀를 함께 실행한다.
- 현재 활성 원장은 서비스 종료 뒤 새 외장 경로와 migration archive 양쪽에서 checksum·SQLite 무결성을 검증하고, 재시작한 서비스에서 기존 Run 복구와 신규 공개시장 이벤트 진행을 확인한다.

## 결과와 한계

이 결정은 저장 증가와 잘못된 원장 볼륨 안전판을 수정한다. 기존 원장 전체 크기를 과거에 없었던 것으로 만들지 않으며, 원본 migration archive를 장기보존 데이터로 남긴다. 직접 외장 APFS의 쓰기 지연은 synthetic 결과만으로 LIVE 적합성을 단정하지 않고 실제 flush·실행경로 p95·fault·drop·gap을 다시 측정한다. 6시간·24시간 안정성과 전략 수익성은 별도 검증이다.
