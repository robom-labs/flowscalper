# ADR-091. 전략리그 OOS 강건성 증거 계산

## 상태

Accepted for research execution only.

## 문제

전 전략 단일 패스 리플레이는 전략별 완료 거래와 BASE·STRESS 성과를 만들었지만 시간순 최종 OOS, bootstrap 기대값 하한, DSR, PBO와 drawdown을 계산하지 않았다. 따라서 70% 관측승률이나 양수 손익이 나와도 강건성 blocker를 실제 계산 결과로 바꿀 수 없었다. 반대로 계산하지 않은 항목을 실패값 0으로 대체하면 표본 부족과 실제 실패를 구분할 수 없다.

## 결정

`research_runtime_strategy_replay.py --all-strategies` 결과 안에서 등록 전략 전체를 동일한 trial 집합으로 유지하고 다음 증거를 계산한다.

- Train 6개와 Validation 2개 Run을 시간순 8개 fold로 사용한다.
- 각 전략·BASE/STRESS·Run의 완료 거래 net bps 평균을 fold 점수로 사용한다. 거래가 없는 사전등록 전략·Run도 trial에서 숨기지 않고 0bp로 남긴다.
- 11개 등록 전략 전체의 fold 배열로 BASE와 STRESS PBO를 별도 계산한다.
- 고정된 Final OOS 5개 Run에서 전략별 BASE와 STRESS 완료 거래의 비연환산 net bps를 사용해 deterministic bootstrap 95% 구간과 DSR을 계산한다.
- BASE·STRESS 한 쌍은 `(run_id, signal_event_id, strategy_id, side)` 기준 하나의 시장기회로 세며 30개 미만에는 순위를 만들지 않는다.
- 각 프로필은 30건, 승률 70% 이상, 양의 비용후 기대값과 순손익, Profit Factor 1 초과, bootstrap 하한 양수, DSR 0.95 이상을 모두 요구한다.
- PBO는 BASE·STRESS 각각 0.20 이하를 요구한다.
- 전략리그 PAPER 1,000 USDT 계좌의 8% drawdown lock인 80 USDT를 OOS drawdown 상한으로 사용한다.
- Final OOS의 열린 포지션이나 대기 진입은 강제청산하지 않고 censored blocker로 남긴다.

과거 OOS 계산이 통과하더라도 파라미터 강건성, 종목·레짐 집중도와 사전등록 이후 독립 `FORWARD_LIVE_PUBLIC`이 남으므로 `ranking_eligible`은 자동으로 참이 되지 않는다. 실제 주문, 인증, private API와 원장은 연결하지 않는다.

## 결과 해석 경계

PBO와 DSR은 선택편향과 표본 불확실성을 줄여 보는 통계량이지 미래 수익 보장이 아니다. Final OOS 결과를 본 뒤 신호, 비용, TP·SL 또는 gate를 바꾸면 같은 Final OOS를 다시 독립 표본이라고 부를 수 없다. 결과가 낮아도 거래기록을 삭제하지 않고 충분한 표본과 Governor 계약에 따라 RETIRED·OFF 여부를 판단한다.

## 검증

- 강건한 합성 전략과 약한 합성 전략을 같은 8개 선택 fold·5개 OOS Run에 넣어 기회 중복제거, BASE·STRESS PBO, bootstrap, DSR, drawdown과 순위 차단을 함께 검증했다.
- 실제 저장 `RUN-B987D1D386C6` 앞 1,000개 이벤트 smoke는 11전략·22계좌, 실제 주문·인증 0과 `INCOMPLETE_REQUIRED_RUNS`를 확인했다. 단일 Run이므로 PBO·OOS를 PASS로 만들지 않았다.
- 관련 연구·전략·원장 회귀 189개, Ruff, mypy와 diff 검사가 통과했다.
- 동결 13-Run 전체 결과는 Wave104 6시간 관찰이 끝난 뒤 byte 재검증 후 실행한다. 실행 전까지 실제 전략 수익성은 `NOT_PROVEN`이다.
