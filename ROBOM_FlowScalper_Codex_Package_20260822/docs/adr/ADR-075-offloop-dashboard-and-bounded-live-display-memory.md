# ADR-075 이벤트 루프 밖 대시보드와 제한된 LIVE 표시 메모리

## 상태

Accepted.

## 배경

ADR-074 릴리스의 깨끗한 5분 관찰은 queue 최대 21, 처리 p95 47.954ms, 거래 p95
63.624ms로 해당 기준을 통과했지만 500ms 초과 event-loop 지연이 1회 발생해 전체 판정은
`FAIL`이었다. 이 구간에는 자연 거래와 실행상태 저장이 없었고, 지연 시각의 queue는 0,
storage health는 1ms, 최근 WAL checkpoint는 205ms였다. 따라서 원자적 거래 저장과 별개의
루프 내 작업을 추적했다.

FastAPI의 dashboard broadcaster는 연결 화면이 있으면 0.5초마다 `runtime.dashboard()`와 약
240KB JSON 직렬화를 시장 이벤트 루프에서 직접 실행했다. HTTP dashboard 요청과 설정 변경
응답도 같은 동기 경로를 사용했다. LIVE runtime은 화면에서 최근 512개, `/api/events`에서
최근 100개만 사용하면서도 `MarketEvent` 객체 10,000개를 메모리에 유지했다. 원본 이벤트는
별도의 bounded persistence buffer를 거쳐 Parquet archive와 SQLite manifest에 전량 보존되므로
이 큰 deque는 복구·replay의 권위 원본이 아니었다.

## 결정

1. dashboard snapshot 집계와 JSON 직렬화를 `asyncio.to_thread`로 이벤트 루프 밖에서
   실행한다.
2. 단일 `asyncio.Lock`으로 WebSocket broadcast, HTTP dashboard와 변경 응답의 snapshot
   생성을 직렬화해 중복 대형 집계를 막는다. `/api/status`와 시장 소비는 그 lock을 사용하지
   않는다.
3. LIVE 표시용 event deque를 2,048개로 제한한다. 화면 집계는 deque 전체를 10,000개 tuple로
   복사하지 않고 역방향 `islice`로 필요한 최근 512개만 복사한다.
4. READY·fixture·replay의 기존 10,000개 테스트 메모리 계약은 유지한다. LIVE 원본 보존,
   ReplayEngine 입력, persistence buffer와 archive 범위는 변경하지 않는다.
5. 동기 LIVE event 판단의 최근·최대 소요시간, 100ms 초과 횟수, 최대 이벤트 종류와 종목을
   고급진단과 soak 표본에 추가해 이후 정지를 dashboard와 전략 판단 중 어느 경로인지 직접
   구분한다.
6. 전략 임계값, 자연신호, TP1·TP2·SL, 비용, 위험예산, 11전략·22계좌와 실제 주문 0 경계는
   변경하지 않는다.

## 결과

- 화면 연결과 HTTP 조회가 시장 소비기·watchdog을 직접 멈추지 않는다.
- LIVE 시작 후 표시 메모리의 불필요한 증가와 큰 deque 순회 비용이 줄어든다.
- 표시 메모리에서 빠진 이벤트도 권위 있는 공개시장 archive에 그대로 남아 replay할 수 있다.
- 수정 릴리스의 별도 5분·20분·6시간·24시간 관찰로 event-loop 500ms 초과 0회와 queue 64
  이하를 다시 확인해야 한다. 실제 시간을 채우지 않은 6시간·24시간은 `NOT_RUN`이다.
