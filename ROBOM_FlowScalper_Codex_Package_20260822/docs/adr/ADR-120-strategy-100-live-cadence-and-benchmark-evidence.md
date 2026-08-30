# ADR-120. 100후보 연구기는 LIVE와 같은 500ms 평가 간격을 사용한다

- 상태. 채택
- 범위. 동결된 공개시장 이벤트를 사용하는 100후보 PAPER screening과 bounded benchmark
- 제외. 실제 주문, private API, API Key, wallet, 입출금

## 문제

기존 100후보 연구기는 모든 호가 이벤트를 PAPER 체결과 피처·전략 재계산에 모두 사용했다. 실제 LIVE 런타임은 모든 호가를 체결 경로에 전달하지만 무거운 피처·전략 평가는 종목별 500ms 간격으로 제한한다. 연구기만 더 촘촘히 재계산하면 실행 시간을 낭비하고 LIVE와 다른 의사결정 주기를 가진다.

또한 첫 10만 이벤트 benchmark는 계산을 끝낸 뒤 `Counter` 진단을 `dataclasses.asdict()`로 변환하는 마지막 단계에서 tuple key가 생겨 JSON 직렬화에 실패했다. 실패한 실행은 처리량 PASS 증거로 쓸 수 없다.

## 결정

1. 모든 sequence-valid 호가는 기존처럼 `PaperPortfolioEngine.on_book()`에 전달한다. 진입, TP, SL, trailing과 종료 체결의 bid·ask 시간 정밀도를 낮추지 않는다.
2. `FeatureEngine.ingest_book()`도 모든 유효 호가를 받아 OFI와 깊이 정보를 보존한다.
3. 피처 snapshot, regime 분류, 포지션 건강평가와 전략 평가는 종목별 500ms에 한 번만 수행한다. 이는 현재 LIVE 런타임 계약과 같다.
4. benchmark는 `RunDiagnostics` 전용 JSON 변환을 사용하고, 실제 `Counter` 키가 든 보고서를 `json.dumps()`할 수 있는지 회귀테스트한다.
5. 실패한 benchmark는 `FAIL` 또는 `NOT_RUN`으로 남기고, 성공한 재실행과 섞어 PASS로 표현하지 않는다.
6. 첫 실패 기록은 `evidence/WAVE119_STRATEGY_100_RESOURCE_BENCHMARK_FAILURE.json`에 별도 보존한다. 처리량과 수익성 근거로 사용하지 않는다.
7. 두 번째 10만 이벤트 실행은 30분 자원 한도를 넘어 `ABORTED_RESOURCE_BUDGET`으로 중단했다. `AlphaFeatureBuilder.snapshot -> _anchored_vwap_bars`를 반복한 것이 주요 병목이었고, 중단 증거는 `evidence/WAVE120_STRATEGY_100_RESOURCE_BENCHMARK_ABORTED.json`에 보존한다.
8. 호가 전달은 줄이지 않고 `PaperPortfolioEngine` 내부에 진입 대기나 포지션이 있는 종목만 계정을 주사하는 O(1) 빠른 경로를 둔다. 아무 상태도 없는 180개 계정을 매 호가마다 반복 주사하지 않는다.
9. 같은 종목·같은 시간구간·같은 의사결정 시각의 불변 alpha snapshot은 전략 패밀리 간 공유한다. 유효한 봉이나 미세구조 입력을 하나라도 받으면 즉시 무효화하므로 미래 데이터나 오래된 호가를 재사용하지 않는다.
10. 성능 최적화는 공개 `on_book()` 계약을 정확히 유지한다. 초기 변경에서 비활성 종목의 `on_book()` 호출 자체를 줄였던 회귀는 기존 테스트가 검출했고, 호출은 보존하되 내부 계정 주사만 줄이도록 수정했다.
11. V2 동결 연구는 checksum으로 고정한 공개봉 워밍업 24종목을 명시적 연구 유니버스로 사용한다. 동일 공개 피드에 섞인 기타 종목은 전체 입력 건수에는 포함하되, PAPER 체결·피처·캔들·전략 계산 전에 제외하고 제외 건수를 진단에 남긴다. 종목 선택을 결과를 보고 바꾸지 않는다.

12. W124부터는 동일한 동결 24종목을 DuckDB 수신순 정렬 전에 제한하되, MULTI parquet 파티션 열이 아닌 `payload_json.symbol`을 필터한다. 종목 선택은 동결 워밍업 manifest를 따라가며 결과를 보고 바꾸지 않는다.
13. 공통 replay 안전감시는 PAPER 포지션이 하나라도 있으면 차단하는 기본값을 유지한다. 다만 100후보 screening은 `--allow-paper-positions`를 명시한 경우에만 LIVE PAPER 포지션과 공존한다. 이 옵션은 `LIVE_SHADOW_PAPER`·`execution_state=PAPER`·실제 주문 false·인증 false를 완화하지 않고, 지연·queue·event stall·저장 장애·drop·비계획 재연결·Run 교체·프로세스 재시작은 기존처럼 즉시 중단한다.

