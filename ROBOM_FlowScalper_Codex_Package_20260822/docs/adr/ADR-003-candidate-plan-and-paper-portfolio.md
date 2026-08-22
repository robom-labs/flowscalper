# ADR-003. 불변 CandidatePlan과 공통 PAPER 포트폴리오 엔진

- Status: Accepted
- Date: 2026-08-22
- Owners: ROBOM / Codex

## Context

전략 모듈이 실제 주문 역할까지 맡으면 신호 품질, 위험예산, 비용 가정과 체결 결과를 분리해서 검증할 수 없다. 또한 main 계좌에서 선택되지 않은 전략을 단순히 무시하면 전략별 성과를 공정하게 비교할 수 없다.

## Decision

1. 모든 적격 전략 신호는 체결 전에 frozen `CandidatePlan`으로 변환한다. plan은 예정 진입, 허용 최악 진입, 초기 stop, TP1/TP2, 수량, 최대손실, 예상 수수료·슬리피지, 순손익비, 유효시간과 관리정책을 포함한다.
2. plan이 생성된 현재 호가에서는 체결하지 않는다. BASE 250ms, STRESS 500ms 뒤 도착한 실행가능 호가에서 marketable-limit IOC로 롱은 ask, 숏은 bid를 소진한다.
3. `worst_allowed_entry` 밖의 잔량은 취소한다. 부분체결 시 실제 체결 수량만 보호하며 익절 비율도 실제 체결 수량에 다시 배분한다.
4. 구조적으로 유효한 중간 목표가 있고 비용 후 보상이 남는 경우 TP1 50%와 TP2 50%를 사용한다. VWAP exhaustion 전략은 유효한 micro-VWAP을 우선 TP1으로 사용한다. 그렇지 않으면 단일 TP1 100%를 사용한다.
5. TP1 뒤 stop을 자동 이동하지 않는다. 명시된 profit-protection 정책이 제안하는 경우에만 유리한 방향으로 조인다. 초기 stop은 절대 확대하지 않는다.
6. main 계좌는 데이터 품질, 유동성, 비용 후 순손익비, 비용부담, 만료시각과 안정적인 식별자 순으로 결정론적 중재를 하고 한 포지션만 유지한다.
7. 모든 적격 후보는 동일한 호가소진 엔진을 사용하는 전략별 BASE·STRESS shadow 계좌에서 독립적으로 진행한다.
8. 포지션은 고정 120초로 종료하지 않는다. 구조, OFI·공격체결, microprice, 유동성, spread, 데이터 건강과 남은 edge가 지속해서 악화될 때만 edge-decay 또는 profit-protection 종료를 준비한다.
9. sequence-invalid 또는 stale 구간에는 기존 TP·SL을 보존한다. 동일 거래소의 유효 데이터가 복구됐을 때 15분 안전한계를 넘은 gap은 보수적 emergency exit로 처리한다.

## Consequences

- 자연 신호가 없는 LIVE 세션은 거래 0으로 남는다. 검증을 위해 문턱을 낮추지 않고 저장한 공개시장 이벤트를 replay한다.
- TP와 SL은 last-price touch가 아니라 실행가능 반대편 호가에서 지연 후 체결된다.
- current position과 미실현 순손익은 실제 엔진 상태에서만 생성된다.
- main과 shadow 결과는 상호 오염되지 않으며 STRESS는 더 긴 지연과 높은 수수료를 독립 적용한다.

## Validation

- frozen plan 수정 거부, 위험예산 상한, 비용 후 순손익비와 TP 비율 합계를 자동검사한다.
- 249ms 무체결과 250ms BASE 체결, 500ms STRESS 체결을 검증한다.
- 실제 ask 진입, 실제 bid TP1·TP2, 부분 진입, actual-fill 보호수량, main 최대 1개를 검증한다.
- 120초 초과 건강 포지션 유지와 800ms 지속 edge decay 종료 준비를 검증한다.
- 전체 회귀, lint, typecheck, build, PAPER 안전검사와 실제 공개 Binance 무자격증명 smoke를 실행한다.
