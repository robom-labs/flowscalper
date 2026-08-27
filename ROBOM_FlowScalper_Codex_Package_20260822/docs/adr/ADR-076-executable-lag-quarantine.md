# ADR-076 임계 지연 실행호가의 전략 입력 격리

## 상태

Accepted.

## 배경

Wave 98 릴리스의 첫 6시간 관찰은 1,141.869초에서 실제 실행호가 지연 기준을 위반해
중단했다. 이 표본은 요청한 21,600초를 채우지 않았으므로 `PASS`가 아니라
`ABORTED_OPERATOR`다. 관찰구간에는 실행호가 지연 기준 초과 이벤트 30건과 사건 1건이
추가됐고, 최장 안전잠금은 5,037.395ms였다. queue 최대 6, 신규 local event-loop 500ms
초과 0, 비계획 재연결·sequence gap·resync·drop·persistence fault·buffer drop 0이어서 로컬
처리나 저장 실패로 분류하지 않는다.

동일 Run의 불변 Parquet 배치를 조사하니 30건은 ETHUSDT·ZECUSDT·DOGEUSDT·BTCUSDT의
`DEPTH_UPDATE`였고 실제 수신 기준 약 2,145.405ms에 걸친 교차 종목 burst였다. 지연은
1,502.087~1,717.235ms였으나 당시 이벤트의 `is_stale`은 모두 false이고 flags도 비어
있었다. supervisor는 임계 초과를 즉시 감지해 신규 PAPER 진입을 fail-close했으며
`BookSnapshot.validate()`는 stale 호가 체결을 거부할 수 있었다. 하지만 이벤트 자체가 stale로
분류되지 않아 runtime의 최신호가와 feature history에 들어가고 같은 이벤트에서 data-gap
시작점을 해제할 수 있는 경계 결함이 남아 있었다.

## 결정

1. LIVE `DEPTH_UPDATE`와 `ORDERBOOK`의 보정 지연이 supervisor의 기존
   `critical_lag_threshold_ms`를 초과하면 `is_stale=true`와
   `EXECUTABLE_LAG_STALE` flag를 붙인다. 기본 1,500ms 기준과 비교 연산은 바꾸지 않는다.
2. 지연값, event id, sequence, venue timestamp, 호가와 원본 공개시장 payload는 버리지 않고
   Run archive와 원장에 격리 사유와 함께 보존한다.
3. 격리 이벤트는 critical 사건·지연 분위수·안전잠금 집계에는 계속 포함한다. 통과한 것처럼
   숨기거나 wide scanner 지연과 섞지 않는다.
4. runtime은 stale 또는 sequence-invalid 실행호가로 최신 실행호가, PAPER 체결, feature
   history, 전략평가, 후보, 포지션 건강판정을 갱신하지 않는다. data-gap 시작점은 보존한다.
5. 같은 종목의 sequence-valid fresh 실행호가가 도착한 뒤에만 최신호가와 feature history를
   전진시키고 data-gap을 해제한다. supervisor의 기존 p95·critical 복구조건과 자동 안전복구는
   그대로 유지한다.
6. 격리 건수와 최근 종목·종류·지연·거래소시각을 한국어 고급진단에 노출한다.
7. `WIDE_TICKER`, `BOOK_TICKER`와 공개 체결의 기존 별도 지연 계약은 변경하지 않는다. 전략
   신호 임계값, TP1·TP2·SL, 비용, 위험예산, 11전략·22계좌와 실제 주문 0 경계도 변경하지
   않는다.

## 결과

- 외부 공개시장 지연 burst는 장시간 관찰 실패와 안전잠금으로 그대로 드러난다.
- 그 구간의 오래된 호가가 다음 정상 호가의 OFI·depth change·microprice·후보 판단을
  오염시키지 않는다.
- 열린 PAPER 포지션은 stale 호가에서 임의 체결되지 않고, 복구 호가에서 TP·SL 또는 별도
  emergency stale 정책을 같은 거래소 기준으로 적용한다.
- 수정 릴리스에서 실제 격리 telemetry와 복구를 확인하고 깨끗한 5분 기준선 뒤 6시간을 다시
  채워야 한다. 첫 6시간 시도와 24시간은 각각 `ABORTED_OPERATOR`, `NOT_RUN`으로 유지한다.
- 현재버전 BASE·STRESS 표본은 각각 14건으로 30건 미만이고 비용후 음수다. 이번 데이터 품질
  수정은 전략 수익성 증거가 아니며 `NOT_PROVEN`을 유지한다.
