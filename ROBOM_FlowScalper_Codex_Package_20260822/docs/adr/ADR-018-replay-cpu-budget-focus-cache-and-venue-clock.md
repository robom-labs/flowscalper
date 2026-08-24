# ADR-018. 대형 replay CPU 예산, 거래 집중 캐시와 거래소 시각 보정

## 상태

Accepted, 2026-08-25.

## 문제

LIVE 서비스에서 수십만 건 이상의 저장 Run을 replay하거나 거래 상세를 처음 열면 SQLite·Parquet 읽기, 전략 재처리, 정렬과 checksum 계산이 길게 이어졌다. 계산을 별도 프로세스로 옮긴 뒤에도 CPU를 제한하지 않으면 공개시장 WebSocket 수신이 밀려 실행경로 지연과 일시 진입잠금을 만들 수 있었다. 누적 실행시간 전체를 기준으로 CPU 빚을 계산한 첫 제한기는 앞선 고부하를 뒤늦게 갚느라 긴 sleep을 반복했고, 이벤트와 결정경로 전체를 다시 canonical JSON으로 만든 checksum 단계는 peak RSS를 약 2.1GB까지 키웠다. 일반 거래기록 화면도 모든 전략 구현 버전의 과거 거래를 섞어 보여 현재 소프트웨어의 보유시간·성과를 오해하게 했다.

또한 로컬 Mac 시각이 거래소 공개 시각보다 약 2초 느린 환경에서 `max(0, local - venue)` 계산은 실제 지연을 전부 0ms로 숨겼다. 운영체제 시각을 프로그램이 임의로 바꾸지 않으면서 공개 거래소 시각 기준으로 지연을 계산해야 했다.

## 결정

1. LIVE 중 전체 replay, 일반 timeline과 거래 집중 replay는 하나의 process lock을 공유하고 독립 SQLite·Parquet 연결을 가진 `anyio.to_process` worker에서만 실행한다.
2. worker에는 OS `nice(19)`와 한 코어 기준 5% CPU 예산을 적용한다. 예산은 replay 시작 이후 누적 평균이 아니라 인접 checkpoint 구간별 wall time과 process CPU time으로 계산하며, 한 번의 sleep은 최대 0.5초다. 전략 재처리는 16 events, streaming checksum은 128 events마다 양보하고 이벤트 읽기·정렬·중복검사도 협력 checkpoint를 호출한다. 초기 10%·64/512-event 설정에서 LIVE 동시 replay 중 임계지연 표본이 늘어난 실측 때문에 완료시간보다 공개시장 수신 여유를 우선해 보수적으로 낮췄다.
3. replay checksum 계약을 schema 3으로 올린다. 정렬된 이벤트는 하나씩 정규화해 길이 prefix와 canonical JSON bytes를 SHA-256에 넣고, 결정경로도 같은 방식의 별도 digest로 만든다. 최종 checksum material에는 전체 이벤트·결정 문자열 대신 두 digest와 count·config·version·최종상태만 넣는다. 이전 replay 결과와 checksum은 불변 원장에 그대로 보존하지만 schema 2 checksum과 schema 3 checksum을 같다고 가정하지 않는다.
4. 신규 Parquet batch에는 `venue_ts_ms`, `symbol`, `event_type`, `batch_checksum` 색인 열을 추가한다. 조회는 관련 manifest만 선택하고 배치의 모든 row checksum과 저장된 `batch_checksum`을 먼저 대조한 뒤 필요한 row만 decode한다. 일부 row가 잘린 배치는 필터 결과가 정상이어도 실패한다. 구형 batch는 기존 전체 검증 경로로 호환한다.
5. 거래 집중 replay는 거래 전 20분과 종료 후 5분 범위만 읽고, 해당 구간을 이미 포함하는 안전한 replay 결과가 있으면 전략 전체 재처리를 반복하지 않는다.
6. 완성된 집중 replay session은 schema v7 `replay_focus_cache`에 zlib 압축 payload와 별도 SHA-256으로 보존한다. 동일 Run·거래·profile·session version 요청은 checksum 검증 후 반환한다.
7. LIVE 거래기록 기본 범위는 현재 `STRATEGY_VERSION`의 `LIVE_PUBLIC` main 거래만 사용한다. 이전 버전 거래는 삭제하지 않고 불변 원장에 보존하며 제외 건수를 화면에 표시한다.
8. Binance USD-M `/fapi/v1/time`과 Bybit V5 `/v5/market/time`의 인증 없는 공개 시각을 세 번 측정하고, RTT가 가장 작은 표본의 로컬 중간시각으로 거래소 오프셋을 계산한다. 지연은 `local + venue offset - event timestamp`로 계산하고 0 미만만 0으로 제한한다.
9. 시각 보정값, RTT와 `SYNCED` 여부를 supervisor telemetry와 시스템 고급진단에 표시한다. 공개 시각 확인 실패는 검증된 LIVE 연결로 가장하지 않고 기존 fail-closed 공급자 전환 규칙을 따른다.

