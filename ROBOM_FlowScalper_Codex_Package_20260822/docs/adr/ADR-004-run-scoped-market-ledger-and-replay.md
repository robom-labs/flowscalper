# ADR-004. Run 범위 시장원장과 동일 런타임 리플레이

- Status: Accepted
- Date: 2026-08-22
- Owners: ROBOM / Codex

## Context

기존 리플레이는 손으로 만든 상태 전이 목록의 checksum만 계산했으며, LIVE 공개시장 이벤트와 feature·regime·전략·후보·PAPER 실행 경로를 다시 처리하지 않았다. 또한 거래소 event ID는 여러 Run에서 반복될 수 있어 전역 기본키로 사용하면 두 번째 Run의 실제 이벤트가 유실될 수 있다.

## Decision

1. SQLite schema v3에서 시장 이벤트 기본키를 `(run_id, event_id)`로 둔다. v2 전역 event ID 테이블은 레코드를 보존하며 복합키로 자동 migration한다.
2. 공개 이벤트는 500개 단위 WAL 트랜잭션으로 배치 저장한다. payload canonical JSON과 SHA-256 checksum을 함께 보존하고 update·delete를 trigger로 금지한다.
3. 동일 Run의 같은 event ID가 다시 들어오면 payload checksum이 같을 때만 idempotent 성공으로 처리한다. 다르면 원장 불변조건 오류로 차단한다.
4. 완성된 캔들, 불변 CandidatePlan, 전략 설정 이력, 전략 계좌 snapshot, main 주문·체결·거래, shadow 거래와 실행 중재 로그를 별도 테이블에 저장한다.
5. 저장 이벤트 조회 시 checksum을 다시 계산한다. 불일치가 하나라도 있으면 replay를 시작하지 않는다.
6. 저장 replay는 fixture 전용 요약기가 아니라 신규 `PaperRuntime(REPLAY)`에 원래 `MarketEvent`를 순서대로 재입력한다. 동일 FeatureEngine, RegimeClassifier, A/B/C/D evaluator, CandidatePlanner와 PaperPortfolioEngine을 사용한다.
7. replay 결과는 원본 이벤트, 고정 Run config·seed·strategy version, 결정 경로와 최종 상태를 합친 SHA-256으로 식별한다. 같은 저장본을 두 번 실행하면 checksum이 같아야 한다.
8. 전략 성과는 승률만 표시하지 않는다. BASE·STRESS별 기대값, Profit Factor, payoff, 비용부담, drawdown, 보유시간, 95% 승률구간, long·short·regime·symbol과 표본상태를 함께 제공한다.
9. 성과 추천은 `유지`, `관찰`, `중지 검토`만 표시하며 Registry mode를 자동 변경하지 않는다.

## Safety impact

Replay 런타임도 PAPER 전용 불변조건을 공유하며 실제 주문과 인증을 사용할 수 없다. 저장 데이터가 없거나 checksum이 깨졌거나 Run이 다르면 추측으로 계속하지 않는다. 자연 신호가 없는 저장본은 적격후보·거래 0을 그대로 재현한다.

## Validation

- v2→v3 데이터 보존 migration, Run 간 동일 event ID, 중복 payload 불일치, update 금지를 테스트한다.
- 이벤트·캔들·후보·main 주문/체결/거래·shadow 거래·config hash 결합을 테스트한다.
- replay HTTP API와 전략 analytics API를 통합테스트한다.
- 실제 Binance 공개 Run에서 50종목 21,620개 이벤트와 53개 캔들을 저장하고 두 번 replay했다. 두 replay 모두 21,620개, 전략평가 3,224회, 적격신호·거래 0, checksum `b3eae11e3f77b9ea741197436619b8bcd3bf2c056246957d21dac14b99aab247`, 실제 주문·인증 false로 일치했다.
