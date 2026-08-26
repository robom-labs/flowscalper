# ADR-045. 비용인식 시간봉 추세 SHADOW와 증거기반 퇴역

## 상태

승인. 2026-08-26.

## 배경

현재 Run의 독립 `LIVE_PUBLIC` 60개 거래는 중앙 보유 23.673초였고 54건이 `EDGE_DECAY`로 종료됐다. 순손익 합계는 -63.1469 USDT, 양수 거래는 1건이었다. 전략별 표본은 아직 수익성 판단에 부족하지만 A·D·E·H는 앞선 시간순 저장시장 BASE·STRESS 검사와 이후 자연표본까지 비용후 실패 방향이 일치했다. 거래를 늘리려고 이 전략들의 진입기준이나 비용모형을 낮추는 것은 연구 무결성을 해친다.

공개 5분봉 414,720개에 대한 Wave 39 사전등록 후보 6개도 모두 BASE·STRESS 음수였다. 완성 1시간봉 Wave 41 추세 후보는 진단 OOS 42건에서 양의 기대값을 보였지만 bootstrap 하한 -48.537bp, DSR 0, PBO 0.3714였고 후보 선정과 독립된 미래 OOS가 없다.

## 결정

1. A·D·E·H는 `RETIRED`·`OFF`로 유지하고 화면에서 mode·방향 재활성화를 잠근다. 소스, 과거 거래, BASE·STRESS 계좌와 감사 이력은 삭제하지 않는다.
2. B만 공동계좌 `ACTIVE`로 유지한다. C·F·G·I·J는 독립 `SHADOW`를 유지한다.
3. `HOURLY_MOMENTUM_BREAKOUT_V1`을 K로 추가하되 독립 `SHADOW`에서만 미래 자연 `LIVE_PUBLIC` 표본을 수집한다.
4. K는 완성 1시간봉 200개 이상, EMA20/50·EMA80/200 정렬 및 EMA80 기울기, 24시간 모멘텀 2%, Donchian20 돌파, ADX 20, 상대거래량 1.1을 모두 요구한다. 새 봉 뒤 5초 안의 실제 bid·ask만 사용한다.
5. K의 TP1은 2.2R·40%, TP2는 4.5R·60%, 최대 안전보유는 36시간이다. 공통 BASE·STRESS 비용과 위험상한은 변경하지 않는다.
6. recovery schema 3은 완전히 새 strategy ID의 BASE·STRESS 두 계좌만 additive extension으로 허용한다. 기존 전략 profile 일부만 누락된 snapshot은 계속 fail-closed한다.
7. K의 연구·테스트·등록은 수익성 승격이 아니다. 미래 독립 OOS, bootstrap·DSR·PBO, 최소표본과 자연 PAPER 성과를 통과하기 전 `NOT_PROVEN`이다.

## 결과

- Registry는 11전략·22개 독립계좌가 된다.
- 엄격조건 때문에 거래가 적을 수 있으나 이는 강제 신호 생성으로 해결하지 않는다.
- 미세구조 가설과 느린 시간봉 가설을 같은 PAPER 체결·원장·성과 경계에서 비교할 수 있다.
- 긴 보유전략은 계획별 `maximum_holding_ms`를 사용하며 기존 15분 비상상한으로 오종료하지 않는다.

## 검증 경계

단위·회귀·replay·브라우저 PASS는 구현과 결정성을 뜻한다. 전략 수익성, 6시간·24시간 장시간 안정성과 미래 독립 OOS는 별도 `NOT_PROVEN` 또는 `NOT_RUN`으로 유지한다.
