# ADR-137. 완성봉 신호와 정밀감시 계획회전 정렬

## 상태

Implemented in source with installed-service verification pending, 2026-09-02.

## 문제

실제 설치 Run은 `RUNNING`, `ENTRY_ENABLED`, wide 80, deep 16이었고 공개시장 event와 전략평가는
계속 전진했다. 실제 주문과 인증은 false였고 queue overflow, gap, drop, persistence fault와
비계획 재연결도 원인이 아니었다. 따라서 거래 0만 보고 서버 또는 PAPER 엔진 정지로 분류할
근거는 없었다.

같은 Run의 2026-09-02 19:00:00 KST 완성봉을 공개 Binance candle로 다시 계산하면 FFUSDT의
30분 다중추세 재합류는 LONG, ADX 53.2778, 24시간 방향 모멘텀 30.7182%, 상대 거래량 2.0021로
봉 조건을 통과했고 `reason_codes`는 비어 있었다. 신호 시각은 epoch `1788343200000`이었다.

그러나 저장된 공개시장 event에서 FFUSDT의 wide ticker는 19:00:01.370부터 있었지만 최초
depth는 epoch `1788343439792`, 즉 19:03:59.792 KST였고 최초 trade도 19:04:00.296이었다.
전략의 사전등록 신호 유효시간은 5초, 동일 방향 공개 호가흐름 확인은 1초이므로 이 setup은
depth·trade 평가를 받을 수 없었다. SKRUSDT처럼 당시 이미 deep이던 종목은 19:00:00부터
depth·trade가 있었으므로 시장 전체 연결 중단도 아니었다.

원인은 provider가 각 WebSocket 연결 시작 뒤 900초를 세어 회전한 것이다. REST prepare,
snapshot과 depth warmup 시간이 반복될수록 회전 시각은 15분봉 경계에서 밀렸고, 새 정밀 종목이
완성봉 뒤에야 들어오는 경우가 생겼다.

별도로 다중추세 재합류 분기는 `_multispeed_reclaim_ready`가 true여도 결과를
`setup_confirmed`에 대입하지 않았다. 진입 evaluator는 비어 있는 rejection code를 사용하므로
실제 판단과 화면의 `진입 형태 확인`이 서로 다르게 보였다.

## 결정

1. 기본 계획회전 900초는 보정된 거래소 시각의 매 15분봉 마감 5분 전에 시작한다.
2. 연결·snapshot·depth warmup 시간도 해당 마감 전 준비시간에 포함한다.
3. 명시적으로 900초가 아닌 진단·테스트 회전시간은 기존 값 그대로 사용한다.
4. Binance primary와 Bybit fallback에 같은 계산을 적용한다.
5. wide 80, deep 16, pin, 최소 30분 체류, 회전당 최대 4개 교체와 warmup 진입잠금은 유지한다.
6. 다중추세 재합류 predicate 결과를 `setup_confirmed`에 저장한다.
7. 신호 5초, 흐름확인 1초, 비용, 실제 bid·ask, 수수료·슬리피지, 수량, TP1·TP2·SL은
   변경하지 않는다.

## 검증 경계

- 고정 phase 단위검사는 19:03→19:10, 19:09:30→19:10, 19:10→19:25,
  19:12→19:25 회전을 확인한다.
- 명시적인 0.25초 회전은 0.25초로 남는다.
- 멀티스피드 LONG·SHORT 고정 fixture는 rejection 0과 `setup_confirmed=true`를 함께 요구한다.
- 실제 설치 뒤 최소 다음 15분봉까지 deep 준비와 공개 event 전진을 관찰한다.
- 자연 적격신호가 없으면 신규 거래는 `NOT_OBSERVED`다. 이번 수정은 거래 횟수나 수익성을
  보장하지 않으며 수익성은 `NOT_PROVEN`, 실자금 준비는 `NOT_READY`다.
