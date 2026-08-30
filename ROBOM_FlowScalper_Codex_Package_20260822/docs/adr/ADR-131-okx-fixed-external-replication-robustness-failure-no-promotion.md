# ADR-131. OKX 고정 외부복제의 강건성 실패와 무승격 결정

- 상태. `ACCEPTED`.
- 결정일. 2026-08-31.
- 범위. HYP-134 OKX 고정 외부 venue 복제의 판정과 다음 연구 경계.
- 제외. 실제 주문, private API, API Key, 실자금 승격, OKX 결과를 본 뒤의 임계값 조정.

## 맥락

HYP-133은 Bybit 결과를 본 뒤 ADX 25 이상, 3개 완성봉 동안 상승하는 ADX,
방향 일치 DMI와 같은 종목 168시간 재진입 제한을 네 비대칭 추세 runner에 적용한
적응 진단이었다. HYP-134는 이 네 규칙을 바꾸지 않고 아직 사용하지 않은 OKX USDT
perpetual 공개자료에 복제하도록 commit `68c3c3e`에서 결과 전에 고정했다.

실행에는 2023-07-01 이상 2026-08-30 미만의 12종목 완성 4시간봉 83,232개와 실제
펀딩 41,645개를 사용했다. 종목별 봉 gap은 모두 0이고 데이터셋 SHA-256은
`5ab722bb91f0b70aa2fd64c98ef70b73f2be1a46eabc4643ca17b4e0b92841c4`다.

네 후보 모두 BASE·STRESS 비용 후 평균과 Profit Factor가 양수였고, STRESS payoff는
1.678~2.882, 최대 승자는 7.832R~14.899R이었다. 이는 여러 작은 손실을 감수하고
드문 큰 추세를 길게 보유하는 형태가 OKX에서도 나타났다는 관찰이다. 그러나 bootstrap
95% 기대값 하한은 모두 음수이고 DSR은 모두 0이었다. 네 후보 모두 시간순 안정성도
실패했고 세 후보는 최소 100건 표본도 채우지 못했다.

## 결정

1. HYP-134의 외부 venue 복제 통과 후보는 0으로 판정한다.
2. 네 후보의 Registry·LIVE SHADOW·CHALLENGER·ACTIVE 변경은 0으로 유지한다.
3. 양의 평균·PF·큰 승자를 미래 수익성이나 실자금 준비로 해석하지 않는다.
4. OKX 결과를 본 뒤 ADX·DMI·cooldown·Chandelier 값을 같은 자료에 다시 맞추지 않는다.
5. 네 결과와 실패 gate는 삭제하지 않고 전략 연구 이력에 보존한다.
6. 후속 후보는 별도 사전등록과 독립 미래자료를 사용하며, 실제 bid·ask depth,
   BASE·STRESS 비용과 전략별 자연표본 30건 전에는 승격하지 않는다.
7. 현재 LIVE PAPER의 적은 익절을 늘리기 위해 진입 기준이나 비용 가정을 낮추지 않는다.
8. 실제 주문, private API, API Key, secret, wallet, 입출금과 runtime AI 주문판단은 계속 0이다.

## 근거

- `docs/research/HYP-134-okx-adx-dmi-asymmetric-runner-external-replication.md`.
- `evidence/WAVE138_OKX_ADX_DMI_ASYMMETRIC_RUNNER_EXTERNAL_REPLICATION.json`.
- `evidence/WAVE138_OKX_ADX_DMI_ASYMMETRIC_RUNNER_EXTERNAL_REPLICATION_QA.json`.
- `backend/tests/test_adx_dmi_asymmetric_runner_okx_replication.py`.

현 상태는
`FIXED_OKX_EXTERNAL_REPLICATION_POSITIVE_SKEW_OBSERVED_ROBUSTNESS_FAILED_NO_PROMOTION`이다.
