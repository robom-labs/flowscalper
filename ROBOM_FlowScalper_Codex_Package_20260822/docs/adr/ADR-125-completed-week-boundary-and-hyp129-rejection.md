# ADR-125. 완성 주 경계 수정과 HYP-129 무승격

- 상태. `ACCEPTED`.
- 결정일. 2026-08-30.
- 범위. HYP-129 시계열 경계 결함, 수정 후 연구 판정과 후속 PAPER 연구 방향.
- 제외. 실제 주문, private API, 실자금 승격, 결과 뒤 HYP-129 임계값 변경.

## 맥락

HYP-129는 주 t 일요일 종가로 후보를 확정하고 주 t+1 월요일 시가에 진입하도록 사전등록했다.
최초 구현은 `week_open + 6일`을 사용해 같은 주 일요일 시가에 들어가면서 그 일요일 종가를
신호에 포함했다. 이는 하루 미래참조다. 최초 출력은 연구결과로 채택할 수 없으므로 전부
폐기했다.

후보 30개, 비용, 위험, TP·SL, 표본과 과최적화 gate는 바꾸지 않았다. 진입 시각만
`week_open + 7일`로 고치고 신호 주 종료시각보다 진입시각이 항상 뒤인지 회귀테스트를 추가한
뒤 같은 불변 공개시장 입력에서 다시 실행했다.

수정 실행은 원신호 6,772개와 완료거래 1,330건을 만들었지만 30개 모두 최소 표본 또는
walk-forward 안정성 gate를 실패했다. 평가 가능한 fold 최댓값은 4개, 양수 fold 최댓값은
2개였고 PBO 0.6571428571은 상한 0.20을 크게 넘었다. 4주 상대모멘텀 일부의 development
양수는 Validation 14건과 fold 2개뿐이라 선발 근거가 아니다.

## 결정

1. 미래참조가 있던 최초 HYP-129 결과는 폐기 상태로만 기록하고 비교·선발·승격에 사용하지
   않는다.
2. 수정 실행의 30개 후보도 순위를 매기지 않고 Registry·PAPER SHADOW 변경을 0으로 유지한다.
3. 양수 development 후보를 Validation·fold 기준에 맞추려고 같은 ID로 임계값을 낮추거나
   기간을 재표집하지 않는다.
4. 실제 주문, private API, API Key, secret, 인증, wallet, 입출금과 runtime AI 주문판단은
   계속 0이다.
5. 후속 연구는 HYP-129를 재조정하지 않고 별도 가설 ID로 만든다. 사용자가 선호한 추세 초입과
   첫 눌림을 더 짧은 완성 봉에서 보되, 다음 봉 진입, BASE·STRESS 비용, 구조적 SL·TP,
   시간순 OOS, PBO·DSR·bootstrap과 실제 bid·ask 미래 SHADOW 경계는 유지한다.
6. 유튜브·TradingView·논문 아이디어는 후보 생성 근거일 뿐 수익성 증거가 아니다. 공개 규칙을
   코드로 명확히 재현하고 결과 전에 후보군과 선발 기준을 고정해야 한다.

## 근거

- `evidence/WAVE129_STATE_CONDITIONED_MOMENTUM_TOURNAMENT.json`.
- `evidence/WAVE129_STATE_CONDITIONED_MOMENTUM_TOURNAMENT_QA.json`.
- `backend/tests/test_state_conditioned_momentum_tournament.py`.
- `docs/research/HYP-129-up-up-state-risk-capped-momentum.md`.
- `evidence/RESEARCH_TRIAL_HISTORY.jsonl`.

현 상태는
`HYP129_EXECUTED_AFTER_LOOKAHEAD_FIX_NO_SELECTION_NO_PROMOTION_NOT_PROVEN_NOT_READY`다.
