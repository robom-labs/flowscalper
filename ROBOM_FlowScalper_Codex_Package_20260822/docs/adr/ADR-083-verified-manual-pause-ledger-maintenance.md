# ADR-083. 검증된 수동 진입 일시정지와 원장 유지관리 중단 경계

## 상태

2026-08-28 승인.

## 문제

증가 중인 대형 SQLite 원장의 online backup은 계속되는 쓰기 때문에 31초와 61초
무진행 상한에서 각각 중단됐다. 닫힌 APFS clone을 다른 device로 전송한 후
사이트를 먼저 복구해 전수검사하는 기존 경로는 자연 PAPER 포지션 2건이 열려
`POSITION_OPENED`로 안전 중단됐다.

사용자가 새 진입만 일시정지하면 시장 관찰과 청산은 계속하면서 전수검사 중
신규 포지션을 0으로 유지할 수 있다. 그러나 기존 `RuntimeSafetyMonitor`는
`MANUALLY_PAUSED`와 `ENTRY_LOCKED`를 무조건 위반으로 분류했다.

## 결정

`verify_macos_ledger_maintenance.py` 에 `--require-manual-pause`를 추가한다. 이 옵션은
다음 모든 조건을 재기동 전후와 검사 샘플링마다 다시 확인할 때만 적용한다.

- `paper_entry_intent.state == USER_PAUSED`.
- `manual_pause_requested == true`.
- 운영 상태가 `MANUALLY_PAUSED` 또는 계획 회전 중의 `SAFETY_WAITING`.
- 시장 관찰은 활성이고 PAPER 진입은 비활성.

위 계약이 검증된 샘플에서만 `OPERATION_NOT_RUNNING`과 `ENTRY_LOCKED` 두 코드를
허용한다. Run 변경, 포지션, 실제 주문, 인증, 저장 잠금, 지연, queue,
비계획 재연결, gap, resync, drop, persistence fault, critical lag 사건은 기존처럼
하나도 허용하지 않는다.

## 실제 결과

검증된 수동 일시정지 재시도는 3,706,220,544 byte를 다른 device로 206.206초에
전송했고 원본과 검증본 SHA-256
`ea91529d988862ff6a6007c46d9b5bea594d50769d87464c046827801955465c`가 일치했다.
같은 Run은 243.307초 뒤 `MANUALLY_PAUSED`로 복구됐고 검사 중 event는 142,428건
전진했으며 포지션은 0건이었다.

그러나 외장 검증 장치의 전수검사가 약 30분 지속되는 동안
`critical_lag_incident_count` 가 0에서 1로 증가했다. 감시기가
`CRITICAL_LAG_INCIDENT`로 중단했고 `quick_check`와 foreign-key 결과는 생성되지
않았다. 따라서 이번 전수 무결성은 `PASS`가 아니라
`ABORTED_RUNTIME_SAFETY / NOT_RUN` 이다.

## 후속 경계

같은 환경에서 상한을 느슨하거나 네 번째 재시도를 하지 않는다. 다음 전수검사는
더 빠른 별도 검증 장치를 사용하거나, localhost를 검사 전체 동안 내릴 수 있는
명시적 유지관리 시간에 닫힌 clone을 검사한다. 그때도 active ledger에 직접
`quick_check`를 실행하지 않는다. 이 제약은 수익성과 무관하며 전략 기준을
낮추거나 실제 주문 경로를 여는 근거로 사용하지 않는다.
