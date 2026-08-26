# ADR-061. 원장 유지관리와 불변 릴리스 전환의 단일 재기동

## 상태

승인. 2026-08-27.

## 배경

기준 6시간 observer가 읽는 설치 서비스는 PID 40454의 기준 commit을 메모리에 유지하고
있지만, 로드된 LaunchAgent plist는 개발 worktree의 `scripts/run_macos_service.sh`를
가리킨다. 이후 ADR-056 구현으로 그 스크립트는 `release-manifest.json`이 있는 물리 불변
릴리스에서만 실행하도록 fail-closed됐다. 개발 worktree에는 manifest가 없으므로 기준
서비스를 먼저 정지한 뒤 기존 plist로 재기동하면 exit 75가 되고 localhost 복구가
실패한다.

기존 설치기는 릴리스 stage·`current` 활성화·plist 작성 직후 곧바로 기존 LaunchAgent를
bootout한다. 원장 유지관리기는 별도로 서비스 종료, WAL 0, APFS clone, 새 서비스 복구를
수행한다. 두 절차를 연달아 실행하면 불필요한 재시작이 두 번 생기고, 첫 재시작과 닫힌
원장 clone 사이에 새 writer가 다시 열린다.

## 결정

1. 설치기에 `--prepare-only`를 추가한다. 이 옵션은 clean commit을 불변 릴리스로 stage하고
   `current`를 원자 활성화하며 새 LaunchAgent plist를 기록하지만, 로드된 서비스를
   bootout·bootstrap·kickstart하지 않는다.
2. 알 수 없는 인자는 exit 2로 거부한다. 기본 인자 없음은 기존 설치 동작을 유지한다.
3. 기준 observer가 끝나고 모든 main·League PAPER pending·position이 0일 때만 준비 경로를
   실제 실행한다.
4. 준비된 plist를 대상으로 닫힌 원장 유지관리기를 실행한다. 유지관리기가 기존 로드 job을
   정상 bootout하고 WAL 0·APFS clone을 만든 다음, 같은 service label의 준비된 plist를
   bootstrap해 새 불변 릴리스를 처음 실행한다.
5. 첫 새 프로세스는 같은 Run을 복구해야 한다. release commit·물리 backend root·frontend
   hash·LIVE·PAPER·RUNNING·평탄 계좌·실제주문·인증 0을 확인하기 전에는 배포 완료로
   기록하지 않는다.
6. 다른 device의 immutable 사본에서 full `quick_check`와 외래키 검사를 수행하는 동안 새
   LIVE 서비스를 계속 감시한다. 검증 device는 별도 APFS physical store인
   `ROBOM4AppsWorkspace`를 우선한다.
7. prepare-only의 `current` 전환과 실제 프로세스 전환 사이에는 짧은 준비 상태가 존재한다.
   이때 설치 대시보드는 여전히 기존 commit을 실행하므로 이를 새 릴리스 실행 증거로
   해석하지 않는다.
8. 전략·비용·TP·SL·체결·Governor·위험예산·원장 정밀도는 변경하지 않는다. 실제 주문,
   private API, API Key, secret, wallet과 runtime AI 주문판단은 계속 0이다.

## 검증 경계

수정 전 표적은 `--prepare-only` 계약 부재로 1 failed·7 passed였다. 구현 뒤 macOS service
contract 8건, 전체 backend 451건, Ruff·mypy 96 source·security 131 source·repository hygiene와
`zsh -n`이 PASS했다. 실제 prepare-only 실행, 기준 서비스 bootout, 닫힌
clone, 새 불변 release의 same-Run 복구와 전수 무결성 검사는 기준 6시간 observer가 끝날
때까지 `NOT_RUN`이다. 이 ADR은 재기동 순서를 고정할 뿐 배포·6시간·24시간·수익성을
입증하지 않는다.
