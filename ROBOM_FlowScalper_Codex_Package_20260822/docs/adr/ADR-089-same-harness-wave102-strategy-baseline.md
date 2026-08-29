# ADR-089. Wave102 전략 기준선의 동일 런타임 재현

## 상태

Accepted for research comparison only.

## 문제

Wave105의 VWAP 재진입 확인은 모든 진입조건이 동시에 300ms 유지될 때만 확인 시간을 누적한다. Wave102는 데이터 정상, RANGE 레짐, 구조 재진입과 microprice 방향 일치만으로 확인 시간을 먼저 누적했다. 서로 다른 커밋이나 별도 런타임에서 결과를 비교하면 이벤트 정렬, PAPER 체결, 비용, 포지션 관리 또는 통계 코드 차이가 전략 차이로 오인될 수 있다.

## 결정

운영 `StrategySignalEvaluator`의 기본 계약은 항상 `CURRENT_FULL_CONFLUENCE`로 유지한다. 연구 리플레이에서만 다음 두 값을 명시적으로 선택할 수 있다.

- `CURRENT_FULL_CONFLUENCE`는 모든 기존 VWAP 진입조건이 동시에 참일 때 확인 시간을 누적한다.
- `WAVE102_PARTIAL_CONFIRMATION_BASELINE`은 Wave102의 과거 부분 확인 조건만 정확히 재현한다.

두 모드는 같은 현재 코드의 수신순 이벤트 읽기, Strategy Registry, PAPER 후보·체결, BASE·STRESS 비용, TP·SL, 포지션 관리와 통계 경로를 사용한다. 결과 JSON은 `strategy_logic`을 최상위와 각 Run에 기록한다. 과거 방식은 명시적으로 선택하지 않으면 사용할 수 없으며 LIVE_PUBLIC 운영 설정에 연결하지 않는다.

## 비교 순서

동결 데이터에서 다음 세 결과를 별도로 만든다.

1. Wave102 기준선은 `WAVE102_PARTIAL_CONFIRMATION_BASELINE`과 `NONE` 신호 gate를 사용한다.
2. 현재 기준은 `CURRENT_FULL_CONFLUENCE`와 `NONE` 신호 gate를 사용한다.
3. 사전등록 후보는 `CURRENT_FULL_CONFLUENCE`와 `TP1_FEASIBILITY_CONFLUENCE_V1` gate를 사용한다.

각 결과는 30개 이상의 중복 제거 시장기회, BASE·STRESS 각각 30건 이상, 양의 비용 후 기대값과 순손익, Profit Factor 1 초과, 시간순 OOS, bootstrap 기대값 하한, DSR, PBO, drawdown과 독립 forward LIVE_PUBLIC 검증을 모두 통과하기 전까지 순위 또는 수익성 근거가 아니다.

## 검증 경계

- 관련 테스트 80개와 Ruff·mypy·diff 검사는 통과했다.
- 실제 저장 이벤트 5,000건씩의 짧은 smoke에서 두 모드의 표시, 수신순 PAPER 경로, 실제 주문 0, 인증 0과 무원장 연구 경계를 확인했다.
- 짧은 smoke에서는 두 모드 모두 거래 0건이었다. 수익성은 `NOT_PROVEN`이다.
- 동결 13-Run 전체 비교와 현재 archive byte 재검증은 Wave104 6시간 관찰이 끝난 뒤 실행한다.
- 이 결정은 신호 기준, 비용, TP·SL 또는 진입 수를 완화하지 않는다.
