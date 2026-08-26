# ADR-054. PAPER 실행 생명주기 상태 전환 감사 정규화

## 상태

승인. 2026-08-27.

## 배경

활성 대형 원장에 전수 무결성 검사를 실행하지 않고 현재 Run의 `execution_audit`와 오프라인 fixture `transitions`만 read-only로 조회했다. 후보 선택·진입 대기·진입 체결·보호관리·청산 대기·청산 체결의 기존 행은 event, 시각, 전략, 계좌와 종목을 보존했지만 transition ID, 이전·신규 상태, actor, 요청·응답 revision과 되돌림 가능 여부를 같은 행에서 직접 감사할 수 없었다.

위험 거절, 사용자 진입 일시정지와 같은 진단 행까지 억지로 생명주기 전환으로 분류하면 실제 상태가 바뀐 행과 상태를 설명하는 행이 섞인다. 또한 전략별 PAPER 계좌는 동시에 서로 다른 종목을 최대 세 개까지 보유할 수 있으므로 계좌 단위 revision만으로는 독립 포지션의 전환 순서를 보존할 수 없다.

## 결정

1. 실제 PAPER 실행 생명주기를 바꾸는 신규 행만 `transition_id`, `previous_state`, `new_state`, `occurred_ts_ms`, `cause`, `cause_code`, `description_ko`, `actor`, Run·전략·계좌·종목, 요청·응답 revision과 `reversible`을 포함한다.
2. 후보 선택·League 무장, 진입 만료·거절·미체결·체결, 수동·관리·손절·익절 청산 대기, 청산 거절·미체결·체결을 각각 `ENTRY_PENDING`, `SCANNING`, `PROTECTED`, `EXIT_PENDING`, `CLOSED`로 연결한다. 부분 청산은 잔여 수량이 있으므로 `PROTECTED`로 돌아간다.
3. revision과 상태 cursor는 `account_id + symbol` 범위로 독립 관리한다. transition ID는 Run·계좌·종목·응답 revision으로 결정적으로 생성해 replay와 복구가 같은 입력에서 같은 식별자를 만든다.
4. 자동 PAPER 실행 전환은 승인된 actor 어휘 안에서 `AUTO_SAFETY`, 사용자가 누른 공동계좌 수동 종료만 `USER_UI`로 기록한다. 체결된 진입과 청산은 불변 원장 결과이므로 되돌릴 수 없음으로 기록한다.
5. recovery snapshot schema v4는 revision cursor, 현재 상태와 마지막 전환을 checksum payload에 보존한다. schema v1~v3는 복구된 pending·position의 실제 상태에서 새 cursor를 안전하게 시작하며 과거에 없던 revision을 추정하지 않는다. schema v4의 계좌·종목·revision·상태·마지막 전환이 불일치하면 fail-closed한다.
6. 새 오프라인 fixture 전환도 `NONE→OBSERVING→ARMED→ENTRY_PENDING→PROTECTED→CLOSED`의 같은 계약을 사용한다. 기존 원장 행은 재작성하지 않는다.
7. 위험 거절, 중복 종목 거절, 사용자 진입 일시정지와 같은 상태 비전환 진단 행은 기존 의미를 유지한다.
8. runtime API는 마지막 PAPER 전환을 평탄 진단으로 노출하고, 설정 화면은 초보자용 `마지막 PAPER 상태` 카드와 접히는 원본 감사값을 분리한다.
9. 전략 신호·임계값·비용·TP·SL·체결 가격·Governor·위험예산·계좌 수와 실제 주문 0 경계는 변경하지 않는다.

## 결과

- 공동계좌와 전략별 BASE·STRESS 계좌의 각 종목 생명주기를 한 행과 연속 revision으로 감사할 수 있다.
- 재시작 뒤에도 다음 전환이 저장된 cursor에서 이어지며 손상된 cursor는 조용히 복구하지 않는다.
- 초보자는 마지막 상태를 쉬운 한국어로 보고 운영자는 같은 화면의 고급진단에서 원본 전환 계약을 확인한다.
- 과거 원장 행을 수정하지 않아 기존 연구 재현성과 불변성을 유지한다.

## 검증 경계

격리 runtime과 fixture 회귀는 후보→진입→보호→청산, 계좌·종목별 revision, schema v4 복구와 손상 fail-closed를 검증했다. backend·frontend·Playwright·정적검사·build·PAPER safety·security·저장소 위생은 현재 미배포 소스에서 PASS했다. 설치 서비스는 아직 기준 commit을 실행 중이므로 실제 배포 후 신규 lifecycle 행·8870 화면·GitHub main·Actions는 `NOT_RUN`이다. 6시간·24시간 observer는 기준 commit 범위에서 계속 진행하며 수익성은 `NOT_PROVEN`이다.
