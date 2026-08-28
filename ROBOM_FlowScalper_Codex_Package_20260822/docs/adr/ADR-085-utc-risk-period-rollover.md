# ADR-085. UTC 일간·주간 위험한도 기간 전환

## 상태

Accepted, 2026-08-28.

## 배경

같은 PAPER Run이 여러 날 계속된 실제 원장에서
`VWAP_EXHAUSTION_REVERSION_V1`의 BASE·STRESS 계좌는 각각 12건을 보유하고 있었다.
각 계좌의 거래는 UTC 일자별 4건·7건·1건으로 분산됐지만, 다음 UTC 일자의 자연 후보도
`MAX_DAILY_TRADES`로 거절됐다. `RiskState.daily_trade_count`, `realized_today`,
`realized_week`에 기간 시작 cursor가 없었고, 복구 때 main 계좌는 모든 Run 거래를 오늘과
이번 주 값으로 다시 넣고 있었다. 따라서 일간 12건·일간 손실·주간 손실 한도가 기간이
바뀐 뒤에도 사실상 영구 잠금이 될 수 있었다.

## 결정

1. 일간 위험기간은 UTC 00:00, 주간 위험기간은 월요일 UTC 00:00에 시작한다.
2. `RiskState`에 일간·주간 시작 millisecond cursor를 보존한다.
3. 후보 위험검사, 진입 체결 회계와 종료 회계에서 event-time으로 기간을 먼저 갱신한다.
4. 날짜가 앞으로 바뀔 때만 일간 거래수·일간 손익 또는 주간 손익을 초기화한다. 늦게 온
   과거 event가 기간을 뒤로 되돌리지는 못한다.
5. recovery snapshot 생성·복구 때 불변 완료거래와 열린 포지션의 실제 event-time으로
   현재 일간·주간 값을 다시 계산한다. 과거 snapshot에 cursor가 없어도 복구 가능하다.
6. 계좌 자산, peak equity, 전체 drawdown, 열린 위험, cooldown과 연속 손실은 누적 안전
   상태이므로 기간 전환으로 지우지 않는다.
7. 일간 12건과 기존 일간·주간 손실 임계값은 그대로 유지한다. 거래를 만들기 위한 기준
   완화가 아니며 실제 주문·인증·private API 경로도 추가하지 않는다.

## 결과

- 장기간 같은 Run을 유지해도 `DAILY`와 `WEEKLY`라는 이름과 실제 잠금기간이 일치한다.
- 재시작 직후에도 현재 기간에 속한 완료거래와 열린 포지션만 일간 거래수에 포함된다.
- 과거 거래, 전략버전, BASE·STRESS 계좌와 비용 결과는 삭제하거나 다시 쓰지 않는다.
- 이번 수정은 잠금 수명주기 결함만 고친다. 전략 수익성은 계속 `NOT_PROVEN`이고 30건
  미만 순위 금지와 Governor 승격 gate는 유지한다.
