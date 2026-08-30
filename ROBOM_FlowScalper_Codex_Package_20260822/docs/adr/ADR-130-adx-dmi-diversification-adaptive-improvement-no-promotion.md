# ADR-130. ADX·DMI 적응 개선 관찰과 무승격 결정

- 상태. `ACCEPTED`.
- 결정일. 2026-08-30.
- 범위. HYP-133 적응 진단 판정과 독립 외부복제 경계.
- 제외. 실제 주문, private API, API Key, 실자금 승격, 같은 Bybit 결과에 맞춘 임계값 조정.

## 맥락

HYP-132의 네 비대칭 추세 후보는 Bybit에서 양의 평균과 왜도를 보였지만 bootstrap·DSR·
시간순 안정성 또는 종목집중 gate를 실패했다. 이 결과를 본 뒤 HYP-133은 ADX 25,
3개 완성봉 동안 상승하는 ADX, 방향 일치 DMI와 같은 종목 168시간 재진입 제한을
commit `b8dd147`에서 사전등록했다.

고정 실행에서 OBV 이동평균 교차 ATR3의 STRESS 기대값은 +1.213에서 +10.011 계좌 bp,
PF는 1.054에서 1.480으로 높아졌고, 수축돌파 ATR4의 ETH 중심 양의 기여 54.7%는 전체
종목 최대 36.9%로 내려갔다. 그러나 같은 필터가 첫 눌림과 가격돌파 후보의 기대값·PF를
악화시켰다. 개선된 두 후보도 bootstrap 하한, DSR, 최소표본 또는 시간순 fold를 실패했다.

## 결정

1. HYP-133의 Registry·LIVE SHADOW 승격은 0으로 유지한다.
2. 같은 Bybit 결과에서 높아진 평균·PF는 `ADAPTIVE_DEVELOPMENT_DIAGNOSTIC`로만 기록한다.
3. ADX 25, 3봉 상승, DMI 방향과 168시간 제한을 같은 Bybit 표본에서 다시 조정하지 않는다.
4. 네 후보를 전부 변경 없이 아직 열지 않은 OKX 공개 perpetual 완성 4시간봉·펀딩에 복제한다.
5. OKX 결과 전 후보·기간·비용·pagination·완성봉·gap·시간순 gate를 별도 사전등록한다.
6. OKX가 통과해도 실제 bid·ask BASE·STRESS 미래 SHADOW 자연표본 30건 전에는 승격하지 않는다.
7. 실제 주문, private API, API Key, secret, wallet, 입출금과 runtime AI 주문판단은 계속 0이다.

## 근거

- `evidence/WAVE136_ADX_DMI_DIVERSIFIED_ASYMMETRIC_RUNNER.json`.
- `evidence/WAVE136_ADX_DMI_DIVERSIFIED_ASYMMETRIC_RUNNER_QA.json`.
- `backend/tests/test_adx_dmi_diversified_asymmetric_runner.py`.
- `docs/research/HYP-133-adx-dmi-diversified-asymmetric-runner.md`.

현 상태는
`ADX_DMI_ADAPTIVE_IMPROVEMENT_OBSERVED_ROBUSTNESS_FAILED_NO_PROMOTION`이다.
