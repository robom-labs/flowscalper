# ADR-103. localhost WebSocket 압축 비활성화와 연구 실행환경 지문

## 상태

수용. 2026-08-30 WAVE116H. 다만 6시간·24시간 안정성과 수익성은 각각 `NOT_RUN`,
`NOT_PROVEN`이다.

## 관찰

WAVE116F E06 후보 계산은 신규 500ms 초과 이벤트 루프 지연을 감지해 안전중단됐다. 이후
무거운 replay가 없는 설치 서비스에서도 별도의 792ms 지연이 발생했다. 따라서 이전 안전중단을
연구 자식 하나의 원인으로 단정할 수 없었다.

설치 서비스에 실제 브라우저와 세 개의 읽기 전용 WebSocket 화면을 연결했을 때 각 화면은 약
160KB 대시보드 상태를 초당 한 번 받았다. 변경 전 연결은 `PerMessageDeflate`를 협상했고,
서비스 main-loop 표본에는 zlib·WebSocket·SSL 전송 작업이 함께 나타났다. 이는 반복 가능한
부하 경로를 확인한 것이지만 792ms 지연의 유일한 원인이라는 증거는 아니다.

또한 E06의 기존 구현 지문은 전략과 replay 소스에는 묶여 있었지만 설치 런처와 연구 제어기의
변경은 포함하지 않았다. 같은 파라미터·데이터·비용이라도 런타임 부하 경로가 달라진 시도를
동일 구현으로 오인할 수 있었다.

## 결정

1. localhost 지원 실행기 `scripts/run_server.py`에서 WebSocket per-message 압축을 끈다.
   대시보드 공유 snapshot과 초당 화면 갱신 주기는 그대로 유지한다.
2. 정상 HTTP 접근 로그 비활성화도 유지한다. 시작·종료·WebSocket 연결과 예외 로그는 남긴다.
3. E06 `implementation_fingerprint`에 설치 런처와 LIVE-safe 연구 제어·safety·후보 생성 소스를
   포함한다. 파라미터·데이터·비용 지문은 독립적으로 계속 기록한다.
4. 이 변경은 전략 신호, bid·ask PAPER 체결, 비용, 수량, 위험예산, TP1, TP2, SL과 종료 규칙을
   바꾸지 않는다.
5. 실제 주문, private API, 인증, API Key, secret과 wallet 경로는 계속 0이다.
6. 같은 구현·파라미터·데이터 지문의 E06 안전중단을 반복하지 않는다. 새 시험은 실제로 변경된
   실행환경 지문을 기록하고 LIVE 안전감시를 다시 통과해야 한다.

## 회귀 계약

- `test_supported_launcher_disables_per_request_access_log`가 접근 로그와 WebSocket 압축을 함께
  비활성화하는 지원 실행기 계약을 고정한다.
- `test_runtime_infrastructure_changes_implementation_fingerprint`가 런처 변경은 구현 지문만 바꾸고
  파라미터·데이터·비용 지문은 바꾸지 않는 계약을 고정한다.
- `config/regression_contracts.json`의 `LOCAL_WEBSOCKET_COMPRESSION_DISABLED`와
  `RESEARCH_RUNTIME_INFRASTRUCTURE_FINGERPRINT`가 이후 Wave의 앵커 제거를 막는다.

## 실제 수용 증거

불변 release `baf43d056b1852d49cea2ad44258d41011fde6fd`를 설치하고 같은 Run
`run-2b7135a972dd`를 복구했다. 실제 브라우저 한 화면과 추가 읽기 전용 WebSocket 세 화면을
동시에 연결했다. 추가 화면은 각각 900초 동안 879개 상태를 받았고 공개시장 event가 각
70,058건 전진했다. 세 연결 모두 협상 확장과 `Sec-WebSocket-Extensions` 응답이 없었다.

이어 1,050.016초·522표본 관찰이 `PASS`했다. LIVE event는 81,431건, 전략 평가는
280,140회 전진했다. 계획회전 1회와 reconnect 1회가 일치했고 비계획 reconnect, gap, resync,
drop, persistence fault와 buffer drop은 0이었다. 최대 queue는 19/4096, 처리 p95는
67.682ms, 실제 체결입력 p95는 92.143ms, 최대 event-loop 지연은 291ms이며 신규 500ms
초과는 0이었다. 메모리 증가는 27.781MB였다.

같은 관찰의 적격신호, 진행 포지션과 신규 완료거래는 모두 0이었다. 이 결과는 거래기록이
멈췄다는 뜻이 아니라 해당 구간에 새 자연 진입·종료가 없었다는 뜻이다. 실제 거래기록 화면은
계획회전 뒤에도 `화면 연결됨`, 진행 0건, 완료 33건을 유지했고 수동 확인 시각이
01:26:59 KST로 전진했다. 현재 Run 현재버전 API 33건, 현재 Run 모든 버전 128건, 전체
보관 853건도 유지됐다.

원시 증거는 `evidence/WAVE116H_WS_COMPRESSION_RUNTIME_QA.json`,
`evidence/WAVE116H_HISTORY_AND_WS_RUNTIME_QA.json`과
`evidence/screenshots/WAVE116H_HISTORY_POST_ROTATION_QA.jpg`에 보존한다.

## 증거 경계

이번 결과는 네 화면과 계획회전을 포함한 17분 30초 단기 증거다. 6시간과 24시간을 실제로
채우지 않았으므로 둘 다 `NOT_RUN`이다. 자연 공개시장 구간에 비영 진행 포지션이 없었으므로
실제 브라우저의 비영 open-to-close 수명주기는 `NOT_OBSERVED`이고, 결정론 회귀만
진행 3→1→0과 완료 0→2→3을 증명한다. 전략별 표본은 30개 고유 기회 미만이며 수익성과
실자금 준비상태는 각각 `NOT_PROVEN`, `NOT_READY`다.
