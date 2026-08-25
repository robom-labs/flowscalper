# ADR-031. 내구성 시장 저장 전체의 프로세스 격리

## 상태

승인. 2026-08-25.

## 문제

WAL checkpoint를 COMMIT 경로에서 제거한 뒤에도 실제 `run-517b78c88366`에서 `synchronous=FULL` 원장 커밋이 7.741초까지 늘었다. 해당 커밋 완료 14ms 뒤 실제 호가 임계 지연 사건이 시작됐고 최대 이벤트 수신 공백도 같은 시각대에 기록됐다. 이는 강한 시간 연관이지만 저장장치·운영체제·공개 네트워크 내부까지 포함한 단일 원인 증명으로 확대하지 않는다.

Parquet 직렬화·압축·fsync는 이미 별도 process였지만, archive manifest·종목별 통계·캔들의 FULL SQLite 커밋은 메인 Python 프로세스의 worker thread에서 실행됐다. 시장 처리와 파일 커밋의 프로세스 경계를 완전히 분리할 필요가 있다.

## 결정

1. 한 2,000-event 배치의 Parquet 작성과 SQLite 원자 커밋을 하나의 background I/O process 호출에서 순서대로 수행한다.
2. worker process는 checksum-addressed Parquet를 먼저 fsync한 뒤 활성 원장에 별도 SQLite 연결을 연다.
3. 분리 연결은 기존 WAL을 확인하고 `foreign_keys=ON`, `synchronous=FULL`, `wal_autocheckpoint=0`, 60초 busy timeout을 적용한다.
4. archive manifest·종목별 통계·캔들은 기존과 동일하게 한 `BEGIN IMMEDIATE`·`COMMIT`으로 확정한다. 중복 checksum 충돌이나 닫힌 Run은 전체 롤백한다.
5. process·Parquet·SQLite 어느 단계든 실패하면 메인 런타임은 시장·캔들 배치를 모두 복원하고 새 PAPER 진입을 fail-closed한다.
6. 기존 동기 원장 메서드는 fixture와 직접 원자성 테스트 호환을 위해 유지하지만 LIVE archive 경로에서는 사용하지 않는다.
7. 전략·비용·체결·TP·SL·위험 기준을 낮추지 않고 실제 주문, private API, API Key, 인증과 wallet 경로는 계속 0 또는 false다.

## 검증 기준

- 독립 연결이 Parquet manifest·통계·캔들을 정확히 저장하고 기존 주 연결에서 즉시 읽을 수 있어야 한다.
- process 실패 주입 때 두 메모리 배치 복원, drop 0, PAPER 신규진입 안전잠금이 유지돼야 한다.
- 전체 backend·frontend·정적·build·PAPER safety·security와 Chromium desktop·tablet·mobile 검증이 통과해야 한다.
- 실제 새 Run을 최소 이전 문제 구간과 비슷한 160,000 이벤트까지 관찰하고 저장·checkpoint·이벤트 공백·임계 지연·재연결·누락·유실·fault를 기록해야 한다.
- 짧은 PASS를 6시간·24시간 또는 전략 수익성 증거로 확대하지 않는다.

## 실제 검증 결과

2026-08-25 구현 commit `663e3857d4574aef9af9e16af3e54699c5f34984`에서 표적 저장·런타임 47개와 전체 backend 321개, frontend 47개, Chromium desktop·tablet·mobile 3개를 포함한 정적·build·PAPER 안전·security·저장소 위생 검사가 통과했다. 같은 commit의 GitHub Actions 32820190558도 validate와 browser를 통과했다.

새 실제 `run-622167a01f3c`을 165,405 events·82 flush까지 관찰했다. worker process에서 12.530초 FULL 커밋과 17.743초 PASSIVE checkpoint가 실제로 발생했지만 실행호가 처리 p95는 37.717ms, 체결 이벤트 p95는 278.101ms였고 비계획 reconnect·sequence gap·drop·저장 fault·buffer drop은 0이었다. 임계 지연 사건은 분리 전 동일 장비 표본의 4회·최장 45.896초에서 2회·최장 1.816초로 줄었고 최종 시점에는 복구돼 신규진입 잠금이 해제돼 있었다.

후속 207,283 events·103 flush와 계획 회전 2회까지 checkpoint 최대값은 22.984초로 늘었지만 시장 처리 p95 39.903ms·체결 p95 45.371ms였고 임계 지연 사건은 2회에서 더 증가하지 않았다. 최종 시점에도 임계 지연 active와 신규진입 lock은 false, 비계획 reconnect·sequence gap·drop·저장 fault·buffer drop은 0이었다.

이는 프로세스 격리 뒤의 실제 운영 표본이지 미래 지연 0을 보장하는 결과가 아니다. 활성 다중 GiB 원장의 전체 foreign-key와 quick check는 저장 관찰과 동시에 재실행하지 않았고 `NOT_RUN`으로 남긴다. 현재 Run의 166,000 archived events와 통계 건수, 83개 archive 파일 존재, 거래·체결·캔들 건수는 읽기 전용으로 대조했다.
