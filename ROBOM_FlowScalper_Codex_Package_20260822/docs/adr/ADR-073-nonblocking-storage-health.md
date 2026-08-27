# ADR-073 이벤트 루프 밖 저장소 상태 점검

## 상태

Accepted.

## 배경

Wave 96의 6시간 관찰은 약 50분 뒤 처리 p95 1,083.579ms와 4,618.787ms
critical lag를 재현해 `ABORTED_OPERATOR`로 끝났다. 첫 원인은 계획 WebSocket 회전 때
`record_universe_snapshot()`의 SQLite `BEGIN IMMEDIATE`와 `synchronous=FULL` commit을
시장 이벤트 루프에서 직접 실행한 경로였다. 이 저장을 persistence worker로 옮긴 Wave 97
20분 관찰에서는 계획 회전 1회가 critical lag 없이 끝났지만, 관찰 구간 안에서 500ms 초과
event-loop 지연이 6회 발생해 `event_loop_lag_bounded`는 계속 실패했다.

코드 추적 결과 `broadcast_dashboard()`가 연결된 화면에 0.5초마다 snapshot을 만들 때
`ProcessResourceSampler.sample()`이 외장 볼륨의 `shutil.disk_usage()`를 동기 호출했다.
`_refresh_storage_safety()`도 같은 이벤트 루프에서 archive와 활성 ledger 볼륨의
`health()`를 매초 동기 호출했다. 외장 저장소의 `statvfs` 응답이 늦으면 시장 소비, 전략 평가,
WebSocket 응답과 watchdog이 함께 멈추는 구조였다.

## 결정

1. `ProcessResourceSampler.sample()`은 CPU·메모리와 이미 측정된 디스크 값만 읽는다.
   `shutil.disk_usage()`는 명시적인 `refresh_storage_usage()`에서만 실행한다.
2. runtime은 디스크 사용량, archive 안전상태와 ledger 안전상태를 1초마다 하나의
   `storage-health-worker`에서 `to_thread`로 갱신한다.
3. dashboard, replay 안전표본과 시장 이벤트의 정상 경로는 캐시된 안전상태만 읽고
   파일시스템을 호출하지 않는다.
4. LIVE supervisor 시작은 최초 저장소 상태 점검을 비동기로 완료한 뒤 신규 PAPER 진입을
   허용한다. 복구 호가 이벤트는 동기 강제점검을 하지 않는다.
5. 사용자 pause·resume의 원장 기록과 명시적 강제점검도 HTTP 이벤트 루프 밖의 worker
   thread에서 처리한다.
6. 마지막 정상 갱신이 5초를 넘으면 `ENTRY_LOCK_STORAGE_HEALTH_STALE`과
   `STORAGE_HEALTH_STALE` 사유로 신규 PAPER 진입만 fail-close한다. 공개시장 관찰과 기존
   포지션 보호·청산은 유지한다.
7. 갱신 횟수, 최근·최대 소요시간과 완료시각을 고급진단 계약에 포함한다.
8. 전략 임계값, 신호, TP1·TP2·SL, 체결, 비용, 위험예산, Governor와 11전략·22계좌는
   변경하지 않는다.

## 결과

- 연결 화면 수나 dashboard 갱신 주기와 외장 볼륨 상태 조회 횟수가 더 이상 비례하지 않는다.
- 저장소 상태 조회가 느려져도 시장 이벤트 루프는 계속 전진한다.
- worker가 멈추거나 저장소 응답이 장기간 끝나지 않으면 오래된 정상값으로 신규 진입하지
  않고 자동 안전대기로 전환한다.
- 실제 장시간 안정성은 수정 릴리스의 별도 6시간·24시간 관찰이 실제 종료되기 전까지
  `NOT_RUN`으로 유지한다.
