# ADR-142. 지연된 공개 체결의 종목별 PAPER 진입 격리

- 상태. 채택·배포·실제 서비스 단기 관찰 완료. 장시간 검증은 미실행.
- 일자. 2026-09-03.
- 범위. LIVE_PUBLIC PAPER의 500ms 초과 aggregate trade 지연 처리에 적용한다.
- 수익성 영향. 없음. 전략·비용·수량·TP1·TP2·SL·체결 계약은 바꾸지 않는다.

## 배경

서비스는 `ENTRY_ENABLED`를 보존하고 공개시장 event·전략 평가를
계속 전진시켰지만, `stale_trade_symbols=1`과 `ENTRY_LOCK_DATA_HEALTH`가
오랜 시간 함께 남아 전체 신규 PAPER 진입이 멈추었다. 호가 순서 누락·실행호가
지연·저장 결함은 없었고, 정밀감시 16종목의 최신 호가는 모두
`HEALTHY`였다.

원인은 한 종목의 늦은 aggregate trade가 전역 `paused`와
`ENTRY_LOCK_DATA_HEALTH`를 설정한 뒤, 그 종목의 다음 fresh trade가 오기
전까지 잠금을 유지한 것이다. 거래가 드문 종목이거나 정밀감시에서
교체된 종목은 fresh trade를 다시 주지 않아 정상 종목도 무기한 중단했다.

## 결정

1. 500ms를 초과한 공개 trade는 이전처럼 candle·전략 피처에 넣지 않고,
   해당 종목의 `data_healthy=false`와 진입 대기만 종목별로 취소한다.
2. 다른 fresh 종목의 PAPER 진입과 전체 Run은 중단하지 않는다.
3. 실행호가 gap, 순서 누락, 임계 호가지연, consumer·queue·저장·
   복구 결함의 전역 fail-closed는 그대로 유지한다.
4. 해당 종목의 fresh trade 뒤 fresh depth가 들어오면 종목별 평가를 자동
   복구한다. 정밀감시에서 제외된 종목의 종료된 stale 상태는 회전 시
   정리한다.
5. 지연 trade가 남아 있는 동안은 수익성 승격용 전역 운영건강 증거를
   여전히 PASS로 판정하지 않는다.

## 검증 계약

- stale trade는 해당 종목 pending만 취소하고 candle·전략 입력을 차단한다.
- 다른 종목과 전체 PAPER는 `ENTRY_LOCK_DATA_HEALTH`없이 진행한다.
- 실행호가 sequence gap은 모든 영향 종목의 fresh depth 회복 전까지
  종전처럼 전역 진입을 잠그다.
- 정밀감시 교체로 빠진 stale 종목은 상태를 정리하며, 이 처리가
  실행호가·저장·복구 잠금을 해제하지는 않는다.
- 실제 서비스는 같은 Run을 보존한 채 설치하고, `RUNNING`·
  `ENTRY_ENABLED`, event·전략평가 전진, queue·fault·drop·실제주문 0을
  관찰한다. 자연 적격신호가 없으면 신규 거래는 `NOT_OBSERVED`다.

## 실제 서비스 검증

- 불변 release `6289ba27b082eb42a4734447c27a23dfc841a835`를 기존
  `run-2b7135a972dd`에 설치했다. CAS revision 111에서 안전 일시정지하고
  revision 112에서 `ENTRY_ENABLED`로 재개했다.
- 설치 후 105.7초 표본에서 `stale_trade_symbols=1`이 실제로 발생했지만
  전체 상태는 `RUNNING`·`ENTRY_ENABLED`·`paused=false`를 유지했다. 이전의
  전역 `ENTRY_LOCK_DATA_HEALTH` 재현 경로가 종목별로 격리됐음을 확인했다.
- 첫 178.8초 관찰에서 공개 event +21,881건·전략평가 +16,116건이 전진했고,
  최종 queue·persistence fault·buffer drop·event drop·비계획 reconnect는
  모두 0이었다.
- 실제 브라우저에서 완료 기회 4건·BASE/STRESS 원장 8행과 저장된
  LIVE_PUBLIC 다시보기 57건을 확인했다. BTWUSDT 다시보기 45프레임을
  재생해 진입·TP1·TP2·초기 손절·종료·KST 시각·진입 근거를 확인했다.
- 브라우저 검증 도중 외부 computer-use 작업자가 CPU 173.3%·RSS 약
  4.86GiB를 사용해 WAL checkpoint가 최대 38.473초까지 늦어지는 호스트
  경합도 보존했다. runtime은 `SAFETY_WAITING`으로 안전 전환하고 공개시장
  관찰을 계속했으며 작업자 종료 뒤 개입 없이 복구했다. 복구 표본은
  `RUNNING`·`ENTRY_ENABLED`, 처리 P95 22.925ms, trade P95 33.019ms,
  queue·fault·drop·비계획 reconnect·gap·resync 0이었다.
- 자연 적격신호와 신규 거래는 관찰되지 않아 `NOT_OBSERVED`다. 6시간·
  24시간은 `NOT_RUN`, 수익성은 `NOT_PROVEN`, 실자금 준비는 `NOT_READY`다.

기계판독 근거는
`evidence/WAVE156_SYMBOL_SCOPED_STALE_TRADE_POSTINSTALL.json`이다.
