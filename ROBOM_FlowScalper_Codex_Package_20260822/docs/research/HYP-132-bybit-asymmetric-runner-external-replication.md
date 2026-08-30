# HYP-132. Bybit 비대칭 runner 4후보 무변경 외부복제 사전등록

- 사전등록 상태. `LOCKED_BEFORE_EXECUTION`.
- 실행 상태. `NOT_RUN`.
- 등록일. 2026-08-30.
- 가설 ID. `HYP-132-BYBIT-ASYMMETRIC-RUNNER-EXTERNAL-REPLICATION`.
- 후보 지문. `8aac503aea1119d7ebe14dd9598a3ed6303d240db015d3ca71854d34a3041cb9`.
- 성과 상태. `NOT_PROVEN`.
- 실자금 준비. `NOT_READY`.

## 질문과 독립성 경계

HYP-131에서 결과 전에 고정된 60개 후보 중 Train·Validation과 walk-forward로 선발된 네
규칙을 Bybit USDT perpetual 공개자료에 파라미터 변경 없이 복제한다. Bybit 결과로 후보를
추가 선택하거나 순위를 다시 매기지 않는다.

Binance와 Bybit는 거래소·거래량·펀딩·가격경로가 다르지만 같은 암호자산 시장 충격에 강하게
연결돼 있다. 따라서 외부 venue 복제는 같은 파일 재실행보다 강한 검증이지만 완전 독립 미래
시장표본은 아니다.

## 공식 공개자료 계약

- [Bybit Get Kline 공식 문서](https://bybit-exchange.github.io/docs/v5/market/kline)의 공개
  `GET /v5/market/kline`, `linear`, `240`, 페이지당 최대 1,000개를 사용한다.
- [Bybit Get Funding Rate History 공식 문서](https://bybit-exchange.github.io/docs/v5/market/history-fund-rate)의 공개 `GET /v5/market/funding/history`, `linear`, 페이지당 최대 200개를 사용한다.
- API Key, 서명, 계정, private API와 주문 endpoint를 사용하지 않는다.
- UTC `2021-01-01` 이상 `2026-08-30` 미만의 완성 4시간봉과 실제 공개 펀딩만 사용한다.
- 종목은 HYP-131과 같은 12개다. 상장 전 구간은 생성하지 않고 실제 제공 시작시각부터 사용한다.
- 종목별 봉·펀딩 SHA-256, 실제 시작·종료, 건수와 봉 gap 수를 보존한다.

## 고정 후보와 비용

다음 네 후보만 평가한다.

1. `T131_OBV_MA_CROSS_4H_BOTH_BALANCED_CHAND22_ATR3`.
2. `T131_OBV_PRICE_BREAKOUT_4H_BOTH_BALANCED_CHAND22_ATR3`.
3. `T131_SQUEEZE_BREAKOUT_4H_BOTH_BALANCED_CHAND22_ATR4`.
4. `T131_OBV_FIRST_PULLBACK_4H_BOTH_BALANCED_CHAND22_ATR4`.

진입, 최초 구조손절, +1R 활성화, 이전 완성 22봉 Chandelier ATR 3·4배, 다음 봉 시가,
동일 봉 최초손절 우선, 갭 시 더 불리한 시가, 거래당 계좌위험 40bp, 동시 2개·일 2진입,
BASE 13bp·STRESS 25bp와 실제 펀딩을 HYP-131에서 바꾸지 않는다. 고정 익절·부분익절·최대보유,
물타기·마틴게일·피라미딩은 없다.

## 외부복제 gate

후보별 전체 기간을 순위 없이 평가하고 다음을 모두 요구한다.

- 완료거래 100건 이상.
- BASE·STRESS 기대값 양수.
- BASE PF 1.15 이상, STRESS PF 1.05 이상.
- STRESS payoff 1.50 이상, 수익분포 왜도 양수, 최대 승자 3R 이상.
- bootstrap 95% 기대값 하한 양수, 4개 시험을 반영한 DSR 0.95 이상.
- 한 종목의 양의 기여 50% 이하.
- 전체 기간 8개 시간순 fold 중 표본 10건 이상인 fold 6개 이상, 비용후 양수 fold 5개 이상,
  최신 두 fold 모두 양수.

통과 후보가 있어도 Registry 변경은 0이다. 그다음 실제 bid·ask와 독립 BASE·STRESS 계좌의
미래 LIVE_PUBLIC SHADOW 자연표본 30건이 필요하다. 외부복제 실패도 삭제하지 않고 같은
후보·기간을 파일명만 바꿔 반복하지 않는다.

실제 주문, private API, API Key, secret, 인증, wallet, 입출금과 runtime AI 주문판단은 계속
0이다.
