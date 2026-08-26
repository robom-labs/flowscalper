# ADR-053. 시작 복구 상태 전환 감사 정규화

## 상태

승인. 2026-08-27.

## 배경

활성 대형 원장에 전수 무결성 검사를 실행하지 않고 `PAPER_RESTART_RECOVERY` incident 45행만 read-only로 조회했다. 45행 전부가 `lifecycle_state`, `recovery_ok`, `open_position`은 보존했지만 transition ID, 이전·신규 상태, 발생시각, 원인 코드, actor, 요청·응답 revision과 되돌림 가능 여부는 동일 행에 없었다.

checksum 오류로 복구가 fail-closed될 때는 시작 복구 incident 자체가 생성되지 않아 운영자가 원인을 단일 전환행으로 감사할 수 없었다. 또한 DEMO fixture 복구도 LIVE 공개호가 재검증이 필요한 복구와 같은 문구로 표시돼 현재 시장 연결 상태를 오해할 수 있었다.

## 결정

1. 신규 시작 복구 incident는 `transition_id`, `previous_state`, `new_state`, `occurred_ts_ms`, `cause`, `cause_code`, `description_ko`, `actor`, Run·전략·계좌·종목, 요청·응답 revision과 `reversible`을 포함한다.
2. LIVE 복구 성공은 `RECOVERY_REVALIDATION_LOCKED`, 준비 모드에서 미종료 Run을 발견한 경우는 `RECOVERY_DEFERRED`, checksum·schema·restore 실패는 `RECOVERY_FAIL_CLOSED`, DEMO fixture 복구는 `FIXTURE_STATE_RECOVERED`로 구분한다.
3. checksum이 틀린 snapshot payload는 신뢰하지 않는다. 단, 원장의 최신 미종료 Run 식별자만 독립 read-only 조회해 fail-closed incident와 연결한다.
4. 기존 `lifecycle_state`, `recovery_ok`, `open_position`과 원본 payload는 호환성을 위해 보존한다. 과거 행을 재작성하거나 storage schema를 migration하지 않는다.
5. runtime API는 마지막 시작 복구의 상태·원인·시각·Run을 평탄한 진단 필드로 노출한다. 설정 화면은 초보자용 요약과 접히는 고급진단의 원본 감사값을 분리한다.
6. 전략 신호·임계값·비용·TP·SL·체결·Governor·위험예산·계좌와 PAPER 안전경계는 변경하지 않는다.

## 결과

- 정상 복구, 지연, 실패와 fixture 복구를 서로 다른 상태·원인으로 감사할 수 있다.
- checksum 실패도 묵음으로 지나가지 않고 ERROR 전환행으로 남는다.
- DEMO fixture와 LIVE 공개시장 재검증 복구를 UI에서 구분한다.
- 과거 원장과 스키마를 변경하지 않아 재현성을 유지한다.

## 검증 경계

격리 runtime 테스트는 LIVE 성공·READY 지연·checksum 실패·fixture 복구의 정규 계약을 검증했다. backend·frontend·Playwright·정적검사·build·PAPER safety·security·저장소 위생은 현재 미배포 소스에서 PASS했다. 설치 서비스는 아직 기준 commit을 실행 중이므로 실제 배포 후 신규 복구 행·8870 화면·GitHub main·Actions는 `NOT_RUN`이다.
