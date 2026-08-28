# ROBOM FlowScalper v0.2 장시간 실행 보고서

- 실행일: 2026-08-22.
- 대상: Binance USDⓈ-M 공개 REST/WebSocket, `LIVE_SHADOW_PAPER`.
- 인증 경계: API Key·로그인·private API·실제 주문 0.
- 기본 감시 범위: wide 50종목, deep 10종목.
- 진입 지연 임계: rolling p95 1,500ms 초과 시 supervisor와 runtime이 함께 PAPER 신규 진입을 잠근다.

## 실행 결과

| 실행 | 상태 | 시간 | 핵심 결과 |
|---|---|---:|---|
| 최초 30분 | FAIL | 1,800초 | 6,503,324 events, drop 0, queue max 2, memory +188.547MB였으나 1,500ms 초과 지연 표본에서 진입잠금이 계속 결합돼 있지 않은 fail-open 가능성을 발견했다. |
| 수정 후 60초 1차 | FAIL | 60초 | 임계 지연에서 fail-closed는 작동했으나, 수용검사가 종료 시 p95 정상화만 요구해 안전하게 잠긴 종료 상태를 실패로 오판했다. |
| 수정 후 60초 2차 | PASS | 60초 | 136,532 events, reconnect 1, gap/resync/drop 0, queue max 2, memory +70.187MB, 최대 p95 12,861ms, 임계 표본 12개 모두 fail-open 0. 종료 p95 5,371ms에서 supervisor lock과 runtime pause가 모두 유지됐다. |
| 수정 후 최종 30분 | PASS | 1,800초 | 3,120,256 events, reconnect 39, gap/resync/drop 0, queue max 2, memory +132.922MB, 최대 p95 21,161ms, 임계 표본 171개 모두 fail-open 0. 종료 p95 6,434ms에서 supervisor lock과 runtime pause가 모두 유지됐다. |

## 발견 원인과 수정

최초 구현은 공개 이벤트 지연을 telemetry로 계산했지만 rolling p95가 임계를 넘는 순간을 런타임의 신규진입 잠금과 지속적으로 결합하지 않았다. 다음과 같이 수정했다.

1. supervisor가 rolling p95 1,500ms 초과를 `critical_lag_active`와 `entry_locked`로 고정한다.
2. runtime은 supervisor 잠금을 `SUPERVISOR_ENTRY_LOCK`과 `CRITICAL_MARKET_LAG_ENTRY_LOCK`으로 반영하고 PAPER 진입을 즉시 pause한다.
3. UI 재개 요청은 supervisor 잠금이 살아 있는 동안 거부한다.
4. rolling p95가 정상화되고 fresh sequence-valid depth가 확인된 뒤에도 runtime pause는 자동 해제하지 않는다. 사용자가 명시적으로 재개해야 한다.
5. 실제 네트워크 수용검사는 지연이 전혀 없음을 요구하지 않는다. 모든 임계 초과 표본에서 fail-open이 0인지, 종료 시 지연이 정상이거나 양쪽 진입잠금이 유지되는지를 검사한다.

이 변경은 전략 문턱이나 지연 임계값을 낮춘 것이 아니다. 불안정한 공개 네트워크에서 신규 PAPER 진입을 더 보수적으로 막는 안전 변경이다.

최종 실행 중 공개 WebSocket 재연결은 39회 관찰됐다. 이를 숨기거나 안정적인 연결로 과장하지 않는다. sequence gap·resync·drop은 0이었고 수신은 3,120,256건까지 계속됐으며, 지연이 임계보다 높은 모든 표본에서 신규 PAPER 진입이 잠겨 수용기준을 충족했다. 이 결과는 네트워크 품질 보증이 아니라 끊김과 지연 속에서의 fail-closed 동작 증거다.

## 자동 수용 기준

- 공개 LIVE 시작과 요청 시간 완료.
- wide 50 이상, deep 8~12 유지.
- 이벤트 수 증가와 메모리 event buffer 10,000건 이하.
- queue capacity 초과 0, dropped event 0.
- 모든 p95 1,500ms 초과 표본에서 supervisor 또는 runtime의 fail-open 0.
- 종료 시 p95 1,500ms 이하 또는 supervisor lock과 runtime pause 동시 유지.
- 관찰된 max RSS 증가 256MB 이하.
- 실제 주문 비활성, 인증 불필요.

## 6시간·24시간 실행

| 항목 | 상태 | 근거 |
|---|---|---|
| 6시간 soak | NOT_RUN | `scripts/soak_6h.command` 제공. 이번 업그레이드 세션에서 실제 6시간을 경과시키지 않았다. |
| 24시간 soak | NOT_RUN | `scripts/soak_24h.command` 제공. 이번 업그레이드 세션에서 실제 24시간을 경과시키지 않았다. |

`NOT_RUN`은 PASS로 간주하지 않는다. 두 스크립트는 30분 검사와 같은 코드·임계·증거 JSON 형식을 사용한다.

## 2026-08-26 Wave 34 재검증

현재 구현으로 격리된 실제 공개시장 30분 검사를 다시 실행했다. `soak-9d9cc1e8cbcf`는 요청한 1,800초를 모두 채워 130,248 events를 처리했다. 계획 회전 1회와 전체 reconnect 1회가 일치했고 비계획 reconnect·sequence gap·resync·drop·critical lag incident는 모두 0이었다. queue 최대는 12/4,096, 실행경로 p95 최대는 62.467ms, 메모리 증가는 143.407MB였고 종료 시 신규진입 잠금은 해제돼 있었다. 실제 주문과 인증은 false였다.

