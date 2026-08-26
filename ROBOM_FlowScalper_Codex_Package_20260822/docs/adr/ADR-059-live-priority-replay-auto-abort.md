# ADR-059. LIVE 우선 저장 리플레이 자동중단

## 상태

승인. 2026-08-27.

## 배경

기준 설치 서비스의 실제 `run-2b7135a972dd`에서 `ONGUSDT` 저장 이벤트 485,283건을
`nice(19)`·구간 CPU 5% worker로 전체 재처리했다. 약 59분 동안 queue는 0~1이고
sequence gap·resync·drop·persistence fault·buffer drop은 0이었지만, 후반에 공개 WebSocket
keepalive ping timeout과 함께 비계획 reconnect가 0에서 1로 증가했다. 같은 구간의 실행경로
p95는 최대 약 23.9초, 공개 체결 p95는 약 11.2초까지 상승했고 런타임은
`SAFETY_WAITING`·`entry_locked=true`로 전환했다.

사람이 operation을 취소한 뒤 worker는 종료됐고 서비스는 약 1분 안에 `RUNNING`으로
복구했다. 외부 공개 스트림 정지와 로컬 replay 부하 중 어느 하나만을 원인으로 확정할 수는
없다. queue 포화가 없고 provider ping timeout이 있었으므로 replay CPU가 단독 원인이라는
증거도 없다. 그러나 LIVE 안전상태가 이미 깨졌는데 장시간 replay가 스스로 중단되지 않은
것은 원인과 무관하게 별도 안전 결함이다.

## 결정

1. LIVE 중 저장 Run 전체 검증은 worker를 시작하기 전에 Run·모드·공개시장·PAPER·저장·
   진입잠금·포지션·실제주문·인증의 최소 안전 snapshot을 고정한다.
2. worker가 도는 동안 1초마다 가벼운 runtime snapshot을 읽는다. queue 64 초과, 실행 p95
   500ms 초과, critical lag, 30초 이벤트 무진행, 신규 비계획 reconnect·gap·resync·drop·
   persistence fault·buffer drop·critical incident, Run 변경, 시장 단절, 저장 잠금, 포지션
   개시, 실제 주문 또는 인증 활성화가 하나라도 나타나면 fail-closed한다.
3. 정상 planned rotation은 reconnect 수와 맞고 15초 유예 안에 있을 때만 일시적인
   entry lock을 허용한다. planned count와 reconnect count가 맞지 않으면 중단한다.
4. 안전위반 시 `anyio.to_process.run_sync(..., cancellable=True)` task를 취소하고 worker 종료를
   기다린 뒤 operation을 `FAILED_RETRYABLE`·`REPLAY_ABORTED_LIVE_SAFETY`로 확정한다. 한국어
   오류에는 실제 원인 코드를 포함한다.
5. LIVE worker는 결과를 직접 append하지 않는다. 최종 안전 snapshot까지 통과한 결과만
   부모 프로세스가 `replay_runs`에 기록한다. 취소·안전실패 결과는 정상 checksum 증거처럼
   목록에 남기지 않는다.
6. 이미 분리된 DEMO·REPLAY 직접 실행은 기존처럼 결과를 기록한다. 이번 결정은 LIVE 병행
   경로만 강화한다.
7. 전략 신호·임계값·비용·TP·SL·체결·위험예산·계좌는 변경하지 않는다. 거래를 만들기 위해
   기준을 낮추지 않고 실제 주문·private API·API Key·secret·wallet은 계속 0으로 유지한다.

## 결과

- 공개시장 안전이 재생 완료보다 우선하며 사람이 화면을 지켜보지 않아도 대형 worker가
  자동 종료된다.
- 안전실패 operation과 정상 완료 replay result가 분리되어 checksum·평가 결과의 의미가
  정직해진다.
- 원인 코드와 상태 전환 감사가 남아 외부 스트림 장애, 계획 회전, 지연, 저장 결함을 다시
  대조할 수 있다.
- 단일 장애 관찰을 replay의 단독 인과관계나 전략 수익성 증거로 과장하지 않는다.

## 검증 경계

순수 guard·planned rotation·event stall·critical lag·worker 취소·일시 probe 오류와 LIVE HTTP
자동중단·미기록 회귀를 추가했다. 관련 backend 38건과 Ruff·mypy는 PASS했다. 이 시점의 실제
8870은 기준 commit을 계속 실행하므로 새 자동중단의 설치 런타임 검증, 보호 경로를 포함한
485,283건 전체 재시도, 배포 후 브라우저, GitHub main·Actions는 `NOT_RUN`이다. 기존 기준
서비스 6시간·24시간 observer는 `IN_PROGRESS`, 전략 수익성은 `NOT_PROVEN`이다.

replay worker를 종료한 뒤 별도 30분 비교도 `FAIL`이었다. event는 134,570건 전진하고 queue
최대 1, 비계획 reconnect·gap·drop·저장 fault는 추가되지 않았지만, 시작 직후 rolling trade
p95가 1,343.622ms까지 남았고 별도의 저장 flush가 22.636초 걸렸다. planned rotation 중에는
8.027초 critical incident가 1건 추가됐다. 따라서 replay 단독 인과는 입증되지 않았고 기준
서비스 자체의 저장·회전 장시간 한계도 독립 후속으로 남긴다. 이 결과는 자동중단 필요성을
없애지 않으며, 보호경로는 이런 상태에서도 LIVE를 우선해 재생을 즉시 양보해야 한다.
