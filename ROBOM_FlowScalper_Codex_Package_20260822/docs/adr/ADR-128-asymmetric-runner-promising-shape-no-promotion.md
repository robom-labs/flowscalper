# ADR-128. 비대칭 runner 유망 형태와 무승격 결정

- 상태. `ACCEPTED`.
- 결정일. 2026-08-30.
- 범위. HYP-131 결과 판정과 후속 외부 venue 검증 경계.
- 제외. 실제 주문, private API, 실자금 승격, 같은 역사에서 결과 뒤 파라미터 변경.

## 맥락

HYP-131은 HYP-130의 고정 진입 30개에 두 Chandelier 추적손절을 결합한 60개 후보다. 결과를
보기 전에 고정 익절·부분익절·최대보유를 제거하고, +1R 뒤 이전 완성 22봉과 ATR 3·4배로만
손절을 유리한 방향으로 이동하도록 고정했다.

네 후보가 Train·Validation과 walk-forward를 통과했다. 진단 OOS에서는 네 후보 모두
BASE·STRESS 기대값이 양수였고 최대 승자 4.63R~28.52R, 양의 수익분포 왜도를 보여 작은
손실과 드문 큰 승자라는 요청 형태가 실제 역사 자료에 나타났다.

하지만 네 후보 모두 bootstrap 기대값 하한과 DSR을 실패했고 전체 PBO는 0.80이었다. 두 후보는
양의 종목 기여가 BTCUSDT 또는 LINKUSDT에 50% 넘게 집중됐고, 한 후보는 STRESS payoff
1.50을 충족하지 못했다. HYP-130 결과를 본 뒤 같은 역사에서 청산을 바꾼 적응 연구라는 한계도
남는다.

## 결정

1. HYP-131의 Registry·PAPER SHADOW 승격은 0으로 유지한다.
2. 양의 평균, 최대 28.52R 승자 또는 낮은 승률·높은 payoff 형태를 수익성 증명으로 표현하지
   않는다.
3. 60개 후보를 같은 역사에서 추가 조정하지 않는다. ATR 3·4배와 +1R 활성화 값을 결과 뒤
   바꾸려면 새 가설·새 다중시험으로 기록한다.
4. 다음 우선 검증은 이번 네 선발 규칙을 바꾸지 않은 다른 공개 perpetual venue다. 외부 venue도
   공통 시장 충격 때문에 완전 독립표본은 아니므로 venue 복제 통과 뒤에도 실제 bid·ask 미래
   SHADOW가 필요하다.
5. 최초 동시 300초 guard의 event-loop 500ms 초과 1회는 실패로 보존한다. 연구만 겹친 분리
   150초 PASS로 전체 테스트 부하와 연구 단독 부하를 구분하며 최초 실패를 덮지 않는다.
6. 실제 주문, private API, API Key, secret, 인증, wallet, 입출금과 runtime AI 주문판단은 계속
   0이다.

## 근거

- `evidence/WAVE133_ASYMMETRIC_TREND_RUNNER_TOURNAMENT.json`.
- `evidence/WAVE133_ASYMMETRIC_TREND_RUNNER_TOURNAMENT_QA.json`.
- `evidence/WAVE133_ASYMMETRIC_TREND_RESEARCH_LIVE_GUARD_300S.json`.
- `evidence/WAVE133_ASYMMETRIC_TREND_RESEARCH_ISOLATED_GUARD_150S.json`.
- `backend/tests/test_asymmetric_trend_runner_tournament.py`.
- `docs/research/HYP-131-asymmetric-trend-runner.md`.
- `evidence/RESEARCH_TRIAL_HISTORY.jsonl`.

현 상태는
`ASYMMETRIC_SHAPE_OBSERVED_ROBUSTNESS_REJECTED_EXTERNAL_VENUE_REPLICATION_NEXT`다.
