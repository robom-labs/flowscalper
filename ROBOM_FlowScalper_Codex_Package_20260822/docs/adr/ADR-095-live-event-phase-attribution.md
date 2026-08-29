# ADR-095. LIVE 호가 처리 지연의 단계별 귀속

## 상태

승인. 개발 소스와 회귀테스트에는 반영했고, 현재 실행 중인 Wave104 6시간 관찰이 끝나기 전에는 서비스 재시작과 배포를 하지 않는다.

## 확인된 사실

- `run-2b7135a972dd`에서 2026-08-29 12:25:46 KST에 BTCUSDT `DEPTH_UPDATE` 한 건의 동기 처리시간이 1,504ms였다.
- 같은 시각 이벤트루프 지연 최대값은 1,505ms였고 500ms 초과 누적값이 6에서 7로 증가했다.
- 그 뒤에도 이벤트는 전진했고 queue, unplanned reconnect, sequence gap, resync, drop, persistence fault, buffer drop과 executable critical lag는 0이었다.
- 이 사건은 AGGRESSOR PAPER SHORT 진입 직후 발생했지만 기존 진단은 전체 동기 경로만 측정하므로 포지션 관리, 전략평가, 후보계획 또는 운영체제 스케줄링 중 어느 단계가 원인인지 확정할 수 없다.
- 해당 BASE·STRESS 거래는 1~15초에 종료되지 않았다. 둘 다 900,900ms 보유 후 `MAX_HOLD`로 종료됐다.

## 결정

기존 PAPER 판단 순서와 체결 계약은 바꾸지 않고 다음 단계별 경과시간만 `time.perf_counter()`로 측정한다.

1. `INGEST_PRE_DISPATCH`
2. `BOOK_BUILD`
3. `PAPER_PORTFOLIO_ON_BOOK`
4. `POSITION_STATE`
5. `FEATURE_SNAPSHOT`
6. `HEALTH_EVALUATION`
7. `STRATEGY_EVALUATION`
8. `CANDIDATE_PLANNING`
9. `STORAGE_SAFETY`
10. `PORTFOLIO_OFFER`
11. `EXECUTION_PERSISTENCE`
12. `INGEST_POST_DISPATCH`

운영진단에는 최근 단계별 시간, 프로세스 수명 중 최장 단계와 시각·이벤트 종류·종목, 100ms 이상 단계 누적값을 노출한다. 기존 running-service soak는 이 값을 읽어 증거 JSON에 최장 단계와 신규 느린 단계 수를 보존한다.

## 안전 경계

- 계측은 실제 주문, private API, 인증과 wallet 경로를 추가하지 않는다.
- 신호 기준, 비용, 수량, TP1, TP2, SL, EDGE_DECAY와 최대보유시간을 변경하지 않는다.
- 진행 중인 Wave104 관찰은 설치된 release 소스로 끝까지 유지한다.
- 설치 후 새 관찰에서 500ms 초과가 재발하면 최장 단계가 가리키는 코드만 최소 범위로 수정한다.
- 새 6시간을 실제로 채우기 전에는 장시간 안정성을 PASS로 기록하지 않는다.

## 검증 계약

- 느린 `PAPER_PORTFOLIO_ON_BOOK`을 주입한 테스트에서 해당 단계가 최장 단계로 보고돼야 한다.
- 기존 운영안전과 running-service soak 회귀테스트가 모두 통과해야 한다.
- 배포 전 상태는 `NOT_RUN`, 배포 후 짧은 확인은 장시간 안정성의 대체 증거가 아니며 새 6시간 관찰 결과만 최종 판정에 사용한다.
