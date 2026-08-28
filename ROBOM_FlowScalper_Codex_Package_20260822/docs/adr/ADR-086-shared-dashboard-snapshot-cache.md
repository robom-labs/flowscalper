# ADR-086. 대시보드 snapshot·JSON 공용 캐시

## 상태

Accepted, 2026-08-29.

## 배경

수정 전 불변 release `47cf13d46d46b766403e627b44417c28e651e1b7`의 실행 서비스를
6시간 관찰했다. 같은 Run과 process에서 공개시장 event와 전략평가는 계속 전진했고 queue,
메모리, 재연결, drop과 저장 fault도 안전범위였지만, 처리 p95 최대 1,032.383ms,
event-loop 최대 1,914ms, 500ms 초과 497회와 critical lag 사건 46건·incident 3건으로
관찰은 `FAIL`이었다. 저장 flush 24.263초와 WAL checkpoint 30.508초도 각각의 상한을
넘었다.

대시보드 응답은 약 447KB이며 전략·차트·실행감사 집계가 대부분을 차지한다. 기존 HTTP
요청은 매번 전체 snapshot을 만들었고 WebSocket broadcaster도 0.5초마다 같은 집계와
JSON 직렬화를 반복했다. 작업 자체는 worker thread에서 실행됐지만 Python 객체 생성과
직렬화가 GIL을 경쟁했다. 화면 연결과 초당 HTTP 요청을 함께 둔 60초 재현에서 HTTP 최대
응답 624.658ms와 event-loop 500ms 초과 3건이 추가됐다. 긴 저장 flush·checkpoint와
대시보드 반복작업은 같은 원인이라고 가정하지 않고 별도 장기 gate로 남긴다.

## 결정

1. 한 화면 갱신주기의 전체 dashboard snapshot을 HTTP와 모든 WebSocket client가 공유한다.
2. raw HTTP JSON과 `{type: dashboard, data: ...}` WebSocket JSON을 각각 한 번만 만들어
   같은 주기 안에서 재사용한다.
3. snapshot 생성과 JSON 직렬화는 계속 `asyncio.to_thread`에서 실행하고 하나의 async lock으로
   직렬화한다.
4. 화면 갱신주기는 1초로 고정한다. 사용자에게 보이는 초 단위 상태와 차트 갱신은 유지하면서
   기존 0.5초 전체 payload 생성을 절반으로 줄인다.
5. Run, mode, 시장상태, pause, PAPER 진입 의도 revision, 선택 종목·시간구간과 control
   revision이 바뀌면 만료시간을 기다리지 않고 새 snapshot을 만든다.
6. pause·resume·긴급 PAPER 종료·차트 선택·전략 설정·rollback 응답은 강제로 새 snapshot을
   만들어 사용자가 이전 상태를 받지 않게 한다.
7. build와 serialization count·latest·maximum 시간을 고급 진단에 노출한다.
8. 전략 신호, 비용, fill, 위험한도, entry, TP1, TP2, SL, Governor와 원장 정밀도는 바꾸지
   않는다. 실제 주문, private API와 인증도 계속 0이다.

## 결과와 검증 경계

- 새 불변 release `0f09703ea973361c3f8d1c52c55dd0437d671f6f`에서 화면 연결과
  초당 HTTP 60회를 함께 둔 구간은 평균 12.585ms·최대 49.556ms, event-loop 500ms 초과
  0건이었다. 같은 구간에 event 3,923건과 전략평가 16,356건이 전진했다.
- 깨끗한 5분 관찰은 처리/체결 p95 최대 34.640/51.182ms, event-loop 최대 203ms,
  queue 최대 1, flush/checkpoint 최대 1.133/0.774초로 `PASS`했다.
- 이 5분 결과는 수정 후 6시간 또는 24시간의 대체 증거가 아니다. 수정 전 6시간 `FAIL`은
  그대로 보존하고 새 release의 6시간·24시간은 실제 경과 전까지 `NOT_RUN`이다.
- 현재버전 BASE 13건·STRESS 12건과 비용후 음수 표본은 30건 미만이다. 성능 개선을 수익성으로
  해석하지 않으며 수익성은 `NOT_PROVEN`, 실자금 준비는 `NOT_READY`다.
