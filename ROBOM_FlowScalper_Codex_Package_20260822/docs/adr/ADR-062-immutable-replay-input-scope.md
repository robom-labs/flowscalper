# ADR-062. 증가 중인 Run의 리플레이 입력 범위 고정

## 상태

채택. 실제 설치 검증 전이다.

## 문제

LIVE PAPER Run은 replay가 실행되는 동안에도 공개시장 이벤트를 계속 append한다. 기존 replay
요청은 Run과 종목만 지정했고 worker는 실행 시점에 보이는 전체 이벤트를 읽었다. 따라서 같은
Run과 종목을 다시 선택해도 입력 건수가 달라져 결정적 재현의 전제가 깨졌다.

실제 취소 작업은 ONGUSDT 485,283건으로 시작했지만 같은 Run의 이후 미리보기는 494,535건이었다.
취소 작업에는 완료 checksum이 없으므로 새 실행이 과거 결과와 일치한다고 소급 주장할 수도 없다.

기존 결과 checksum은 원본 이벤트와 전략 version·config·decision path·final state를 함께 묶은
종단간 값이다. 이를 입력 checksum이라고 부르면 원본 일치와 실행결과 일치를 구분할 수 없다.

## 결정

1. UI는 정밀 timeline이 보고한 이벤트 건수를 replay 요청의 `event_limit`으로 보낸다.
2. 서버는 요청 건수가 현재 저장 건수보다 크면 409로 거부한다.
3. 요청이 없더라도 확인 가능한 현재 저장 건수를 operation 시작 시 고정한다.
4. in-process와 격리 process는 같은 `event_limit`을 `list_market_events(limit=...)`에 전달한다.
5. 실제 읽은 이벤트 수가 요청 수와 다르면 결과를 만들지 않는다.
6. operation의 `total_events`는 예상치가 아니라 고정 입력 수를 뜻한다.
7. ReplayEngine은 정규화된 이벤트 stream만 묶은 `input_checksum`을 별도로 반환한다.
8. 기존 `checksum`은 전략 version·config·decision path·final state까지 묶는 종단간 값으로 유지한다.
9. 과거 저장 결과에 `input_checksum`이 없으면 UI는 없다고 표시한다.

## 결과

열린 Run이 계속 자라도 한 작업의 입력 건수는 변하지 않는다. 같은 고정 범위를 다시 실행해
입력 checksum을 먼저 비교하고, 그 다음 종단간 checksum을 비교할 수 있다.

이 결정은 완료 전에 취소된 485,283건 작업의 미생성 checksum을 복원하지 않는다. 새 485,283건
실행은 첫 485,283개 정렬 이벤트로 자기 범위를 고정하고 그 입력 checksum을 새로 증명한다.

전략, 비용, 체결, 위험, 계좌와 PAPER 안전 경계는 바뀌지 않는다.
