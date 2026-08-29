# ADR-110. 모든 신규 PAPER 계획의 구조형 TP·SL 결판

- 상태. 채택.
- 일자. 2026-08-30.
- 범위. Strategy Registry가 만드는 신규 LIVE_PUBLIC·Replay PAPER 진입계획에 적용한다.

## 배경

기존 미시구조 전략 10개는 Registry 기본값으로 900초 최대보유와 일반 근거약화 청산을
사용했고, 시간봉 전략 1개는 36시간 최대보유를 명시했다. 최근 추가한 완성봉 추세 V2
4개만 시간청산과 일반 근거약화 청산을 사용하지 않았다. 같은 PAPER 전략리그 안에서
진입계획의 종료 계약이 서로 달라 사용자가 결과를 비교하기 어렵고, 구조가 아직 깨지지 않은
포지션이 경과시간만으로 비용을 확정할 수 있었다.

사용자는 진입 전에 손익비가 좋은 구조를 엄격히 확인하고 entry·TP1·TP2·SL·수량·최대손실을
고정한 뒤, 정상 시장에서는 익절 또는 구조 손절로 결판내기를 요청했다. 이 요청은 임의 시간
종료와 데이터·시스템 안전종료를 구분해야 한다.

## 결정

1. `StrategyDescriptor.max_hold_seconds`와 `CandidatePlanner.maximum_holding_ms`의 기본값을
   `None`으로 바꾼다.
2. 모든 현재 Registry 전략은 신규 계획에서 `maximum_holding_ms=null`을 사용한다. 시간만
   지났다는 이유로 `MAX_HOLD` 주문을 만들지 않는다.
3. 모든 현재 Registry 전략은 일반 `EDGE_DECAY` 관리청산을 기본 비활성화한다. 정상 시장의
   종료는 TP1·TP2·구조 손절과 손절의 이익보호 방향 단축으로 제한한다.
4. 진입 전에 실제 반대호가, 구조 손절, TP1·TP2, 수량, 최대계획손실, BASE·STRESS 비용과
   순손익비를 계속 고정한다. 초기 손절은 진입 뒤 넓히지 않는다.
5. stale·sequence gap·복구 실패·원장 결함·명시적 PAPER 비상종료는 전략의 임의 시간종료가
   아니므로 기존 fail-closed 안전경로를 유지한다.
6. `MAX_HOLD` enum, 원장 schema, replay와 한국어 표시 코드는 과거 불변 거래를 읽기 위해
   삭제하지 않는다. 이 결정 뒤의 새 계획이 해당 종료를 생성하지 않는지만 회귀검증한다.
7. 역사 연구의 고정 관찰구간은 과거 가설을 재현하기 위한 연구 계약으로 남길 수 있지만,
   신규 runtime PAPER 계획의 강제종료로 해석하지 않는다.
8. 실제 주문, private API, API Key, 인증, secret, wallet과 입출금 경로는 계속 0으로 유지한다.

## ADR-105와의 관계

ADR-105는 시간청산 제거를 신규 추세 V2 네 개에만 한정하고 기존 11개 전략의 일괄 변경을
기각했다. 사용자가 종료 범위를 모든 전략으로 명시적으로 확대했으므로 ADR-110이 신규
runtime 계획의 종료 정책에 한해 그 결정을 대체한다. ADR-105의 완성봉·호가확인·구조손절
계약과 실패 전략 기록 보존 결정은 그대로 유지한다.

## 검증 계약

- Registry의 모든 전략이 `max_hold_seconds=null`, `edge_decay_enabled=false`,
  `NO_TIME_EXIT` 모델을 내보내는지 확인한다.
- Planner 기본 계획과 JSON 복구 뒤 계획이 시간종료와 일반 근거약화 정책을 포함하지 않는지
  확인한다.
- 저수준 PositionManager의 과거 `MAX_HOLD` 해석과 명시적 opt-in `EDGE_DECAY` 기능은 과거
  replay 호환을 위해 별도 테스트로 유지한다.
- TP1·TP2·STOP, 이익보호 stop, stale·gap·복구 안전종료와 stop 비확대 불변조건을 다시
  검증한다.
- 전체 backend·frontend·Playwright·PAPER safety·security와 실제 설치 브라우저에서 새
  계획 표시를 확인한다.

## 한계

시간청산 제거는 승률 또는 수익성을 높였다는 증거가 아니다. 표본이 적거나 TP·SL까지 오래
걸릴 수 있고, 비용후 기대값이 음수일 수도 있다. 현재버전 30개 고유 기회, BASE·STRESS,
시간순 OOS, bootstrap 하한, DSR, PBO, drawdown과 집중도 gate 전에는 `NOT_PROVEN`, 실제
자금은 `NOT_READY`를 유지한다.
