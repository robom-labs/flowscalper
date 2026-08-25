# ADR-037. READY 시작의 과거 Run 보존 종료

- 상태: Accepted
- 날짜: 2026-08-25
- 범위: macOS READY 서비스에서 Fresh LIVE·DEMO Run을 만드는 수명주기

## 배경

macOS LaunchAgent는 사이트를 항상 열 수 있게 `ROBOM_MODE=READY`로 서버를 부팅한다. 시작 버튼은 `start_live_run()`을 호출하지만 `_archive_current_run()`은 READY mode에서 바로 반환했다. 따라서 실제 거래·시장데이터는 Run ID로 계속 분리됐지만, 이전 Run의 `finalized_ts_ms`가 비어 있는 행이 누적됐다. 실제 활성 원장에서 새 Wave33 Run을 포함해 미종료 행 76개가 확인됐다.

이 행들을 삭제하거나 과거 거래를 다시 쓰면 안 된다. 동시에 가장 최근 복구 snapshot에 진행 중인 PAPER pending 또는 position이 있다면 새 Run을 만들어 그 노출을 고아 상태로 남겨서도 안 된다.

## 결정

1. Fresh LIVE·DEMO·사용자 새 Run·거래소 failover Run을 만들기 전에 남은 미종료 과거 Run을 한 트랜잭션에서 `preserved`와 `recovered_as_superseded` 이유로 종료한다.
2. 거래, 주문, 체결, snapshot, 시장 archive는 삭제하거나 수정하지 않는다.
3. READY에서 LIVE를 시작하기 전 최신 checksum 검증 recovery snapshot의 top-level `open_position`과 모든 계좌의 `pending_entries`·`positions`를 확인한다.
4. 복구할 PAPER 노출이 있으면 `RECOVERY_OPEN_PAPER_EXPOSURE`로 Fresh Run을 차단한다. 현재 프로세스에도 pending·position이 있으면 `OPEN_PAPER_EXPOSURE`로 차단한다.
5. 평평한 과거 Run 여러 개가 모두 종료되고 새 Run만 복구대상으로 남는 테스트와, 복구할 포지션이 있을 때 기존 Run이 미종료 상태로 보존되는 테스트를 유지한다.

## 결과와 한계

이 변경은 과거 데이터를 지우지 않고 Run 수명주기만 명확히 한다. 배포 시점에는 열린 main·League 포지션 0을 다시 확인한 뒤에만 서비스와 Fresh Run을 교체한다. 활성 원장의 76개 과거 행이 실제로 보존 종료됐는지와 새 Run 한 개만 미종료인지 배포 후 별도 확인한다.
