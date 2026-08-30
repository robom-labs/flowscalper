# HYP-134. OKX ADX·DMI 비대칭 추세 runner 독립 복제 사전등록

- 사전등록 상태. `LOCKED_BEFORE_MARKET_DATA_DOWNLOAD`.
- 실행 상태. `NOT_RUN`.
- 등록일. 2026-08-30.
- 가설 ID. `HYP-134-OKX-ADX-DMI-ASYMMETRIC-RUNNER-EXTERNAL-REPLICATION`.
- 성과 상태. `NOT_PROVEN`.
- 실자금 준비. `NOT_READY`.

## 질문과 독립성 경계

HYP-133은 HYP-132 Bybit 결과를 본 뒤 만든 적응 개발 진단이었다. ADX·DMI와 168시간
동일 종목 재진입 제한으로 평균·PF와 종목 분산이 일부 개선됐지만 bootstrap 하한, DSR,
최소표본 또는 시간순 fold를 모두 통과한 후보는 없었다.

이번 질문은 다음 하나로 고정한다.

> HYP-133의 네 진입·손절·Chandelier·ADX·DMI·재진입 규칙을 하나도 바꾸지 않고,
> 아직 결과를 보지 않은 OKX USDT perpetual 공개시장에 복제했을 때도 BASE·STRESS 비용 후
> 양의 비대칭과 시간·종목 분산이 재현되는가?

이 문서는 OKX 가격·펀딩 파일을 요청하기 전에 commit한다. OKX 결과를 본 뒤 종목,
기간, ADX, DMI, 재진입 간격, ATR 배수, 비용, 표본 gate 또는 fold를 바꾸지 않는다.
결과가 좋아도 이 한 번의 bar·funding 복제로 Registry나 LIVE SHADOW에 승격하지 않는다.
실제 bid·ask 깊이를 쓰는 미래 BASE·STRESS 자연표본이 별도로 필요하다.

## 공식 공개 입력 계약

- venue는 `OKX_USDT_SWAP`으로 고정한다.
- 종목은 `BTC`, `ETH`, `BNB`, `SOL`, `XRP`, `DOGE`, `ADA`, `AVAX`, `LINK`, `DOT`,
  `LTC`, `BCH`의 `*-USDT-SWAP` 12개만 사용한다.
- 기간은 `2023-07-01T00:00:00Z` 이상 `2026-08-30T00:00:00Z` 이하의 완성 4시간봉이다.
- 캔들은 OKX 공식 공개 `GET /api/v5/market/history-candles`에서 `bar=4H`, 최대 300개,
  과거 방향 pagination으로 가져온다. `confirm=1`만 사용한다.
- 같은 timestamp 중복은 값이 완전히 같을 때만 한 번으로 합친다. 값이 충돌하면 fail한다.
- 4시간 gap은 종목별로 기록하고 합성봉, forward fill과 다른 거래소 대체값을 만들지 않는다.
- 펀딩은 OKX 공식 Historical Market Data 화면의 공개 download-link 경로에서 실제
  `fundingRate`와 `fundingTime`을 받아 사용한다. 2022-03부터 제공된다는 공식 화면 경계를
  따른다.
- 공개 download-link가 재현 가능하게 열리지 않거나 필요한 기간이 누락되면
  `BLOCKED_BY_OFFICIAL_OKX_HISTORY_ACCESS`로 종료한다. 펀딩을 0으로 치환하지 않는다.
- API Key, 로그인, private API, secret, wallet과 입출금은 사용하지 않는다.
- 수집 파일과 정규화 행별 SHA-256, 요청 파라미터, pagination 경계, 종목별 시작·종료·건수와
  gap을 보존한다.

## 변경하지 않는 네 후보

다음 HYP-133 규칙만 1:1로 복제한다.

1. `T134_OKX_OBV_MA_CROSS_4H_BOTH_BALANCED_CHAND22_ATR3_ADX25_RISE3_DMI_COOLDOWN168H`.
2. `T134_OKX_OBV_PRICE_BREAKOUT_4H_BOTH_BALANCED_CHAND22_ATR3_ADX25_RISE3_DMI_COOLDOWN168H`.
3. `T134_OKX_SQUEEZE_BREAKOUT_4H_BOTH_BALANCED_CHAND22_ATR4_ADX25_RISE3_DMI_COOLDOWN168H`.
4. `T134_OKX_OBV_FIRST_PULLBACK_4H_BOTH_BALANCED_CHAND22_ATR4_ADX25_RISE3_DMI_COOLDOWN168H`.

- DMI·ADX는 Wilder RMA 14다.
- 현재 완성 신호봉 ADX 25 이상, 3개 완성봉 전보다 상승을 요구한다.
- LONG은 `+DI > -DI`, SHORT는 `-DI > +DI`를 요구한다.
- 같은 후보의 같은 종목은 방향과 무관하게 직전 종료 뒤 168시간 재진입을 금지한다.
- HYP-133의 원 진입 점수, 다음 봉 시가 진입, 최초 구조손절, +1R 활성화 뒤 이전 완성
  22봉 Chandelier ATR 3·4배를 유지한다.
- 거래당 계좌위험 40bp, 최대 동시 2포지션, UTC 하루 최대 2진입, notional 1배 상한을 유지한다.
- BASE 왕복 13bp와 STRESS 왕복 25bp, 실제 OKX 펀딩을 유지한다.
- 고정 익절, 부분익절, 일반 근거약화, 고정 최대보유, 물타기, 마틴게일, 피라미딩과 손절 확대는 없다.
- 신호·진입·펀딩·청산은 현재 완성봉 이후 정보를 참조하지 않는다. 같은 봉에서 손절과
  trailing이 함께 가능하면 불리한 손절을 우선한다.

## 고정 판정 gate

후보별로 다음을 모두 통과해야 연구 gate 통과로 기록한다.

- 완료거래 100건 이상.
- BASE·STRESS 기대값 양수.
- BASE PF 1.15 이상, STRESS PF 1.05 이상.
- STRESS payoff 1.50 이상, 수익분포 왜도 양수, 최대 승자 3R 이상.
- bootstrap 2,000회 95% 기대값 하한 양수.
- 네 후보를 반영한 DSR 0.95 이상.
- 한 종목의 양의 기여 최대 비중 50% 이하.
- 전체 기간 8개 시간순 fold 중 표본 10건 이상 fold 6개 이상, 양수 fold 5개 이상,
  최신 두 fold 모두 양수.

PBO는 후보군 전체에서 별도로 계산해 보조 위험지표로 기록한다. 종목별, 방향별, 연도별,
종료이유별 표본과 BASE·STRESS 비용 차이도 보존한다. 통과 후보가 0이어도 실패 결과와
원자료 지문을 삭제하지 않는다.

## 종료 조건과 승격 금지

한 번의 고정 실행과 동일 cache의 결정론적 재실행으로 종료한다. 첫 결과 뒤 파라미터를
조정한 재실행은 HYP-134가 아니며 새 가설 ID와 새 사전등록이 필요하다.

HYP-134가 통과해도 상태는 `NOT_PROVEN`, `NOT_READY`다. 실제 bid·ask 깊이, 수수료와
슬리피지를 쓰는 미래 BASE·STRESS PAPER 자연표본이 전략별 최소 30건 쌓이기 전에는
승률 순위·ACTIVE 승격·실자금 사용을 금지한다. 실제 주문, private API, API Key, secret,
wallet, 입출금과 runtime AI 주문판단은 계속 0이다.
