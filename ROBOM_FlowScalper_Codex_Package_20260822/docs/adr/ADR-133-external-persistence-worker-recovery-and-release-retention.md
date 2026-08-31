# ADR-133. 외장 저장 worker 자동복구와 불변 릴리스 보존 한도

## 상태

승인. 2026-08-31.

## 배경

LIVE 공개시장 저장과 같은 물리 One Touch 장치에서 4.9GB 닫힌 검증 원장을 전수 읽는 동안
AnyIO 저장 process가 `BrokenWorkerProcess` 초기화 오류를 반환했다. 보류된 시장 이벤트는
메모리에 복원됐지만 기존 런타임은 이 예외를 영구 원장 결함으로 분류했다. 그 결과 새 process를
만들어 재시도할 수 있는 일시 장애인데도 `PERSISTENCE_FAULT_ENTRY_LOCK`과
`PERSISTENCE_BACKLOG_ENTRY_LOCK`이 함께 유지되고 버퍼 드롭이 누적됐다.

같은 시점에 불변 릴리스 폴더도 업그레이드마다 계속 쌓여 외장 저장공간을 불필요하게 사용했다.
Git history와 GitHub main·Release가 과거 소스를 보존하므로 실행 폴더에는 현재 릴리스와 실제
롤백에 필요한 직전 릴리스만 있으면 된다.

## 결정

1. `BrokenWorkerProcess`는 이미 hard fault가 존재하지 않는 경우에만 일시적인 격리 저장 worker
   장애로 분류한다. `StoragePressureError`와 같은 자동복구 진입잠금 계약을 사용한다.
2. 실패한 batch는 삭제하거나 전진시키지 않고 원래 버퍼에 복원한다. 신규 PAPER 진입만 잠그며
   기존 공개시장 관찰과 열린 포지션 보호는 계속한다.
3. 저장공간과 런타임 안전조건이 정상인 것을 확인한 뒤 일시 잠금을 해제하고 새 AnyIO process로
   동일 batch를 재시도한다. 재시도도 실패하거나 portfolio가 이미 hard fault면 기존 영구
   fail-closed 계약을 유지한다.
4. 활성 원장이나 같은 물리 backing device의 대형 사본에 LIVE와 동시에 full `quick_check`,
   전체 SHA 또는 전수 읽기를 실행하지 않는다. 전수 무결성 검증은 ADR-049와 ADR-132의 닫힌
   cross-device 절차만 사용한다.
5. 외장 runtime의 `releases/`에는 manifest가 일치하는 현재 릴리스와
   `current-deployment.json`이 가리키는 직전 롤백 릴리스만 남긴다. 형식이 알 수 없거나 manifest가
   불일치하는 폴더는 자동 삭제하지 않고 `skipped_paths`에 남긴다.
6. 소스·원장·시장자료·Python·cache·temp·로그·불변 릴리스와 증거는 외장 APFS에만 둔다.
   GitHub main은 공유 가능한 최신 소스와 증거의 원격 기준이다. 내장 예외는 macOS가 요구하는
   작은 user LaunchAgent plist 하나뿐이며 그 실행 대상과 로그는 모두 외장 경로다.

## 검증 계약

- 첫 저장 worker 초기화 실패에서 batch가 그대로 복원되고 portfolio hard fault는 생기지 않아야 한다.
- 저장 안전조건 회복 뒤 두 번째 호출은 같은 batch를 저장하고 일시 잠금과 오류를 정리해야 한다.
- 릴리스 정리는 현재·롤백 두 폴더만 보존하고 잘 모르는 경로를 지우지 않아야 한다.
- 실제 불변 릴리스 설치 뒤 같은 Run과 process를 180초 관찰해 event·전략평가·flush가 전진하고
  저장결함·drop·backlog 잠금·비계획 재연결·gap·resync가 증가하지 않아야 한다.
- 데스크톱·태블릿·모바일의 실제 8870 화면에서 기록·상세·다시보기·전략 정렬·홈 이동과
  실제 주문 0을 확인한다.

## 결과

구현 commit `50c3e8ae7af08667546e8a1f2e4a70890e92d0f6`을 GitHub main과 외장 불변
릴리스에 설치했다. 같은 `run-2b7135a972dd`의 180.014초 관찰에서 공개시장 event
15,404건과 전략평가 81,420회가 전진했고 저장결함·버퍼 drop·backlog 진입잠금·비계획
재연결·gap·resync·dropped event 증가는 모두 0이었다. 실제 주문과 인증은 false였다.

릴리스 보존기는 current `50c3e8a`와 rollback `f12015c`만 남기고 검증된 과거 release
`a2e718a`를 정리했다. 세부 기계판독 근거는
`evidence/WAVE140_EXTERNAL_ONLY_STORAGE_RECOVERY_QA.json`에 있다.

## 한계

- 별도 `soak_live.py`로 만든 180초 결과는 실행 중 8870을 관찰한 것이 아니므로
  `FAIL_WRONG_SCOPE_PRESERVED`로 제외했다.
- 이번 180초 PASS는 저장 worker 복구와 운영 안정성 증거다. 신규 적격신호는 0건이었고
  BASE·STRESS 현재버전 표본은 각각 15건이므로 수익성은 `NOT_PROVEN`이다.
- 6시간·24시간은 실제 경과하지 않아 `NOT_RUN`이다. 활성 원장의 full `quick_check`도 이번
  Wave에서는 실행하지 않았다.
- 로그아웃·재부팅 뒤 sparsebundle 자동 연결은 사용자 macOS 승인 전까지 별도
  `LOCAL USER ACTION REQUIRED`다.
