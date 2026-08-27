# ADR-071. 저장 적체 기반 WAL checkpoint 연기와 PAPER 진입 안전잠금

## 상태

승인. 2026-08-27. 변경 후 실제 서비스 검증은 진행 중이다.

## 문제

불변 release `b1a89276a86a1547336960fd540c04e363541619`과 기존 Run
`run-2b7135a972dd`를 추가 시장연결 없이 6시간 관찰하던 중 2,464.693초 표본에서
시장 저장 대기가 535건에서 24,735건으로 증가했다. 같은 구간의 최대 원장 통합
커밋은 40.927초, 최대 PASSIVE WAL checkpoint는 57.324초였다. 처리 경로 p95는
최대 1,080.879ms, 체결 이벤트 p95는 최대 3,334.171ms였고 critical lag incident가
2건 추가됐다. 유실·저장 fault·비계획 reconnect·sequence gap·resync는 0이었지만
대기 버퍼와 메모리가 계속 증가해 6시간을 억지로 채우지 않고 조기 중단했다.

중단 직전 50초 표본에서는 공개 이벤트가 5,845건 증가한 반면 저장은 1,000건만
완료돼 대기 버퍼가 19,679건에서 24,524건으로 늘었다. 당시 활성 WAL은 약 1.9MiB로
기존 64MiB fail-closed 한계보다 훨씬 작았다. ADR-070의 4 flush 주기는 정상 부하에서는
유효했지만, 큰 외장 APFS 원장에서 저장 적체가 이미 발생한 상황에도 작은 WAL의
checkpoint를 기다리느라 유일한 영속화 worker가 수십 초씩 멈추는 문제가 드러났다.

## 결정

1. 1,000-event 저장 batch, `synchronous=FULL`, WAL, checksum, 단일 transaction,
   process 격리와 64MiB checkpoint 실패 fail-closed는 유지한다.
2. 정상적으로 저장 대기가 2,000건 미만이면 ADR-070처럼 4 flush마다 PASSIVE
   checkpoint를 수행한다.
3. 저장 대기가 2,000건 이상이고 WAL이 16MiB 미만이면 checkpoint를 다음 flush로
   연기해 영속화 worker가 backlog를 먼저 줄이게 한다.
4. 적체 중에도 WAL이 16MiB 이상이면 checkpoint를 실행한다. 실패하거나 미완료인
   WAL이 64MiB 이상이면 기존처럼 영구 저장 fault와 신규 PAPER 진입 fail-closed를
   적용한다.
5. 시장 저장 대기가 10,000건 이상이면 자료를 버리지 않고 신규 PAPER 진입만
   가역적으로 잠근다. 대기가 2,000건 이하로 회복된 뒤 다른 안전조건도 정상일 때만
   자동 재개한다.
6. 대기 최대값, 적체 잠금 횟수, checkpoint 연기 횟수와 판단 당시 WAL bytes를
   시스템 고급진단과 장시간 증거에 기록한다.
7. 전략 기준, 자연신호, 비용, TP1·TP2·SL, 체결, 위험예산과 실제 주문 0 경계는
   변경하지 않는다.

## 검증 기준

- 10,000건에서 진입이 잠기고 반복 표본에서는 잠금 횟수가 중복 증가하지 않아야 한다.
- 2,001건에서는 잠금이 유지되고 2,000건에서 가역 잠금 플래그가 제거돼야 한다.
- 3,000건 적체와 작은 WAL에서는 첫 checkpoint가 연기되고 backlog가 줄면 checkpoint가
  실제 완료돼야 한다.
- 기존 불완전·과대 WAL의 64MiB fail-closed 회귀가 계속 통과해야 한다.
- 전체 backend·frontend·정적·build·PAPER safety·security·실제 브라우저 검증 뒤
  불변 release를 설치한다.
- 변경 후 같은 실제 서비스에서 저장 대기가 10,000건을 넘지 않고, 처리·체결 p95,
  critical lag, event-loop lag, flush와 checkpoint가 수용 상한을 지키는지 다시 관찰한다.
- 실제 6시간·24시간을 채우기 전에는 각각 `NOT_RUN` 또는 `IN_PROGRESS`로 유지한다.

