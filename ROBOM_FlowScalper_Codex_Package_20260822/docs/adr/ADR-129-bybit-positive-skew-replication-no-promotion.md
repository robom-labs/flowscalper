# ADR-129. Bybit 양의 비대칭 복제와 무승격 결정

- 상태. `ACCEPTED`.
- 결정일. 2026-08-30.
- 범위. HYP-132 외부 venue 복제 판정과 다음 가설 경계.
- 제외. 실제 주문, private API, API Key, 실자금 승격, Bybit 결과 후 같은
  Bybit 표본에 맞춘 재선택.

## 맥락

HYP-131의 Binance 시간순 선발 후보 네 개를 commit `86f2c92`에서 고정했다. 그런
뒤 Bybit linear 12종목의 완성 4시간봉 141,422개와 공개 펀딩 71,609개에 진입,
최초 구조손절, +1R 활성화, 이전 완성 22봉 Chandelier ATR 3·4배, 비용과 위험
규칙을 변경 없이 복제했다.

네 후보 모두 다른 venue에서도 BASE·STRESS 기대값, 수익분포 왜도와 최대 승자가
양수였다. 즉 작은 손실을 되풀이하다 드문 큰 추세를 길게 보유하는 형태는 재현됐다.

그러나 모두 bootstrap 95% 하한과 DSR을 실패했다. 대표 수축돌파 ATR4는 203건,
STRESS 기대값 +7.546 계좌 bp·PF 1.333·payoff 2.137·최대 15.414R이었지만 시간순
양수 fold는 요구치 5개보다 적은 4개였고 양의 종목 기여 54.7%가 ETHUSDT였다.

## 결정

1. HYP-132의 Registry·LIVE SHADOW 승격은 0으로 유지한다.
2. 양수 평균, 낮은 승률·높은 payoff, 15.414R 승자를 수익성 증명으로 표현하지
   않는다. 현재 상태는 `NOT_PROVEN`, `NOT_READY`다.
3. Bybit 결과를 본 뒤 같은 Bybit 표본에서 ADX, breadth, ATR 배수를 재탐색해 승자를
   만들지 않는다.
4. 후속 가설은 두 실패원인을 같이 다룬다. 횡보시 진입을 줄이기 위한 사전등록
   ADX 상승·DMI 방향 확인과 단일 종목 기여를 억지로 삭제하지 않는 포트폴리오 분산
   규칙을 결과 전에 고정한다.
5. 후속 규칙은 Binance·Bybit를 개발표본으로만 쓰고, 아직 열지 않은 다른 공개
   venue 또는 실제 bid·ask 미래 SHADOW에서 외부 검증한다.
6. 실제 주문, private API, API Key, secret, wallet, 입출금과 runtime AI 주문판단은
   계속 0이다.

## 근거

- `evidence/WAVE134_BYBIT_ASYMMETRIC_RUNNER_EXTERNAL_REPLICATION.json`.
- `evidence/WAVE134_BYBIT_ASYMMETRIC_RUNNER_EXTERNAL_REPLICATION_QA.json`.
- `evidence/WAVE134_BYBIT_EXTERNAL_REPLICATION_LIVE_GUARD_300S.json`.
- `backend/tests/test_asymmetric_trend_runner_bybit_replication.py`.
- `docs/research/HYP-132-bybit-asymmetric-runner-external-replication.md`.

현 상태는
`BYBIT_POSITIVE_SKEW_REPLICATED_UNCERTAINTY_AND_CONCENTRATION_FAILED_NO_PROMOTION`이다.
