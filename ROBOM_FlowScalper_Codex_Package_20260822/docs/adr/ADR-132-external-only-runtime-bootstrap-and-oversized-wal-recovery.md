# ADR-132. 외장 전용 런타임 bootstrap과 비정상 대형 WAL 복구

## 상태

승인. 2026-08-31.

## 배경

정식 소스·원장·시장자료가 외장 APFS sparsebundle에 있었지만 macOS 설치기는 Python
실행환경, bytecode cache, stage 결과와 서비스 로그를 내장
`~/Library/Application Support/ROBOM FlowScalper`에 두었다. 개발용
`frontend/node_modules`와 `.tool-cache`도 내장
`~/Library/Caches/ROBOM_FlowScalper`를 가리켰다. 프로그램 관련 내장 사용량은 약
3.6GB였고 내장 Data 볼륨 여유는 약 6.9GiB였다.

외장 sparsebundle의 논리 크기도 34.1GB에 불과해 26.7GB를 사용한 상태였다. 자동 서비스가
재시작될 때 활성 SQLite 5.207GB와 WAL 2.354GB의 WAL index를 다시 읽느라 서버 port와
공개시장 연결 전에 장시간 멈췄다. WAL은 기존 64MiB 미확정 상한보다 훨씬 컸다.

SQLite의 공식 WAL 문서는 장시간 reader가 checkpoint 완료를 막을 수 있음을 설명하고,
`SQLITE_CHECKPOINT_TRUNCATE`는 성공한 WAL을 0byte로 줄이는 계약을 제공한다.
Apple의 launchd 문서는 사용자 Agent plist를 사용자 Library에 두고 `KeepAlive`로 다시
실행하는 방식을 정의한다. 따라서 macOS가 요구하는 작은 plist 외의 프로그램 상태를 내장에
둘 이유가 없다.

## 결정

1. 소스, 불변 릴리스, Python base·venv, bytecode·uv·XDG cache, temp, 서비스 로그,
   stage 결과, 원장과 시장자료는 모두 외장 APFS 작업공간에 둔다.
2. 내장에는 macOS가 요구하는 `~/Library/LaunchAgents/kr.robom.flowscalper.plist`만 둔다.
   plist의 표준 출력·오류는 외장 APFS 로그이며 10MiB 단위로 제한 회전한다.
3. macOS LaunchAgent의 백그라운드 셸은 `One Touch` 일반 폴더 쓰기와 disk image 직접
   attach를 privacy 제약으로 거부하지만, 이미 마운트된 외장 APFS에는 접근할 수 있음을 실제
   최소 실행으로 확인했다. 따라서 LaunchAgent는 마운트된 외장 APFS의 `current` 불변
   릴리스 runner를 직접 실행한다. 외장 bootstrap 스크립트와 내장 runtime fallback은 두지
   않는다. sparsebundle의 재로그인 자동 연결은 사용자가 macOS 로그인 항목 또는 privacy
   권한을 승인하기 전까지 별도 로컬 승인 항목으로 남긴다.
4. 설치기는 외장 프로젝트·외장 runtime·외장 sparsebundle이 모두 확인되지 않으면
   fail-closed한다. 내장 Application Support fallback은 제거한다.
5. 서비스 시작 전 WAL이 64MiB를 넘으면 server나 모드 선택보다 먼저 handle 0을 확인한다.
   DB·WAL·SHM을 같은 APFS device에 `clonefile(2)`로 보존한 뒤 닫힌 원장에만
   `wal_checkpoint(TRUNCATE)`를 실행한다. open writer, clone 실패 또는 WAL 0byte 실패는
   서비스 시작을 거부하고 복구본과 기계판독 결과를 남긴다.
6. 정상 범위 WAL과 새 원장은 변경하지 않는다. 대형 WAL 복구는 거래·전략·비용·Run을
   수정하지 않으며 실제 주문·인증·private API를 만들지 않는다.
7. sparsebundle은 256GiB 가변 이미지로 확장한다. 논리 상한이므로 실제 One Touch 공간은
   작성된 band만 사용한다.
8. 원장과 공개시장 원본은 용량 정리를 이유로 삭제하지 않는다. 재생성 가능한 cache와 완전히
   병합된 구작업 폴더만 별도 확인 뒤 제거한다.

## 검증 계약

- 시작 전 대형 WAL은 open writer에서 거부되고 clone이 checkpoint보다 먼저 존재해야 한다.
- checkpoint 뒤 WAL은 busy 0·0byte여야 한다.
- 실제 대형 원장은 닫힌 source를 checkpoint한 뒤 다른 물리 device에 SHA-256 일치 사본을
  만들고 그 immutable 사본에서만 `quick_check`와 외래키 검사를 실행한다.
- 설치·runner·plist 정적 회귀는 Application Support와 ROBOM 내부 cache 경로가
  다시 생기는 것을 막는다.
- 실제 서비스 복구 뒤 동일 Run, 공개시장 event 전진, PAPER, 실제 주문 false, 인증 false,
  queue·지연·저장·재연결 안전선을 다시 관찰한다.

## 근거

- [SQLite Write-Ahead Logging](https://www.sqlite.org/wal.html)
- [SQLite Checkpoint API](https://www.sqlite.org/c3ref/wal_checkpoint_v2.html)
- [Apple Creating Launch Daemons and Agents](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)

## 한계

- 외장 물리디스크가 분리돼 있거나 sparsebundle 연결이 완료되지 않으면
  localhost는 열릴 수 없다. `KeepAlive`는 마운트가 완료된 뒤 재시도를 제공할 뿐 컴퓨터가
  꺼진 동안 사이트를 제공하지 않는다.
- 현재 세션에서는 LaunchAgent가 마운트된 APFS runner를 실제 시작하는 것까지 검증했다.
  로그아웃·재부팅 뒤 자동 sparsebundle 연결은 macOS 사용자 승인이 없어 `NOT_RUN`이다.
- 이 복구는 저장 무결성과 시작 정지를 다룬다. 전략 수익성, 6시간·24시간 지속성과 실자금
  준비를 증명하지 않는다.
