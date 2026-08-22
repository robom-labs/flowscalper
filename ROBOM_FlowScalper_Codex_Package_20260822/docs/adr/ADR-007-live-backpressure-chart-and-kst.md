# ADR-007. LIVE backpressure, 차트 수명주기, KST 표시

- Status: Accepted
- Date: 2026-08-22
- Owners: ROBOM / Codex

## Context

실제 LIVE 운영에서 50개 `bookTicker`와 10개 `depth@100ms`를 하나의 고빈도 처리 경로에 두면 거래소 이벤트가 WebSocket 내부 queue에 쌓여 13~23초까지 느려졌다. 넓은 감시 `24hrTicker`의 초기 event ID도 같은 millisecond에 충돌해 SQLite 쓰기 실패 배치를 매 이벤트마다 다시 직렬화하는 폭주를 만들었다.

프런트엔드는 WebSocket snapshot이 도착할 때마다 Lightweight Charts 인스턴스를 제거·재생성했고, CSS grid가 scanner와 chart를 같은 높이로 stretch해 차트가 과도하게 길어졌다. 차트 timestamp는 Unix UTC를 브라우저 기본 해석에 맡겨 한국 현재 시각과 일치하지 않을 수 있었다.

## Decision

1. Binance wide 50개는 1초 `24hrTicker`, deep 10개는 기본 250ms diff-depth, 실제 체결은 `aggTrade`로 서로 다른 WebSocket에서 수신한다.
2. PAPER 진입 잠금에 쓰는 `processing_lag_p95_ms`는 실행 경로인 depth·trade만 산정한다. 1초 wide scan age는 `wide_lag_p95_ms`로 별도 표시한다.
3. sequence가 없는 wide event ID는 거래소 event time과 수신 monotonic nanosecond를 결합해 수신 단위로 고유하게 만든다.
4. SQLite canonical JSON·checksum·transaction·fsync는 500건 bounded batch를 `asyncio.to_thread` worker에서 실행한다. 저장 실패는 종전처럼 fail-closed하되 동일 실패 배치를 이벤트 루프에서 무한 재시도하지 않는다.
5. 대시보드 WebSocket은 연결된 브라우저 수와 무관하게 0.5초마다 snapshot과 JSON을 한 번만 생성해 broadcast한다.
6. Lightweight Charts는 component mount/data-ready 전환에서만 생성하고, 이후에는 series·marker·price line만 갱신한다. chart panel은 scanner와 stretch하지 않고 viewport 기반 360~560px 높이를 쓴다.
7. 차트 시간축, event log, replay, 시스템 시각은 모두 `Asia/Seoul` KST로 표시하고 서버 snapshot과 UI 수신 시각 차이를 시스템 화면에 보여준다.
8. LIVE 대시보드는 SQLite writer의 WAL checkpoint를 기다리지 않는다. Run 시작 시 이전 LIVE 거래를 불변 메모리 cache로 읽고, 이후 화면 snapshot은 cache와 현재 Run의 메모리 거래만 결합한다. 거래 원장 API와 replay는 계속 SQLite를 직접 읽는다.

## Safety impact

실제 주문·private API·인증 경로는 추가하지 않았다. depth·trade p95가 1,500ms를 초과하면 기존과 같이 PAPER 신규 진입을 잠그고, 저장 실패도 포지션 열기를 fail-closed한다. wide 1초 스캔 age가 실행 호가 지연을 가리거나 가짜 진입 잠금을 만들지 않는다.

## Validation

- backend 96 tests PASS. persistence worker, wide/execution lag 격리, 다중 WebSocket client broadcast, LIVE dashboard의 SQLite writer lock 비의존 회귀검사를 포함한다.
- frontend Vitest 3 tests, ESLint, TypeScript, Vite build PASS. KST 변환 결정론 test를 포함한다.
- 실제 Binance LIVE `run-ef96cc96a072`에서 wide 50·deep 10, queue/gap/drop/persistence fault 0, 저장 batch 진행 중 실행 경로 p95 0~1,224ms를 확인했다.
- 최종 `run-b74c8bad6fca`를 625.957초 실행해 129,849 events와 604 candles를 처리했다. 38회 화면 API 표본은 전부 HTTP 200, 최대 120.584ms였고 최종 실행 경로 p95 71ms, queue/reconnect/gap/drop/persistence fault 0이었다.
