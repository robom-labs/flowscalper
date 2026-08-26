# ADR-067 계획 회전 안전대기의 유지관리 유예

- 상태는 승인이다.
- 날짜는 2026-08-27이다.

## 맥락

무간섭 상태에서 3,023,081,472 byte 닫힌 원장을 다른 device로 전송하고 같은 Run을 새 불변
릴리스로 복구한 뒤 전수검사를 실행했다. LIVE는 68,229 events를 전진했고 최대 queue 22,
처리 p95 232.333ms, 비계획 reconnect·gap·resync·drop·저장결함·critical lag·포지션·실주문·
인증이 모두 0이었다.

검사 시작 약 15분 뒤 정기 계획 회전이 시작됐다. 런타임은 신규 PAPER 진입을 fail-closed하고
초보자 상태를 `SAFETY_WAITING`으로 표시했으며 planned rotation은 1 증가했다. 감시기는 같은
전환의 `ENTRY_LOCKED`와 reconnect 차이는 15초 동안 허용했지만 `OPERATION_NOT_RUNNING`은
즉시 실패로 남겨 전수검사를 중단했다. 이는 정상 계획 회전을 서로 다른 두 규칙으로 모순되게
판정한 결함이다.

## 결정

계획 회전이 실제로 새로 시작됐고 planned/reconnect count가 허용 관계이며 유예시간 안인 경우,
`operation_state == SAFETY_WAITING`과 `entry_locked == true`의 조합만 정상 전환으로 허용한다.
기존 planned rotation 유예와 같은 15초를 사용하며 별도 임계값을 늘리지 않는다.

`MANUALLY_PAUSED`, `READY`, `SAFETY_BLOCKED` 또는 entry lock이 없는 비RUNNING 상태는 계속
`OPERATION_NOT_RUNNING`이다. 유예 뒤 남은 lock, queue·lag·critical incident, 비계획 reconnect,
gap, resync, drop, 저장결함, Run·process 변경과 실제 주문·인증도 계속 즉시 실패한다.

## 결과

정상 planned rotation의 짧은 fail-closed warmup은 유지관리 전수검사를 거짓 중단시키지 않는다.
수동 정지와 실제 안전장애를 계획 회전으로 숨길 수 없다. 전략, 비용, TP, SL, PAPER 체결,
Governor, 위험예산, 계좌와 실제 주문 0 경계는 바뀌지 않는다.
