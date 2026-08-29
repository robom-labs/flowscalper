# Wave 108. 전략별 고정 gate 비교 판정 사전등록

## 상태

`PRE_REGISTERED_NOT_RUN`이다. 동결 13-Run 전체 baseline이나 후보 성과를 보기 전에 비교 입력,
무결성 조건, 표본 기준과 판정 순서를 고정한다. 이 판정은 PAPER 연구 후보를 독립 미래
`LIVE_PUBLIC` SHADOW 관찰로 넘길지 결정하는 중간 단계이며, Registry 승격이나 실제 주문을
허용하지 않는다.

## 비교 단위와 baseline 재사용

- baseline은 `signal_gate=NONE`으로 11개 등록 전략과 22개 BASE·STRESS 계좌를 같은 공개시장
  이벤트에 한 번에 실행한 결과다.
- `NONE`은 어떤 전략의 신호도 바꾸지 않으므로, 같은 코드 commit, 같은 동결 manifest, 현재
  archive byte 재검증 PASS, 같은 13개 Run과 이벤트 순서를 만족하면 하나의 검증된 baseline을
  여러 target 전략 비교에 재사용할 수 있다.
- baseline의 `signal_gate_target_strategy_id`는 전역 gate 진단행을 귀속하기 위한 식별자일 뿐
  비대상 전략의 baseline 결과를 무효화하지 않는다.
- 후보는 `signal_gate=TP1_FEASIBILITY_CONFLUENCE_V1`과 target 전략 하나를 명시한 별도 실행이다.
- target이 다른 후보는 별도 가설 시험이다. 후보 표본, 승률, 기대값, PBO, DSR과 판단을 서로
  합치지 않는다.
- baseline 재사용은 반복 archive 읽기를 줄여 실행 중인 LIVE 공개시장 PAPER 경로에 주는
  부하를 줄이기 위한 계산 최적화일 뿐 기준 완화가 아니다.

## 무결성 조건

`scripts/compare_strategy_gate_trials.py`는 다음 중 하나라도 다르면 `FAIL_INTEGRITY`와
`INVALID_DO_NOT_USE`를 반환한다.

1. 두 결과의 commit, 연구범위, Registry 전략 목록·수·버전과 BASE·STRESS 계좌 수.
2. 동결 manifest 파일·내부 checksum, 13개 Run, 2,690,582개 이벤트와 현재 archive byte
   재검증 결과.
3. Run 순서, 이벤트 범위·개수·종류, source 전략설정과 PAPER 안전 상태.
4. 후보 target의 gate 적용 전 `QUALIFIED` 수와 baseline target의 같은 수.
5. 비대상 전략의 판단진단, 후보계획 수와 ID를 제외한 거래 내용.
6. 후보 gate가 기존 신호나 후보계획을 새로 만들거나 target 신호를 늘린 경우.
7. gate baseline·통과·거부 집계와 target의 실제 post-gate 집계.
8. train·validation과 최종 OOS 5개 Run의 완전성, OOS 개봉과 개봉 후 재조정 금지 표시.

무작위 실행 ID인 `candidate_id`, `trade_id`, `trailing_state_checksum`만 비대상 거래 불변 비교에서
제외한다. 가격, 수량, 비용, 손익, 보유시간, 종료사유와 시각은 제외하지 않는다.

## 고정 절대 gate

후보 target은 최종 시간순 OOS에서 다음을 모두 만족해야 한다.

- 서로 다른 시장기회 30개 이상. BASE·STRESS 한 쌍은 하나의 기회다.
- BASE와 STRESS 각각 표본 30건 이상, 승률 70% 이상.
- BASE와 STRESS 각각 비용 후 기대값과 순손익 양수.
- BASE와 STRESS 각각 Profit Factor 1 초과. 손실 없이 양수 거래만 있는 표본은 동일 gate의
  수학적 예외로 처리한다.
- bootstrap 기대값 95% 하한이 0 초과, DSR 확률 0.95 이상.
- 최대 drawdown이 전략리그 한도 이내.
- BASE·STRESS PBO gate, 종목·Run·레짐 집중도 gate 통과.
- 최종 OOS의 열린 포지션과 대기 진입 0.

비교기는 상위 `historical_*_gates_passed` 표시만 신뢰하지 않는다. 각 profile의 수치와 내장 gate,
PBO, 집중도와 censored 상태를 다시 대조한다.

## baseline 대비 gate

절대 gate를 통과한 후보도 BASE와 STRESS 각각 다음을 만족해야 한다.

- 승률이 같은 target의 공용 baseline보다 낮지 않다.
- 비용 후 기대값 bps가 baseline보다 낮지 않다.
- 최대 drawdown USDT가 baseline보다 높지 않다.

거래를 크게 줄여 우연히 높은 승률만 만든 후보는 30개 시장기회와 절대 통계 gate에서 탈락한다.
승률 70%만으로는 통과할 수 없다.

## 고정 판정 순서

1. 무결성 실패는 `INVALID_DO_NOT_USE`다.
2. 최종 OOS 시장기회 30개 미만은 `NOT_PROVEN_INSUFFICIENT_OOS_SAMPLE`이다.
3. 절대 비용·OOS·통계·집중도 gate 실패는 `REJECTED_HISTORICAL_GATES`다.
4. 절대 gate는 통과했지만 baseline보다 개선되지 않으면
   `REJECTED_NO_BASELINE_IMPROVEMENT`다.
5. 모두 통과한 경우에도 `HISTORICAL_CANDIDATE_FORWARD_SHADOW_PENDING`일 뿐이다.

모든 판정에서 `ranking_eligible=false`, `promotion_allowed=false`, 수익성 `NOT_PROVEN`을
유지한다. 마지막 상태도 새 revision의 독립 미래 `LIVE_PUBLIC` 자연표본과 기존 Governor의
SHADOW→CHALLENGER·ACTIVE gate를 추가로 통과해야 한다.

## 실행·안전 경계

- Wave 104의 실제 6시간 observer가 끝나기 전에 전체 archive baseline과 후보를 실행하지 않는다.
- 전체 실행은 `scripts/run_live_safe_strategy_league_replay.py`의 저우선순위·LIVE 자동중단
  경로만 사용한다.
- 자연 신호가 적어도 진입, 비용, 체결, TP1, TP2, SL, 조기종료와 최대보유 기준을 낮추지 않는다.
- 실제 주문, private API, 인증, API Key, secret, wallet, 입출금과 runtime AI 주문판단은 계속
  0이다.
- 6시간·24시간을 실제로 채우지 않은 관찰은 각각 `NOT_RUN`으로 남긴다.

## 사전등록 시점 검증

- 비교 판정 단위검사 8건이 통과했다.
- 비교기와 직접 연구 runner·LIVE 안전 wrapper를 합친 표적 회귀 35건이 통과했다.
- 다른 target으로 기록된 검증된 `NONE` baseline 재사용, 30건 미만, 절대 gate 요약 불일치,
  비대상 변경, target 신호·후보 증가, commit·archive 불일치를 각각 검사했다.
- Ruff, mypy 106개 source와 `git diff --check`가 통과했다.
- 동결 13-Run baseline과 후보 성과 실행은 `NOT_RUN`이고 수익성은 `NOT_PROVEN`이다.
