# ADR-101. LIVE 우선 archive 재검증과 단계 증거

## 상태

Accepted. 다만 전체 13-Run 재검증이 실제 LIVE 안전감시를 끝까지 통과하기 전까지 운영 결과는 `NOT_RUN`이다.

## 문제

동결 13-Run 전략리그는 실행 전에 현재 Parquet bytes, 이벤트 범위와 건수를 다시 검증한다. 기존 구현은 다음 두 문제를 가졌다.

1. CPU 협조 예산은 PAPER 전략 이벤트 처리에만 적용됐고, 시작 단계의 archive SHA-256 및 Parquet 집계에는 적용되지 않았다.
2. 안전감시가 자식 프로세스를 중단해도 stdout이 비어 있어 byte 검증 중인지 전략 처리 중인지 제어 증거만으로 구분할 수 없었다.

실측상 `WAVE111_ALL_TP1`은 808.772초 뒤, CPU 25% 협조 예산을 추가한 `WAVE112_ALL_TP1_CPU25`는 52.953초 뒤 모두 `EVENT_LOOP_LAG_OVER_500MS`로 안전 중단됐다. 두 번째 중단 시각은 대시보드 build 최대치 515.925ms의 완료 시각과 같았지만, 이는 상관관계이며 단독 원인 확정 증거가 아니다. 연구를 실행하지 않은 27초 표본에서는 500ms 초과 증가가 0건이고 대시보드 build 관측 최대가 21.707ms였다.

## 결정

1. archive byte 검증은 1MiB 단위로 읽는다.
2. 각 읽기 전에 활성 PAPER 원장의 I/O priority 공유 잠금을 잡고, 1MiB 읽기 직후 잠금을 해제한다. LIVE 영속화는 같은 잠금의 배타 구간을 사용하므로 원장 쓰기가 우선된다.
3. 잠금을 해제한 뒤 CPU 협조 예산과 16MiB/s 목표 읽기 예산을 적용한다. 대기 중 공유 잠금을 유지하지 않는다.
4. Run 범위·건수는 DuckDB 전체 집계 대신 Parquet row-group metadata로 검증한다. metadata 통계가 없는 파일만 필요한 열을 읽는다. 종목 집합은 기존 DuckDB hive partition 계약과 같게 `symbol=` partition에서 읽고, partition이 없는 테스트·이전 파일만 작은 `symbol` 열을 읽는다.
5. 자식 stdout에 `ARCHIVE_BYTE_VERIFICATION_STARTED`, `ARCHIVE_BYTE_VERIFICATION_COMPLETED`, `PAPER_STRATEGY_REPLAY_STARTED`, `PAPER_STRATEGY_REPLAY_COMPLETED`, `RESULT_WRITTEN` 단계를 한 줄 JSON으로 남긴다. 안전 중단 제어 증거는 stdout tail로 마지막 단계를 보존한다.
6. LIVE-safe 전체 실행은 활성 원장 경로를 찾지 못하면 시작하지 않는다. 이 경계는 실제 주문이나 private API를 추가하지 않는다.
7. 같은 연구 가설·파라미터·데이터의 안전 중단 재시도는 구현 지문이 달라졌을 때만 허용한다. 안전 중단 기록은 append-only 이력에 보존한다.

## 수용 기준

- archive 재검증 단위테스트에서 읽기 byte 수와 I/O priority guard 진입이 관찰된다.
- LIVE-safe 결과 검증은 `live_writer_io_priority_gate=true`와 정확한 목표 읽기 속도가 없으면 실패한다.
- 저장된 13-Run 재실행 동안 LIVE event가 전진하고 신규 500ms 초과 지연, unplanned reconnect, gap, resync, drop, persistence fault, buffer drop이 0이어야 한다.
- 위 전체 실행을 실제로 마치지 못하면 `PASS`가 아니라 `NOT_RUN` 또는 안전 중단 상태로 기록한다.

## 제외

- 자연신호를 늘리기 위한 전략 기준 완화.
- 실제 주문, 거래소 인증, private API, API Key, secret, wallet 경로.
- 안전 중단 표본을 수익성 또는 목표 승률 증거로 사용하는 행위.
