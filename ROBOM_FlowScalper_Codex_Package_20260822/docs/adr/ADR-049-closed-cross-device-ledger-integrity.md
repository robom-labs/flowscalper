# ADR-049. 닫힌 원장의 별도 device 전수 무결성 검증

## 상태

승인. 2026-08-26.

## 배경

2.798GB 활성 SQLite writer에 `PRAGMA quick_check`를 동시 실행한 Wave 47은 437초 동안 결과를 내지 못했고 provider queue 4,096, drop 9,736과 신규진입 잠금을 만들었다. 이 검사는 무결성 PASS가 아니라 `FAIL_FOR_LIVE_CONCURRENCY`다.

SQLite [Online Backup API](https://www.sqlite.org/backup.html)는 증분 복사 단계 사이에 source lock을 풀지만, 다른 connection이 source를 쓰면 전체 backup이 처음부터 재시작될 수 있다. 실제 2.8GB 원장에서도 진행률이 반복 초기화되어 온라인 snapshot이 완료되지 않았다. Python `sqlite3.Connection.backup()`은 SQLite의 이 계약을 그대로 사용하므로 외부 writer를 멈추지 않는다고 완료를 보장할 수 없다.

같은 외장 APFS device에 닫힌 `clonefile(2)` 사본을 만든 후 전수검사하면 writer lock은 없지만 물리 I/O를 경쟁한다. 세 번의 fail-closed 시도에서 계획 회전 중간상태, 단일 HTTP 감시 timeout, 실제 실행경로 p95 736.122ms를 각각 발견했다. 첫 두 경우는 감시 계약을 교정했고, 지연 상한 초과는 의도대로 검사를 중단했다.

## 결정

1. 활성 원장에 full `quick_check`나 `foreign_key_check`를 실행하지 않는다.
2. Online Backup API 경로는 작거나 조용한 원장의 비차단 복사로만 남긴다. 기본 최대 300초와 30초 무진행 상한을 두고 부분 파일을 삭제한다.
3. macOS LaunchAgent는 `ExitTimeOut=60`을 사용한다. 포지션과 pending 진입이 0이고 LIVE·PAPER·RUNNING 안전선일 때만 `launchctl bootout`으로 저장 종료를 기다린다. 유지관리 script는 SIGKILL을 직접 요청하지 않는다.
4. process handle이 0이 된 뒤 `wal_checkpoint(TRUNCATE)`의 busy 0·WAL 0byte를 확인한다. 같은 APFS device에 fallback 없는 `clonefile(2)`를 직접 호출해 닫힌 사본을 고정한다.
5. clone 직후 LaunchAgent를 재기동하고 동일 Run·LIVE·PAPER·RUNNING·평평한 계좌·실제주문 0·인증 0을 확인한다. full check를 위해 localhost를 계속 내려두지 않는다.
6. 활성 writer와 물리 I/O를 나누기 위해 닫힌 clone을 명시적으로 다른 device의 임시 검증 경로로 제한 복사한다. 원본·검증 사본 SHA-256이 일치해야 하며, 전송이 끝나면 외장 clone을 즉시 제거한다.
7. `quick_check` 전체와 `foreign_key_check`는 `mode=ro&immutable=1`로 연 별도 device 사본에서만 실행한다. PASS·실패·안전중단 후 정책에 따라 임시 사본을 제거하고 복구용으로 유지하는 사본을 자동으로 덮어쓰지 않는다.
8. 전송·해시·전수검사 동안 LIVE를 별도 thread로 감시한다. queue 64, 실행 p95 500ms, event 정지 15초, drop·gap·resync·비계획 reconnect·persistence fault·buffer drop·critical incident 상증 0을 상한으로 둔다.
9. 계획 회전은 planned count가 reconnect count보다 한 단계 먼저 올라간 뒤 동일해지는 최대 15초만 중간 잠금을 허용한다. 단일 localhost timeout은 증거 수집에 남기되 세 번 연속 실패 전에는 전체 검증을 중단하지 않는다.
10. 실제 주문, private API, API Key, secret, wallet과 인증 경로는 계속 0이다. 이 유지관리는 전략·임계값·PAPER 손익을 변경하지 않는다.

## 실제 검증

2026-08-26 `run-2b7135a972dd`에서 2,842,066,944byte·693,864page·schema v7 원장을 사용했다.

- 서비스 종료는 6.436초, 동일 Run의 LIVE PAPER 복구는 유지관리 시작 후 16.912초에 완료됐다.
- WAL은 busy 0·0byte였고 `clonefile(2)`는 0.002초에 동일 크기 사본을 만들었다.
- 다른 device로 2,842,066,944byte를 215.789초 제한 복사했고 양쪽 SHA-256이 일치했다.
- 닫힌 검증 사본의 full `quick_check=ok`, foreign-key 위반 0은 78.467초에 완료됐다.
- 재기동 후 244회·294.3초 감시에서 event +28,348, queue 최대 22, 실행 p95 최대 189.040ms였다. 비계획 reconnect·gap·resync·drop·fault·buffer drop·critical·position·실제주문·인증은 모두 0이었다.
- 외장 clone과 내장 임시 검증 사본은 PASS 후 모두 제거됐다.

기계판독 원본은 `evidence/wave48-ledger-integrity/actual-cross-device-maintenance-integrity.json`이다. 중단된 경로도 삭제하지 않고 같은 폴더에 보존한다.

## 한계

- 유지관리 재기동 중 localhost는 약 17초 접속할 수 없었다. 컴퓨터가 꺼져 있는 동안도 localhost를 제공할 수 없다.
- 검증 device에는 사본 크기와 설정된 headroom을 더한 여유공간이 필요하다. 운영자는 실행 전 여유공간을 확인해야 하며, script는 clone·전송 전에 각 대상의 여유공간을 재검사해 부족하면 fail-closed한다.
- 이 PASS는 해당 닫힌 사본의 구조·외래키와 복사 정확성을 입증한다. 전략 수익성, 6시간·24시간 안정성, 향후 모든 원장의 영구적 무결성을 보장하지 않는다.
