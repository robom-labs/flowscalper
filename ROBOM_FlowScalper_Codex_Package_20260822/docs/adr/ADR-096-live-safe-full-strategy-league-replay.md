# ADR-096. LIVE 우선 11전략 전체 연구 리플레이

## 상태

승인. 구현 완료, 실제 13-Run 실행 대기. 2026-08-29.

## 배경

현재 전략 11개를 같은 공개시장 입력과 22개 독립 BASE·STRESS PAPER 계좌에서 비교하려면
동결된 13개 Run, 2,690,582개 이벤트를 실제 전략→후보→호가깊이 체결→TP·SL→종료 경로로
전수 재생해야 한다. 기존 저장 Run 화면 리플레이에는 저우선순위 worker와 LIVE 자동중단이
연결되어 있지만, 연구용 `research_runtime_strategy_replay.py`를 직접 실행하면 이 보호 계약을
우회할 수 있었다.

과거 대형 재생에서는 같은 외장 저장장치의 archive 읽기와 활성 원장 쓰기가 경쟁하면서
처리 지연, 안전잠금과 비계획 재연결이 발생했다. 따라서 연구 결과를 빨리 얻는 것보다 현재
공개시장 PAPER 수신과 불변 원장을 보호하는 것이 우선이다.

## 결정

1. 전체 연구는 `scripts/run_live_safe_strategy_league_replay.py`를 유일한 운영 실행기로 사용한다.
2. 자식은 `nice(19)`와 macOS background `taskpolicy`로 실행하고 수치 라이브러리 thread를 1개로
   제한한다.
3. 시작 전에 LIVE_SHADOW_PAPER, 공개시장 LIVE, PAPER 체결, 평평한 전체 계좌, 저장 허용,
   실제 주문 false와 인증 false를 확인한다.
4. 실행 중 1초마다 프로세스 uptime, 작동 상태, PAPER 실행, 이벤트 전진, queue, 실행 p95,
   planned·unplanned reconnect, gap, resync, drop, 저장 fault, buffer drop, critical lag, 포지션과 Run을
   감시한다. 프로세스 재시작·작동 정지·PAPER 이탈도 즉시 중단 조건이다.
5. 500ms 초과 로컬 event-loop counter가 기준선보다 한 번이라도 증가하면 별도
   `EVENT_LOOP_LAG_OVER_500MS` 위반으로 즉시 자식을 종료한다. 누적 과거 횟수는 실패로
   재해석하지 않고 실행구간 증가분만 판정한다.
6. 자연 PAPER 포지션이 열리거나 LIVE 안전조건이 흔들리면 연구보다 포지션 보호를 우선해
   자식을 종료한다. planned rotation은 기존 15초 유예 계약 안에서만 허용한다.
7. 자식은 임시 결과에만 쓴다. exit 0, 최종 안전 표본, 11전략·22계좌, PAPER-only,
   실제 주문·인증·runtime AI 0, 현재 archive byte 재검증 PASS를 모두 확인한 뒤에만 최종
   결과 경로로 원자 이동한다.
8. baseline과 후보마다 `signal_gate`, `signal_gate_target_strategy_id`, `strategy_logic`을
   자식 명령에 명시하고, 결과의 `signal_gate_trial_id=<gate>:<target>`까지 요청값과 같아야
   최종 결과를 게시한다. 대상 전략이 다른 결과는 별도 시험이며 합산하지 않는다.
9. 중단·timeout·오류 결과는 전략 성과로 사용하지 않는다. 임시 파일을 제거하고 별도 제어
   증거에 `ABORTED_RUNTIME_SAFETY`, `ABORTED_TIMEOUT` 또는 `FAIL`을 기록한다.
10. 전체 동결 실행의 상한은 8시간이다. 완료보다 LIVE 안정성이 우선이며 자동중단 뒤에는
   원인을 수정하거나 안전한 무부하 창에서 다시 실행한다.
11. 이 보호는 전략 임계값, 비용, fill, TP1, TP2, SL, 위험예산, Governor와 30표본·70%·OOS
    gate를 바꾸지 않는다.

## 검증 경계

대시보드 변환, 500ms counter 증가 자동중단, planned rotation, 자식 명령, 결과 불변조건과
관측 집계를 단위검사했다. 실제 설치 대시보드도 같은 Run, LIVE_SHADOW_PAPER, queue 0,
포지션 0, 실제 주문 false, 인증 false로 새 parser가 읽었다. 동결 archive 현재 bytes 재검증과
2,690,582-event 전체 재생은 진행 중인 무간섭 6시간 observer가 끝나기 전이므로 `NOT_RUN`이다.
그 결과가 나오기 전 전략 순위와 수익성은 `NOT_PROVEN`이다.

2026-08-29 후속 연결 검사에서 안전 래퍼가 대상 전략을 자식에게 전달하지 않던 차이를
수정했다. gate·대상·로직 전달, 다른 시험 결과 거부와 직접 연구 runner의 대상 분리를 합친
표적 테스트 27건, Ruff와 diff 검사가 통과했다. 실제 13-Run 성과는 여전히 `NOT_RUN`이다.
