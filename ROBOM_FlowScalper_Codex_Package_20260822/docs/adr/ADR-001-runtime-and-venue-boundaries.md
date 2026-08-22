# ADR-001: 페이퍼 전용 단일 프로세스와 거래소 경계

- Status: Accepted
- Date: 2026-08-22
- Owners: ROBOM / Codex

## Context

첫 릴리스는 자격 증명 없이 공개 시세를 읽고 내부 가상계좌만 변경해야 한다. Binance는 2026년 WebSocket 공개 스트림을 고빈도 Public과 일반 Market 경로로 분리했으며, Bybit public linear 토픽은 인증이 필요 없다.

## Official evidence

- Binance USDⓈ-M Connect 문서에서 `/public`과 `/market` 경로, 24시간 연결 수명, 1024 스트림 상한을 2026-08-22 재확인했다.
- Binance 로컬 호가장 문서에서 snapshot과 `U/u/pu` 연속성 절차를 재확인했다.
- Bybit V5 Connect와 Orderbook 문서에서 public linear URL과 snapshot/delta reset 규칙을 재확인했다.

## Decision

FastAPI 기반 단일 Python 프로세스와 정적 React 번들을 사용한다. 거래소 어댑터는 공개 데이터만 반환하고 Run은 정확히 한 거래소에 묶는다. 런타임 모드는 `FIXTURE_OFFLINE`, `LIVE_SHADOW_PAPER`, `REPLAY`만 정의한다.

## Safety impact

실제 주문 모드, 자격 증명 입력, 비공개 거래소 세션을 설계에서 제거한다. 공개 연결 검증 전에는 LIVE를 표시하지 않는다.

## Alternatives considered

다중 프로세스·데스크톱 래퍼는 첫 수직 슬라이스에 불필요해 보류한다. 비공식 거래소 SDK는 비공개 기능의 우발 유입을 줄이기 위해 사용하지 않는다.

## Consequences

어댑터·전략·실행 경계를 유지하면서도 로컬 설치가 단순하다. CPU 병목이 측정되면 후속 ADR로 프로세스 풀을 검토한다.

## Validation

`make test`, `make lint`, `make typecheck`, `make build`로 검증한다.

