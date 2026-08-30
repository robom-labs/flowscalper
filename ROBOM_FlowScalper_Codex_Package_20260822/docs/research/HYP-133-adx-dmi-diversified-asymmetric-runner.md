# HYP-133. ADX·DMI 확인과 종목 분산을 결합한 비대칭 추세 runner 사전등록

- 사전등록 상태. `LOCKED_BEFORE_EXECUTION`.
- 실행 상태. `NOT_RUN`.
- 등록일. 2026-08-30.
- 가설 ID. `HYP-133-ADX-DMI-DIVERSIFIED-ASYMMETRIC-RUNNER`.
- 성과 상태. `NOT_PROVEN`.
- 실자금 준비. `NOT_READY`.

## 질문과 적응 연구 경계

HYP-132의 네 후보는 다른 공개 거래소에서도 비용 후 평균과 양의 왜도는 양수였지만,
bootstrap 하한·DSR·시간순 fold와 종목집중 gate를 통과하지 못했다. 특히 가장 근접한
수축돌파 후보는 양의 종목 기여 54.7%가 ETHUSDT에 집중됐다.

이번 한 가지 질문은 다음과 같다.

> HYP-132의 네 진입·손절·Chandelier 규칙과 비용·위험을 바꾸지 않고, 완성봉에서 상승 중인
> ADX와 방향 일치 DMI를 요구하며 같은 종목 재진입을 168시간 제한하면 횡보 손실과 단일 종목
> 집중을 줄이면서 BASE·STRESS 비용 후 양의 비대칭을 유지하는가?

Binance와 Bybit 결과를 이미 보았으므로 두 자료에서의 HYP-133 결과는
`ADAPTIVE_DEVELOPMENT_DIAGNOSTIC`일 뿐 독립 외부검증이 아니다. 결과가 좋아도 Registry나
LIVE SHADOW에 승격하지 않는다. 아직 열지 않은 OKX 공개 완성 4시간봉 또는 사전등록 이후의
실제 bid·ask 미래 SHADOW 자연표본으로 별도 확인해야 한다.

## 공식 공개근거와 고정 DMI 계약