## 검증

- cooperative callback 유무가 replay checksum과 집계를 바꾸지 않는지 검사한다.
- schema v7 migration, 집중 replay cache checksum·압축 손상·결정성, 시간창 조회와 구형 Parquet fallback을 검사한다.
- LIVE API의 전체 replay·timeline·focus가 동일 process lock을 사용하는지 검사한다.
- 거래소 공개 time adapter에 인증 헤더가 없고, 최소 RTT 오프셋과 보정 지연 계산이 결정적인지 검사한다.
- 현재 전략 버전 거래만 기본 기록에 보이고 이전 버전 제외 건수가 유지되는지 검사한다.
- 실제 브라우저에서 첫 집중 replay 대기 상태, 캐시 재호출, 신호·진입·핵심·종료 이동, 80배속 재생·일시정지를 누른다.
- 대형 저장 Run replay 동안 1초·30초 간격으로 LIVE p95, queue, 임계지연, 진입잠금, 재연결, gap, drop과 오류를 측정한다.

2026-08-25 로컬 실측에서 schema 2 누적예산 구현은 332,553 events를 7,055초에 완료했지만 LIVE p95가 전체 창 최대 3,352ms, 병렬 회귀 부하가 끝난 뒤에도 최대 1,804.5ms였고 안전잠금 표본이 남아 성능 수용기준을 `FAIL`로 판정했다. 결과 checksum과 집계 자체는 완성됐으며 실제주문과 인증은 false였다.

schema 3 구간예산·streaming 구현은 같은 저장 원장의 85,714 events를 두 번 replay해 각각 472초와 473초에 완료했다. 두 실행은 checksum `e88e18b62d3c0b40efcfb6529aae3e7eea118dfacf40c49758452a86ebcd1fc7`, 평가 154,208·적격 24·후보 6·shadow 9·결정경로 396으로 일치했다. LIVE 표본은 각각 p95 최대 171.5ms와 659.5ms, queue 최대 2와 17, 비정상 재연결·gap·drop·critical lag·진입잠금·저장 fault 모두 0이었다. replay peak RSS는 약 529MB와 536MB였다. 새 schema 3으로 332,553건 전체를 다시 실행한 결과는 `NOT_RUN`이다.

## 한계

CPU 예산은 replay 완료시간보다 공개시장 수신을 우선한다. 첫 집중 replay는 저장 구간과 기존 replay 증거에 따라 수십 초 이상 걸릴 수 있지만 이후 동일 요청은 캐시를 사용한다. 한 번에 하나의 replay만 허용하며 다른 요청에는 HTTP 409 `REPLAY_BUSY`와 재시도 안내를 즉시 반환한다. 공개 인터넷 자체의 지연이나 거래소 이벤트 timestamp 의미 차이는 제거할 수 없으며, 임계 지연에서는 기존 PAPER 신규진입 잠금과 자동 복구를 유지한다. 중간 규모 결정성 통과는 새 schema의 332,553건 전체 replay, 6시간·24시간 soak 또는 전략 수익성을 증명하지 않는다.
