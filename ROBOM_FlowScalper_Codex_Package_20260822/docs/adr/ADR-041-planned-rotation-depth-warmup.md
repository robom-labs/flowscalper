# ADR-041. 계획 교체 depth warmup의 stale backlog 비실행 처리

- 상태: Accepted
- 날짜: 2026-08-26
- 범위: Binance 공개 depth WebSocket 계획 교체와 실행용 top-of-book 전달

## 배경

장시간 서비스에서 15분 계획 교체 직후 임계지연 사건이 두 번 연속 각각 99.325초와 98.882초 지속됐다. 계획 교체·전체 reconnect 수는 일치했고 비계획 reconnect, sequence gap, resync, drop과 persistence fault는 0이었다.

Binance provider는 wide·depth·trade WebSocket을 먼저 열고 REST depth snapshot을 받은 뒤 순서가 맞는 depth delta를 적용한다. snapshot을 준비하는 동안 새 WebSocket에서 받은 delta가 queue에 쌓였고, 기존 구현은 snapshot 뒤 이 오래된 backlog를 실행 가능한 top-of-book 이벤트로 모두 다시 내보냈다. 이 때문에 거래소 event-time이 오래된 이벤트가 실행 지연 통계를 오염시키고 신규 PAPER 진입을 장시간 안전잠금했으며, 각 stale delta마다 `book.top(20)` 계산도 반복됐다.

## 결정

1. 연결별 depth warmup은 snapshot 직후 시작하고 첫 신선한 `DEPTH_UPDATE`를 내보낼 때 끝낸다.
2. warmup 중 1,500ms보다 오래된 depth delta는 sequence continuity를 위해 로컬 호가장에는 적용하지만 실행 이벤트로 내보내지 않는다.
3. stale delta를 적용한 뒤에는 `book.top(20)`과 전략·체결 경로를 실행하지 않는다.
4. 첫 신선한 depth 이벤트가 전달될 때까지 supervisor의 기존 계획교체 신규진입 잠금과 `RECONNECTING` 계약은 유지한다.
5. warmup이 끝난 뒤의 stale 이벤트는 기존 임계지연 fail-closed 진단을 그대로 거친다. 임계값이나 전략 진입조건은 낮추지 않는다.
6. 실제 주문, private API, 인증, API Key, secret과 wallet 경로는 추가하지 않는다.

## 검증

- stale warmup delta가 호가장의 update id를 전진시키되 이벤트를 내보내지 않고, 다음 신선한 delta가 정상 호가를 내보내는 회귀검사를 추가한다.
- 실제 Binance 공개시장에서 계획 회전을 30초로 단축해 75초 관찰하고 reconnect, 임계지연, sequence gap, resync, drop과 queue를 측정한다.
- 생산 15분 회전을 실제 서비스에서 두 번 이상 관찰해 이전의 98~99초 사건이 재발하지 않는지 확인한다.
- 전체 backend·frontend·Playwright·정적·보안·PAPER 안전검사와 실제 브라우저 시스템 화면·console을 다시 확인한다.

## 결과와 한계

단축 실제 공개시장 검증은 5,066개 전달 이벤트, 계획회전·전체 reconnect 2·2, 비계획 reconnect 0, 임계지연 사건 0, 실행경로 p95 22.286ms, sequence gap·resync·drop 0으로 끝났다. 배포 후 생산 주기 2회의 15분 계획교체와 146,510 events에서도 임계지연 사건 0, 비계획 reconnect·gap·resync·drop·fault 0, 실행경로 p95 39.409ms였다.

이 결과는 재현한 계획교체 backlog 결함의 해결 증거다. 모든 미래 네트워크 상태, 6시간·24시간 지속 실행이나 전략 수익성을 보장하지 않는다. wide scanner 지연은 실행용 정밀호가 지연과 계속 분리한다.
