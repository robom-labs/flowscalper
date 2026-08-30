# ADR-127. 공개 영상 후보 무선발과 중복 전략 추가 금지

- 상태. `ACCEPTED`.
- 결정일. 2026-08-30.
- 범위. HYP-118 실행 판정과 후속 해외 영상 아이디어의 채택 경계.
- 제외. 실제 주문, private API, 실자금 승격, 결과 뒤 HYP-118 임계값 변경.

## 맥락

유동성 훑기 후 복귀와 일목 추세 재개 2계열을 12개 방향·강도 후보로 고정하고, 12종목 완성
5분봉 922,752개에서 BASE 13bp·STRESS 25bp와 시간순 split을 적용했다. 12개 중
Train·Validation을 함께 통과한 후보는 없었다.

유동성 훑기 LONG 완화형은 Validation 29건에서 양수였지만 development 81건은 BASE·STRESS
모두 음수였다. 일목 후보는 표본이 부족하거나 두 구간 모두 비용 후 음수였다. PBO만 0.1429로
상한 안에 있어도 기본 성과·표본 gate 실패를 대신하지 못한다.

## 결정

1. HYP-118의 Registry·PAPER SHADOW 승격은 0으로 유지한다.
2. Validation만 양수인 유동성 훑기 후보를 같은 역사에서 재조정하거나 development 음수구간을
   제거하지 않는다.
3. 희소 일목 후보의 큰 양수 기대값을 수익성 증거로 사용하지 않는다.
4. 공개영상의 `best`, `secret`, 승률, 조회수와 게시자 수익은 입력으로 사용하지 않는다.
5. Supertrend, Donchian, breakout-retest, VWAP와 squeeze처럼 기존 F03~F20, HYP-117,
   HYP-127 또는 HYP-130과 같은 계산은 새 전략 이름으로 복제하지 않는다.
6. 기계적으로 완전한 비중복 후보만 별도 가설 ID와 결과 전 고정된 데이터·비용·OOS 계약으로
   연구한다. 다음 우선순위는 HYP-130 근접 후보의 파라미터 무변경 외부 venue 검증이다.
7. 실제 주문, private API, API Key, secret, 인증, wallet, 입출금과 runtime AI 주문판단은
   계속 0이다.

## 근거

- `evidence/WAVE132_PUBLIC_VIDEO_TREND_TOURNAMENT.json`.
- `evidence/WAVE132_PUBLIC_VIDEO_TREND_TOURNAMENT_QA.json`.
- `evidence/WAVE132_PUBLIC_VIDEO_TREND_RESEARCH_LIVE_GUARD_300S.json`.
- `backend/tests/test_public_video_trend_tournament.py`.
- `docs/research/HYP-118-public-video-trend-tournament.md`.
- `docs/research/WAVE118_YOUTUBE_TRADINGVIEW_IDEA_MAPPING_KO.md`.
- `evidence/RESEARCH_TRIAL_HISTORY.jsonl`.

현 상태는
`HYP118_PUBLIC_VIDEO_CANDIDATES_EXECUTED_NO_SELECTION_DUPLICATES_NOT_REINTRODUCED`다.
