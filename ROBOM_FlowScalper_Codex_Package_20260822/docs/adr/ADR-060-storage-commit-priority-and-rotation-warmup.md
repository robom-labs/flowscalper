# ADR-060. 저장 원자 커밋 우선순위와 종목별 회전 warmup

## 상태

승인. 2026-08-27.

## 배경

Wave 58의 대형 replay를 취소한 뒤 replay worker 없이 같은 설치 service·Run을 30분 더
관찰했다. event는 134,570건, 전략평가는 476,160회 전진했고 queue 최대 1, 신규 비계획
reconnect·gap·resync·drop·persistence fault·buffer drop·포지션·실제주문·인증은 0이었다.
그러나 저장 flush 한 건은 22.636초가 걸려 20초 상한을 넘었고, planned rotation 구간에는
8.027초 critical lag incident가 한 건 추가됐다. 따라서 replay가 두 현상의 단독 원인이라는
가설은 기각했다.

현재 설치 Run의 누적 최장 flush 진단은 archive 588.476ms, SQLite ledger 66,179.757ms였다.
30분 구간의 22.636초 flush와 누적 최장값이 같은 세부 구간이라는 직접 표본은 없지만,
코드에서는 archive worker에 건 `taskpolicy -b`가 Parquet 압축뿐 아니라 `synchronous=FULL`
SQLite 원자 커밋 전체에도 계속 적용됐다. 큰 원장의 짧은 write lock과 fsync까지 background
우선순위로 제한하는 구조는 장시간 ledger 지연의 가장 강한 현재 설명이다. 배포 후 독립
관찰 전에는 인과가 입증됐다고 하지 않는다.

Binance 회전 warmup은 또 하나의 전역 boolean이었다. 첫 정밀 종목 한 개의 fresh depth가
도착하면 나머지 11개 종목도 warmup이 끝난 것으로 처리했다. 다른 종목의 socket backlog가
1,500ms를 넘으면 실행호가 경로에 들어가 transient critical incident를 만들 수 있었다.

Wave 58 구현은 `runtime.py`가 `backend.app.replay.safety`를 module import하면서 replay package
초기화가 다시 `runtime.py`를 읽는 순환 import도 만들었다. 전체 suite의 기존 import 순서는
이를 가렸지만 두 표적 파일을 단독 수집하면 즉시 실패했다.

## 결정

1. archive 직렬화·압축·파일 fsync는 기존처럼 Darwin background 우선순위를 유지한다.
2. archive 준비가 끝난 뒤 SQLite 연결·`BEGIN IMMEDIATE`·manifest·candle 삽입·FULL COMMIT
   구간만 `taskpolicy -B`로 background에서 꺼낸다. commit 성공·실패와 관계없이 `finally`에서
   `taskpolicy -b`를 다시 적용한다.
3. `synchronous=FULL`, WAL, checksum, 단일 transaction, rollback과 버퍼 복원은 바꾸지 않는다.
   비용을 낮추거나 비동기 성공처럼 보이게 하지 않는다.
4. planned rotation마다 현재 deep symbol 전체를 warmup 집합으로 시작한다. 각 종목의 book에는
   stale delta를 sequence 용도로 계속 적용하되, 모든 종목에서 1,500ms 이하 fresh depth를 한
   번씩 확인할 때까지 실행호가 출력과 신규 PAPER 진입잠금을 유지한다.
5. warmup을 완료하지 못하면 연결을 정상으로 가장하지 않는다. 후속 rotation 또는 안전중단이
   처리하게 하고 자연신호를 만들기 위해 기준을 낮추지 않는다.
6. runtime의 replay 안전 snapshot type은 TYPE_CHECKING과 함수 내부 import로 바꿔 단독 import
   순서에서도 순환 의존성을 제거한다.
7. 전략 임계값·비용·TP·SL·체결·Governor·위험예산·원장 정밀도는 변경하지 않는다. 실제주문,
   private API, API Key, secret, wallet과 runtime AI 주문판단은 계속 0이다.

## 검증 경계

수정 전 종목별 warmup helper와 SQLite foreground bracket 표적은 각각 missing contract로
실패했고, runtime 단독 import는 순환 import로 실패했다. 수정 뒤 표적 3건, 관련 supervisor·
저장·replay 84건, 전체 backend 450건과 Ruff·mypy·ESLint·TypeScript·security·repository
hygiene가 PASS했다. 실제 macOS child에서 `taskpolicy -b`와 `-B` 반환도 모두 true였다.

현재 8870은 여전히 기준 commit이므로 새 우선순위와 종목별 warmup의 실제 flush·rotation,
불변 release 활성화, 실제 브라우저, 동일 485,283건 replay, GitHub main·Actions는
`NOT_RUN`이다. commit `15308988242aadd7844da071b0c2bfa430353977`은 불변 릴리스로
stage했고 manifest의 commit·frontend hash와 release-root backend import를 확인했다.
그 릴리스의 격리 `DEMO_FIXTURE` 서버를 대상으로 frontend 64건, fixture 18건,
desktop·tablet·mobile Playwright 3건과 PAPER build safety가 PASS했다. Playwright는 고정
fixture UI 회귀이므로 실제 설치 8870·LIVE_PUBLIC 검증으로 해석하지 않는다. production
bundle 522.23kB에는 500kB 초과 경고가 남아 `PASS_WITH_WARNING`이다. 기준 6시간·24시간
observer는 기존 실패를 포함한 채 `IN_PROGRESS`, 전략 수익성은 `NOT_PROVEN`이다.
