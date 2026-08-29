# ADR-093. Registry 지원 레짐 기반 Governor 판정

## 상태

수용됨. 2026-08-29.

## 문제

Governor는 모든 SHADOW 전략에 2개 레짐, 모든 CHALLENGER의 ACTIVE 승격에 3개 레짐을 고정 요구했다. 그러나 Registry의 `VWAP_EXHAUSTION_REVERSION_V1`은 RANGE 하나만 지원하고 CBR·Aggressor 같은 추세 전략은 상승·하락 두 레짐만 지원한다. 따라서 충분한 자연표본과 비용후 증거가 있어도 설계상 존재하지 않는 레짐을 요구해 승격이 불가능했다. RANGE 전용 전략은 30건 이후 승률 70% 미달 퇴역 판정도 받지 못해 낮은 전략을 자동으로 중지한다는 정책과 충돌했다.

## 결정

- Governor는 매 평가에서 Registry descriptor의 지원 레짐 수를 읽는다.
- SHADOW·30건 퇴역 판정의 필요 레짐 수는 `min(2, 지원 레짐 수)`로 계산한다.
- ACTIVE 승격의 필요 레짐 수는 `min(3, 지원 레짐 수)`로 계산한다.
- 표본 30건·7일, 표본 100건·21일, BASE·STRESS 승률 70%, 기대값, Profit Factor, DSR, PBO, 강건성, 독립기간과 비용 gate는 변경하지 않는다.
- 지원 레짐이 여러 개인 전략이 한 레짐에서만 성과를 낸 경우는 계속 차단한다.

## 결과

단일 RANGE 전략은 RANGE 자연표본만으로 엄격한 승격·퇴역 평가를 받을 수 있다. 두 레짐을 지원하는 전략은 두 레짐을 모두 관찰해야 하며, 세 레짐을 지원하는 전략만 ACTIVE 단계에서 세 레짐을 요구한다. 이는 승격 기준을 낮추는 것이 아니라 Registry에 사전등록된 전략 정의와 판정 분모를 일치시키는 변경이다.

## 검증

- RANGE 전용 VWAP가 레짐 1개와 나머지 모든 gate를 충족하면 SHADOW에서 CHALLENGER, CHALLENGER에서 ACTIVE로 진행할 수 있다.
- RANGE 전용 VWAP가 30개 이상 BASE·STRESS·LIVE_PUBLIC 표본과 7일을 채운 뒤 한 프로필이라도 승률 70% 미만이면 RETIRED/OFF가 된다.
- 상승·하락 두 레짐을 지원하는 CBR는 레짐 1개뿐이면 `REGIME_COUNT_LT_2`로 계속 차단된다.
- 실제 주문·private API·인증·wallet 경로는 연결하지 않는다.