- [TradingView Directional Movement 공식 설명](https://www.tradingview.com/support/solutions/43000502250-directional-movement-dmi/)에 따라 DMI는 ADX, +DI와 -DI를 함께 사용해 추세 강도와 방향을 구분한다.
- [TradingView Average Directional Index 공식 설명](https://www.tradingview.com/support/solutions/43000589099-average-directional-index-adx/)의 일반적인 25 기준을 고정 출발점으로 사용한다. 종목별로 임계값을 재조정하지 않는다.
- +DM은 현재 고가-직전 고가가 직전 저가-현재 저가보다 크고 양수일 때만 사용한다. -DM은 반대 조건만 사용한다.
- TR, +DM, -DM과 DX는 Wilder RMA 14로 평활하고 `+DI=100*RMA(+DM)/RMA(TR)`, `-DI=100*RMA(-DM)/RMA(TR)`로 계산한다. ADX는 DX의 Wilder RMA 14다.
- 현재 완성 신호봉의 ADX가 25 이상이고 3개 완성봉 전 ADX보다 커야 한다.
- LONG은 `+DI > -DI`, SHORT는 `-DI > +DI`를 추가로 요구한다.
- 현재 봉 이후 정보, 미래 고가·저가, 다음 봉 시가와 미래 펀딩은 신호 판정에 사용하지 않는다.

## 고정 후보와 분산 규칙

다음 네 후보만 평가한다. 후보 ID는 HYP-132 규칙에 `ADX25_RISE3_DMI_COOLDOWN168H`를
붙인 새 ID로 고정한다.

1. `T133_OBV_MA_CROSS_4H_BOTH_BALANCED_CHAND22_ATR3_ADX25_RISE3_DMI_COOLDOWN168H`.
2. `T133_OBV_PRICE_BREAKOUT_4H_BOTH_BALANCED_CHAND22_ATR3_ADX25_RISE3_DMI_COOLDOWN168H`.
3. `T133_SQUEEZE_BREAKOUT_4H_BOTH_BALANCED_CHAND22_ATR4_ADX25_RISE3_DMI_COOLDOWN168H`.
4. `T133_OBV_FIRST_PULLBACK_4H_BOTH_BALANCED_CHAND22_ATR4_ADX25_RISE3_DMI_COOLDOWN168H`.

- 같은 후보 안에서 한 종목의 직전 포지션 종료 후 168시간이 지나야 그 종목에 다시 진입한다. 방향을 바꿔도 우회할 수 없다.
- 후보별 최대 동시 2포지션과 UTC 하루 최대 2진입을 유지한다.
- 원신호가 같은 시각에 겹치면 기존과 같이 점수가 높은 순서, 종목명 순서의 결정론적 규칙을 사용한다.
- 특정 종목을 사후 삭제하거나 종목별 ADX·재진입 간격·ATR 배수를 조정하지 않는다.
- HYP-132의 다음 봉 시가 진입, 최초 구조손절, +1R 이후 이전 완성 22봉 Chandelier,
  ATR 3·4배, 실제 공개 펀딩, 거래당 계좌위험 40bp, notional 1배 제한,
  BASE 왕복 13bp와 STRESS 25bp를 바꾸지 않는다.
- 고정 익절, 부분익절, 일반 근거약화 청산, 고정 최대보유시간, 물타기, 마틴게일,
  피라미딩과 손절 확대는 계속 없다.

## 진단 gate와 종료 조건

Bybit cache 재실행은 HYP-132 원후보와 HYP-133 필터 후보를 같은 완성봉·비용으로 대조한다.
후보 순위를 정하거나 통과 후보를 운영에 넣지 않는다. 다음 진단 gate를 모두 기록한다.

- 완료거래 100건 이상.
- BASE·STRESS 기대값 양수.
- BASE PF 1.15 이상, STRESS PF 1.05 이상.
- STRESS payoff 1.50 이상, 수익분포 왜도 양수, 최대 승자 3R 이상.
- bootstrap 95% 기대값 하한 양수, 네 시험을 반영한 DSR 0.95 이상.
- 한 종목의 양의 기여 50% 이하.
- 전체 기간 8개 시간순 fold 중 표본 10건 이상인 fold 6개 이상, 비용 후 양수 fold 5개 이상,
  최신 두 fold 모두 양수.
- 필터 사유별 통과·거절 수와 재진입 제한 거절 수를 별도로 보존한다.

한 번의 고정 실행과 결정론적 재실행으로 종료한다. 같은 Bybit 결과를 본 뒤 임계값을 다시
조정하지 않는다. 통과 후보가 없어도 실패 결과와 원자료 지문을 보존한다. 통과 후보가 있어도
상태는 `NOT_PROVEN`, `NOT_READY`이며 독립 OKX 또는 미래 LIVE_PUBLIC SHADOW 검증 전에는
Registry 변경이 0이다.

실제 주문, private API, API Key, secret, 인증, wallet, 입출금과 runtime AI 주문판단은 계속
0이다.

## 실행 결과

- 실행 상태. `COMPLETE_WITH_RESEARCH_GATE_FAILURE`.
- 사전등록 commit. `b8dd147bd84446b992e68d0ef7c16de5690d3d24`.
- 실행 코드 commit. `0be93e1f1d1c51a9bc1bc1081367036f8b64bbf4`.
- 입력. Bybit linear 12종목 완성 4시간봉 141,422개, 공개 펀딩 71,609개.
- 데이터셋 SHA-256. `ee992d2fd31257fad48e0c50865101985a0f68c91f042a741c70bdf674fa61bb`.
- 후보 지문. `ba7a1a7dc251854181b41f1f1595ae21dae43d7c1b26393ecaaafbce00150adb`.
- 생성시각을 제외한 재실행 SHA-256. `e6dd77476893de55c9ebc34b5d831f34f2def1f6e4a76f50e94519f7d9473875`.
- 적응 개발 진단 gate 통과. 0개.
- Registry·LIVE SHADOW 변경. 0개.

ADX 25와 3봉 상승 규칙은 네 후보의 원래 적격 신호 중 36.6%~79.6%를 제거했고, 통과한
신호에서도 168시간 재진입 제한이 후보별 22~1,314건을 추가 차단했다. 최종 완료표본은
94~358건이었다. 네 후보의 양의 종목 기여 최대 비중은 19.0%~38.7%로 모두 50% 아래였지만,
네 후보 모두 bootstrap 95% 기대값 하한과 DSR을 실패했다.

가장 개선 폭이 컸던 OBV 이동평균 교차 ATR3는 129건에서 BASE 기대값 +11.214 계좌 bp·
PF 1.557, STRESS 기대값 +10.011 계좌 bp·PF 1.480이었다. STRESS 승률은 43.4%, payoff는
1.930, 왜도는 2.135, 최대 승자는 8.286R이었다. 원후보의 STRESS 기대값 +1.213·PF
1.054보다 높았고 단일 종목 비중은 28.0%였다.

그러나 bootstrap 하한은 -1.117 계좌 bp, DSR은 0이고 최신 두 시간순 fold가 모두 양수가
아니었다. 수축돌파 ATR4도 STRESS 기대값 +13.389·PF 1.582였지만 94건으로 최소 100건에
미달했고 bootstrap 하한 -3.676, 양수 fold 2/4였다. 나머지 두 후보는 필터 뒤 성과가
악화되거나 payoff·PF·시간순 안정성을 실패했다.

따라서 같은 Bybit 자료에서 보인 개선을 미래 수익성으로 해석하지 않는다. HYP-132 결과를 본
뒤 만든 적응 진단이므로 상태는 `NOT_PROVEN`, `NOT_READY`이며, 네 규칙을 다시 바꾸지 않은
OKX 공개 자료 복제 또는 사전등록 이후 실제 bid·ask 미래 SHADOW가 다음 확인 단계다.

기계판독 결과는
`evidence/WAVE136_ADX_DMI_DIVERSIFIED_ASYMMETRIC_RUNNER.json`과
`evidence/WAVE136_ADX_DMI_DIVERSIFIED_ASYMMETRIC_RUNNER_QA.json`에 보존한다.