같은 호스트에서 별도 전체 연구와 기존 서비스를 동시에 실행했을 때 기존 서비스의 계획 회전 1회가 86.467초 critical lag로 늘어난 표본은 숨기지 않는다. 이는 격리 30분 검사에서 재현되지 않았고 자동 복구됐으므로 `PASS_WITH_LIMIT`다. 무거운 offline 연구를 LIVE와 같은 호스트에서 전속 실행하지 않는 기존 경계를 유지한다.

6시간과 24시간은 이번 Wave에서 실제 시간을 채우지 않았으므로 계속 `NOT_RUN`이다.

## 2026-08-26 Wave 49 실행 서비스 비침습 30분 검증

기존 8870 LaunchAgent 서비스 자체를 증명하기 위해 별도 `PaperRuntime`, 공개시장 연결, Run, replay 또는 SQLite writer를 만들지 않고 `/api/dashboard`만 읽는 검사를 추가했다. `make soak-30m`과 달리 이 검사는 설치 서비스의 같은 Run·같은 프로세스가 실제로 계속 전진하는지를 본다.

`evidence/WAVE49_RUNNING_SERVICE_SOAK_30M.json`은 `run-2b7135a972dd`를 monotonic 1,800.038초·181표본 동안 관찰한 `PASS`다. 시스템 UTC 시각 차이는 동기화 보정 때문에 1,799.986초였고, 실제 경과 수용판정은 시각 조정에 영향받지 않는 monotonic clock을 사용했다.

| 항목 | 실제 결과 |
|---|---:|
| event 전진 | +158,346 |
| 전략 평가 전진 | +486,276 |
| 적격신호·main 거래·League 거래 증가 | 0·0·0 |
| 계획 rotation / reconnect / 비계획 reconnect | 2 / 2 / 0 |
| gap / resync / drop / 저장 fault / buffer drop / WAL fault | 모두 0 |
| queue 최대 | 23 / 4,096 |
| 실행호가 p95 최대 / 체결 p95 최대 | 122.399ms / 508.430ms |
| wide p95 최대 | 1,814.534ms, 관찰 전용 |
| 저장 flush / WAL checkpoint | 1→80 / 0→10 |
| flush / checkpoint 최대시간 | 10.145초 / 14.019초 |
| 현재 RSS 기준선 / 최대 / 증가 | 184.281MB / 279.891MB / 95.610MB |
| 전략 / 독립 BASE·STRESS 계좌 | 11 / 22, 전 표본 완전 |
| 최대 포지션 / 실제 주문 / 인증 | 0 / false / false |
| probe 오류 / 실패한 검사 | 0 / 0, 45개 전부 true |

적격신호와 신규 거래가 0인 결과를 성능 결함이나 수익성으로 해석하지 않는다. 현재버전 BASE 5건은 -3.573282460 USDT, STRESS 5건은 -6.819651904 USDT로 표본이 부족하고 비용후 손실이다. 전략 수익성은 `NOT_PROVEN`이며 진입조건을 낮추지 않았다. 6시간·24시간은 이 구현으로 실제 시간을 채우지 않았으므로 계속 `NOT_RUN`이다.

## 2026-08-29 Wave 104 대시보드 부하 수정 전·후 검증

수정 전 불변 release `47cf13d…`의 실행 서비스를 21,600.053초·720표본 동안 실제
관찰했다. event +1,560,430, 전략평가 +5,706,432, queue 최대 59, 메모리 증가
11.203MB였고 비계획 reconnect·gap·resync·drop·persistence fault·buffer drop은 0이었다.
그러나 처리 p95 최대 1,032.383ms, event-loop 최대 1,914ms·500ms 초과 497회,
flush 최대 24.263초, WAL checkpoint 최대 30.508초, critical lag event 46건·incident
3건으로 `FAIL`이다. 원본 `WAVE104_RUNNING_SERVICE_SOAK_6H.json`을 그대로 보존한다.

대시보드 snapshot과 HTTP·WebSocket JSON을 1초 공용 캐시로 바꾼 불변 release
`0f09703e…`에서 화면 연결을 둔 채 초당 HTTP 60회를 실행했다. 평균/최대 응답은
12.585/49.556ms, event-loop 500ms 초과·critical lag·저장 fault·buffer drop 증가는
0이었다. 같은 60초 동안 event +3,923, 전략평가 +16,356이 전진했다.

이어 `WAVE104_POST_DASHBOARD_CACHE_CLEAN_5M.json`은 300.021초·61표본을 채워
`PASS`했다. event +19,094, 전략평가 +78,048, queue 최대 1, 처리/체결 p95 최대
34.640/51.182ms, event-loop 최대 203ms·500ms 초과 0, flush/checkpoint 최대
1.133/0.774초였다. 비계획 reconnect·gap·resync·drop·persistence fault·buffer drop·
critical lag·실제 주문·인증은 모두 0이었다.

이 5분은 수정 후 장시간 안정성의 대체 증거가 아니다. 수정 후 6시간과 24시간은
실제 시간을 채우기 전까지 `NOT_RUN`이다. 현재버전 BASE 13건·STRESS 12건, 순손익
-18.1140910600/-22.4771721880 USDT이며 새 표본 증가는 0이므로 수익성은 `NOT_PROVEN`이다.
