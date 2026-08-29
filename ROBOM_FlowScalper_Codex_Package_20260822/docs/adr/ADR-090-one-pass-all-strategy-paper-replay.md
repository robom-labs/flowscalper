# ADR-090. 전 전략 단일 패스 PAPER 연구 리플레이

## 상태

Accepted for research execution only.

## 문제

운영 런타임은 Registry의 11개 전략을 같은 공개시장 입력에서 평가하고 전략별 BASE·STRESS 22개 독립계좌를 유지한다. 기존 실제 PAPER lifecycle 연구 도구는 한 전략만 SHADOW로 켜고 나머지는 OFF로 바꾼 신규 런타임을 만들었다. 11개 전략을 각각 재생하면 같은 archive를 11번 읽어야 하며, 한 번의 동일 입력에서 나온 리그 상태와 미완료 포지션을 함께 비교하기 어렵다.

## 결정

`research_runtime_strategy_replay.py --all-strategies`는 각 저장 Run을 한 번만 수신순으로 읽고 다음 경로를 함께 실행한다.

- Registry에 등록된 전략 11개를 신규 무원장 REPLAY 런타임 안에서만 임시 SHADOW로 설정한다.
- 모든 전략의 LONG·SHORT를 같은 FeatureSnapshot과 Regime에서 평가한다.
- 전략마다 BASE·STRESS 독립계좌를 사용해 총 22개 계좌를 유지한다.
- 후보, 실제 bid·ask 체결, 수수료·슬리피지, TP1·TP2·SL, 관리청산과 미완료 포지션을 운영 PAPER 경로로 처리한다.
- archive 끝의 열린 포지션은 수익이나 손실로 강제 종료하지 않고 censored 상태로 보존한다.
- 각 전략의 완료 거래만 해당 전략 요약에 포함하고 다른 전략의 거래를 섞지 않는다.
- 결과에 원래 Registry mode·lifecycle과 연구 중 임시 mode를 모두 기록한다.

퇴역 전략의 임시 SHADOW 전환은 해당 메모리 내 REPLAY에서만 유효하다. 로컬 원장, 운영 설정 revision, LIVE_PUBLIC 서비스와 GitHub 기록은 변경하지 않는다. 실제 주문, 인증, private API, API Key, secret과 wallet 경로는 없다.

## 결과 해석

단일 패스 결과는 계산 효율과 동일 입력 계약을 제공하지만 수익성을 자동 증명하지 않는다. 전략별 중복 제거 시장기회 30개, BASE·STRESS 각각 30건, 양쪽 70% 이상 관측승률, 양의 비용후 기대값·순손익, Profit Factor 1 초과와 모든 OOS·bootstrap·DSR·PBO·drawdown·독립 forward gate를 통과하기 전에는 순위를 만들지 않는다. 낮은 승률 전략도 30건 전에는 표본 부족으로 유지하며, 충분한 실패 증거가 생기면 불변 거래를 남긴 채 RETIRED·OFF로 전환한다.

## 검증

- 관련 전략·Registry·22계좌·포트폴리오 회귀검사 199개가 통과했다.
- Ruff, mypy와 diff 검사가 통과했다.
- 실제 저장 `RUN-B987D1D386C6`의 앞 5,000개 이벤트 단일 패스에서 11전략·22계좌와 22개 성과행을 확인했다.
- 이 smoke는 전략 평가 34,958회, qualified 평가 15회, 중복 제거 후보 5개와 구간 끝 열린 PAPER 포지션 8개를 만들었다.
- 완료 거래는 0건이었다. 열린 포지션을 강제종료하지 않았으므로 성과와 70% 목표는 `NOT_PROVEN`이다.
- 동결 13-Run 전체 리그는 Wave104 6시간 관찰이 끝난 뒤 실행한다.

## 전략 대기 원인 진단

단순히 거래가 0건이라는 사실은 정상적인 조건 대기와 구현 결함을 구분하지 못한다. 연구 evaluator는 전략별·LONG/SHORT별 전체 평가 수, 원래 전략에서 통과한 수, 사전등록 gate 이후 통과한 수와 rejection code 빈도를 누적한다. 이 진단은 기준을 완화하지 않고 이미 계산된 결정 사유만 집계한다.

실제 저장 Run의 앞 1,000개 이벤트 진단 smoke에서 11개 전략은 각각 642회, 합계 7,062회 평가됐다. 모든 전략의 LONG·SHORT 경로가 결과에 존재했고 실제 주문·인증·원장은 0이었다. 이 구간은 통계와 레짐 warmup이 지배적이므로 거래 0건과 rejection 빈도로 전략 성과를 판단하지 않는다. 전체 동결 Run에서 warmup 이후에도 특정 필수조건이 논리상 한 번도 만족되지 않는 경우에만 구현 또는 조건계약 감사를 시작한다.