## 수용기준

- 100후보·90실행가능·180 독립 BASE/STRESS PAPER 계좌 계약이 그대로이다.
- 500ms 안의 연속 호가도 PAPER 체결 경로에는 모두 도달한다.
- 피처 snapshot 실행·제한 횟수가 진단 증거에 남는다.
- 비활성 계정 빠른 경로와 snapshot cache hit·miss가 진단 증거에 남는다.
- 재실행 benchmark가 증거 JSON을 원자적으로 쓰고, 모든 안전 불변조건을 PASS해야 한다.
- 처리량 통과를 수익성 통과로 해석하지 않는다.

## 결과

이 결정은 100개 후보를 동시에 사전등록·비교할 수 있게 하지만, 어떤 후보도 수익 후보로 승격시키지 않는다. Train·Validation·비용·PBO·DSR·표본수 기준을 나중에 따로 통과해야 한다.

W121 10만 이벤트 재실행은 1,002.837초, 99.717 events/s로 안전 불변조건을 `PASS`했다. 100개 등록, 90개 실행가능, 180개 BASE/STRESS 계좌, 최종 OOS 미사용과 승격 0을 확인했다. 이 결과는 `evidence/WAVE121_STRATEGY_100_RESOURCE_BENCHMARK_V3.json`에 있으며 수익성은 `NOT_PROVEN`이다.

W122는 W121과 같은 원시 입력 10만 건에서 동결 24종목 계약을 계산 직전에 적용한 재측정이다. 50,755건이 유니버스 밖이었고 72,780회를 후보평가했지만, 1,211.228초·82.561 events/s로 W121보다 느려 전체 screening 실행 구성으로는 채택하지 않았다. 안전 불변조건은 `PASS`, 수익성은 `NOT_PROVEN`이다.

W123은 정렬 전 필터를 처음 적용했지만 MULTI archive의 parquet 상위 `symbol`을 잘못 사용해 0건을 처리했다. `evidence/WAVE123_STRATEGY_100_RESOURCE_BENCHMARK_V5.json`의 상태는 `INSUFFICIENT_BOUNDED_SAMPLE`이며 처리량·수익성·승격 근거로 사용하지 않는다. 회귀테스트는 parquet 상위 종목과 payload 종목을 의도적으로 다르게 고정했다.

W124는 수정된 `payload_json.symbol` 정렬 전 필터로 10만 연구대상 이벤트를 1,225.751초에 처리했다. 처리량은 81.583 events/s, 후보평가는 154,920회·126.388 evaluations/s, peak RSS는 873.797MB였다. 단위 이벤트 속도는 W121보다 낮지만 W122 bounded 표본에서 연구 밖 이벤트가 50.755%였으므로, 전체 Train·Validation에서 무관 이벤트를 계산·정렬 경로에서 제거하기 위해 채택한다. 이 50.755%는 10만 건 bounded 표본이며 전체 데이터의 확정 비율은 아니다.

W124 동시 LIVE 안전감시는 1,224.896초·244회 읽기에서 이벤트 88,730건 전진, lag P95 최대 28.043ms, queue 최대 11, probe 오류·저장 오류·버퍼 손실·비계획 재연결 0, 실제 주문·인증 0이었다. 이 결과는 `evidence/WAVE124_STRATEGY_100_RESOURCE_BENCHMARK_V6.json`과 `evidence/WAVE124_STRATEGY_100_RESOURCE_BENCHMARK_LIVE_GUARD.json`에 나눠 보존한다. 성능·안전 불변조건은 `PASS`, 수익성은 `NOT_PROVEN`, 승격은 0이다.

W125 첫 전체 screening 시도는 시작 snapshot에 이미 보호 중인 BTCUSDT SHORT BASE·STRESS PAPER 포지션 2개가 있어 `POSITION_OPENED`로 즉시 `ABORTED_RUNTIME_SAFETY`가 됐다. worker는 시작되지 않았고 수익성은 `NOT_PROVEN`이다. 장시간 선별 동안 PAPER 진입이 한 번이라도 생기면 동일하게 전부 유실되는 구조였으므로, 공통 기본 차단은 유지하되 100후보 screening에만 PAPER 공존 opt-in을 추가했다. W125 중단 증거는 `evidence/WAVE125_STRATEGY_100_SCREENING_LIVE_GUARD.json`에 보존한다.
