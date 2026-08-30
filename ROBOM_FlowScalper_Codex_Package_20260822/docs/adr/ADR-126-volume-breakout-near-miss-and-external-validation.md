# ADR-126. 거래량 돌파 근접 실패와 외부 검증 분리

- 상태. `ACCEPTED`.
- 결정일. 2026-08-30.
- 범위. HYP-130 판정, 방향별 사후 차이와 다음 외부검증 경계.
- 제외. 실제 주문, private API, 실자금 승격, 결과 뒤 HYP-130 임계값 변경.

## 맥락

HYP-130은 5개 거래량 확인 추세 계열에 LONG·SHORT·BOTH와 두 강도를 적용한 30개 후보를
결과 전에 고정했다. 한 양방향 OBV·가격 동시돌파 후보가 Train·Validation과 walk-forward를
통과했고 진단 OOS 68건에서 BASE·STRESS 평균 기대값도 양수였다.

하지만 bootstrap 95% 기대값 하한은 음수였고 DSR은 0, PBO는 0.8571이었다. 후보를 여러 번
비교한 뒤 남은 양의 평균이 미래에도 반복된다고 볼 근거가 부족하다. 방향별 사후 진단에서는
SHORT가 양수이고 LONG이 음수였지만, 이는 OOS를 연 뒤 알게 된 분할이다. 같은 역사에서
SHORT만 고르면 진단 OOS를 새 Train으로 재사용하는 셈이다.

## 결정

1. HYP-130의 Registry·PAPER SHADOW 승격은 0으로 유지한다.
2. 양방향 후보의 SHORT 부분을 같은 dataset에서 승격하거나 같은 가설 ID로 임계값을 다시
   맞추지 않는다.
3. 근접 실패와 방향별 진단은 삭제하지 않고 불변 시험이력과 기계판독 증거에 보존한다.
4. 다음 검증은 후보 규칙과 비용 gate를 바꾸지 않은 다른 venue 또는 이후 시점 공개자료를
   우선한다. venue 차이 때문에 계약·가격형성 경계가 달라지면 독립 가설로 사전등록한다.
5. 유튜브·해외 사이트의 아이디어는 게시자의 승률·수익률을 가져오지 않는다. 기존 100후보와
   중복을 먼저 제거하고, 완성봉 신호·다음 봉 진입·구조 SL·TP·비용·표본·OOS 규칙을 결과
   전에 완전히 고정할 수 있는 후보만 별도 가설로 실행한다.
6. 실제 주문, private API, API Key, secret, 인증, wallet, 입출금과 runtime AI 주문판단은
   계속 0이다.

## 근거

- `evidence/WAVE131_VOLUME_CONFIRMED_EARLY_TREND_TOURNAMENT.json`.
- `evidence/WAVE131_VOLUME_CONFIRMED_EARLY_TREND_TOURNAMENT_QA.json`.
- `evidence/WAVE131_VOLUME_TREND_RESEARCH_LIVE_GUARD_180S.json`.
- `backend/tests/test_volume_confirmed_early_trend_tournament.py`.
- `docs/research/HYP-130-volume-confirmed-early-trend.md`.
- `evidence/RESEARCH_TRIAL_HISTORY.jsonl`.

현 상태는
`HYP130_NEAR_MISS_PRESERVED_POST_HOC_DIRECTION_PROMOTION_FORBIDDEN_EXTERNAL_VALIDATION_NEXT`다.
