# ADR-029. 시장 archive manifest·통계·캔들의 원자 커밋

## 상태

승인. 2026-08-25.

## 문제

외장 APFS의 활성 SQLite는 `WAL + synchronous=FULL`로 운영된다. 기존 영속화 작업자는 2,000개 공개시장 이벤트를 Parquet으로 저장한 뒤 archive manifest·종목별 이벤트 통계를 한 트랜잭션으로 확정하고, 같은 배치의 캔들을 다시 별도 트랜잭션으로 확정했다. 따라서 외장 저장장치의 비싼 FULL 커밋이 연속 두 번 발생했다.

실제 변경 전 `run-d1cbbe3d2458`의 최장 flush는 24.564초였다. Parquet 0.237초, manifest 0.621초, candle 원장 23.689초였고, 최대 이벤트 수신 공백 21.236초와 47.999초 임계 지연 사건이 거의 같은 시각에 기록됐다. 시간상 강한 연관은 확인했지만 저장장치와 운영체제 내부 I/O까지 계측한 인과 증명으로 확대하지 않는다.

## 결정

1. Parquet 파일은 기존처럼 별도 저우선순위 process에서 checksum·압축·fsync를 완료한다.
2. 한 영속화 배치의 모든 Parquet 파일이 준비된 뒤 archive manifest, 종목별 이벤트 통계와 캔들을 SQLite의 한 `BEGIN IMMEDIATE` 트랜잭션에서 확정한다.
3. `PRAGMA synchronous=FULL`, WAL, checksum, Run 격리, 불변 trigger와 저장공간 fail-closed 기준은 낮추지 않는다.
4. manifest 또는 candle의 중복 payload가 기존 checksum과 다르면 전체 SQLite 배치를 롤백한다. 일부 manifest나 일부 통계를 정상으로 남기지 않는다.
5. Parquet 성공 뒤 SQLite 커밋이 실패하면 기존처럼 시장·캔들 메모리 배치를 모두 복원하고 신규 PAPER 진입을 fail-closed한다. 재시도 파일명은 batch checksum 기반이라 같은 배치가 결정적으로 같은 경로를 사용한다.
6. 고급진단의 별도 manifest·candle 시간은 `원장 통합 커밋 ms` 하나로 교체한다. 교체된 구형 진단 키와 문구는 현재 코드에서 제거한다.
7. 전략 신호, 자연신호 임계값, bid·ask 비용, TP, SL, 위험예산과 실제 주문 0 경계는 변경하지 않는다.

## 결과

단위·통합 테스트는 archive manifest·통계·캔들이 정확히 한 번의 `BEGIN IMMEDIATE`와 한 번의 `COMMIT`을 사용함을 검증했다. 충돌 캔들을 주입하면 manifest·통계가 함께 롤백됐고, 작업자 원장 오류를 주입하면 시장·캔들 버퍼가 모두 복원되며 PAPER 신규진입이 fail-closed됐다.

실제 새 `run-2b0119b86432`의 56,260 이벤트·28 flush 표본에서 최장 flush는 1.506초였다. 해당 최장 표본은 Parquet 0.728초·통합 원장 0.770초였고 2초 이상 flush는 0회였다. 실행호가 p95 마지막 표본은 75.969ms, 최대 수신 공백은 1.231초, 임계 지연 사건·비계획 재연결·sequence gap·drop·저장 fault·버퍼 손실은 모두 0이었다. 이는 실제 짧은 서비스 표본의 개선 증거이며 6시간·24시간 무지연 또는 저장장치 전체 수명 성능 증거는 아니다.
