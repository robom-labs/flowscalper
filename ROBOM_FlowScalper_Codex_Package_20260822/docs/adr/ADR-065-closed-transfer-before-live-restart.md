# ADR-065 닫힌 원장 전송 뒤 LIVE 재시작

- 상태는 승인이다.
- 날짜는 2026-08-27이다.

## 맥락

첫 실제 유지관리에서 정상 종료, WAL 0 checkpoint, APFS clone과 같은 Run 불변 릴리스 복구는
성공했다. 그러나 3,002,593,280 byte clone을 다른 device로 복사하는 동안 새 LIVE 서비스도 같은
원본 device에서 FULL SQLite commit을 수행했다. 복사는 372MB 지점에서 공개 깊이 지연 안전잠금을
유발했고 유지관리기는 `ABORTED_RUNTIME_SAFETY`로 중단했다. 같은 시간 최대 ledger flush는
32,806.024ms였다. 원본 원장과 서비스는 안전하게 유지됐고 임시 파일은 제거됐다.

## 결정

정상 종료와 APFS clone 뒤, 서비스가 아직 닫힌 동안 clone을 다른 device로 전송하고 양쪽 SHA-256을
대조한다. 전송 완료 뒤 source-side clone을 제거한 다음 정확한 불변 릴리스를 시작해 같은 Run을
복구한다. 전수 quick-check와 foreign-key 검사는 다른 device의 닫힌 immutable copy에서 수행하며,
그 구간만 새 LIVE 서비스를 엄격하게 감시한다.

전송 실패 시 `finally`가 준비된 LaunchAgent를 복구한다. quick-check 중 LIVE 안전 위반이 생기면
계속 즉시 중단한다. 실제 주문, 인증, private API와 열린 원장 quick-check는 계속 0이다.

## 결과

source device에서 clone 읽기와 활성 원장의 FULL commit이 경쟁하는 구간을 없앤다. 유지관리 중단
시간에는 cross-device 전송이 포함되지만, 검증 부하 때문에 신규 PAPER 진입이 반복 잠기는 것보다
상태와 원장의 진실성을 우선한다.
