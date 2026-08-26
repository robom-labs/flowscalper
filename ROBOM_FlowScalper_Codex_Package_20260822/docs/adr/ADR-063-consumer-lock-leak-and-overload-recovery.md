# ADR-063. 시장 소비 작업 잠금 누수와 과부하 복구

## 상태

채택. 실제 설치 검증 전이다.

## 문제

장시간 LIVE PAPER 기준선에서 계획 재연결 뒤 공개시장 producer는 계속 전진했지만 시장 소비,
전략 평가와 저장 완료가 동시에 멈췄다. 큐는 4,096건으로 포화됐고 이벤트 누락은 계속
증가했다. 신규 진입 잠금 자체는 켜졌지만 이전 화면은 `작동 중`과 `PAPER 진입 가능`으로
표시해 실제 내부 상태와 모순됐다.

프로세스 표본에는 유휴 worker와 Python `RLock`에서 무기한 대기하는 worker가 함께 있었다.
SQLite `_Transaction.__enter__`는 process lock을 얻은 뒤 `BEGIN IMMEDIATE`를 실행했으며,
`BEGIN`이 실패하면 context manager의 `__exit__`가 호출되지 않아 lock을 해제하지 않았다.
별도 process의 FULL commit과 foreground 저장이 경합할 수 있는 구조에서 이 결함은 이후
저장 호출과 소비 작업을 영구 대기시킬 수 있다.

또한 소비 sink가 예외를 한 번 발생시키면 `_consume` task 전체가 종료됐다. producer는 계속
큐에 넣으므로 큐 포화와 누락만 증가하고 소비 task의 종료 이유나 복구 상태는 보이지 않았다.

## 결정

1. `_Transaction.__enter__`는 `BEGIN IMMEDIATE`가 어떤 예외로 끝나도 process lock을 즉시
   해제하고 원래 예외를 다시 전달한다.
2. 소비 task는 개별 sink 예외를 해당 이벤트의 명시적 소비 누락으로 집계하고 task 자체는
   유지한다.
3. 소비 예외와 queue 포화는 즉시 신규 PAPER 진입을 잠근다.
4. 소비 task가 연속 성공 이벤트를 처리하고 queue가 저수위까지 내려간 뒤에만 자동 안전잠금을
   해제한다. 필요한 연속 성공 수는 queue 크기에 따라 4~64건으로 제한한다.
5. supervisor 전체와 소비 task의 실행 여부, 성공·실패·누락·복구 건수와 시각, queue 과부하
   시작·복구·누락 건수를 시스템 진단에 노출한다.
6. 런타임은 supervisor 중단, 소비 중단, 소비 전달 실패와 queue 과부하를 서로 다른
   `ENTRY_LOCK_*` 원인으로 표시한다. producer나 consumer 어느 한 task라도 종료되면 신규
   PAPER 진입과 시장 관찰 표시를 fail-closed한다.
7. 대시보드 생성 시 supervisor 안전상태를 다시 읽는다. 저장 오류가 함께 있어도 task 중단을
   `시장 관찰 중` 문구보다 먼저 표시한다.
8. 멈춘 LIVE supervisor에서 `자동 관찰 시작`을 누르면 기존 Run을 보관하거나 새 Run을 만들지
   않고 같은 Run의 supervisor만 안전하게 교체한다. 새 Run은 별도 `새 Run` 동작에서만 만든다.

## 결과

SQLite BEGIN 경합이 실패해도 Python lock이 영구 오염되지 않는다. 일시적인 소비 예외는
producer만 살아 있고 consumer가 죽는 비대칭 상태로 확대되지 않는다. queue 과부하가 발생하면
누락 수치와 안전대기 상태가 유지되고 실제 처리 회복과 queue 배수가 확인된 뒤에만 신규 PAPER
진입이 복구된다. task가 종료된 상태는 다른 저장 안전잠금보다 먼저 사용자에게 표시되며,
재시작은 현재 Run·원장·리플레이 범위를 보존한다.

실제 기준선 사고가 해당 `BEGIN` 실패에서 시작했다는 직접 예외 문자열은 이전 서비스가 보존하지
않았으므로 인과관계는 `강한 정합성, 직접 로그 없음`으로 남긴다. 코드 결함 자체와 lock 누수는
실패 우선 회귀검사로 재현했다.

실제 주문, private API, 인증, 지갑, 전략 임계값과 PAPER 비용모델은 바꾸지 않는다.
