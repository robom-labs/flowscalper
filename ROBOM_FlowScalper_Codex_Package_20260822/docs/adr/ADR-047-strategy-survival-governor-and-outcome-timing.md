# ADR-047. 전략 생존정책·자동 Governor와 결과 도달시간

## 상태

승인. 2026-08-26.

## 배경

현재 전략 버전의 자연 `LIVE_PUBLIC` 거래는 전략별 BASE 표본이 작고 비용후 성과가 음수다. 유일한 양수 거래나 작은 승률을 근거로 전략을 순위화하면 표본오류와 선택편향을 확대한다. 거래 빈도 자체는 2026-08-25와 2026-08-26에 각각 고유 후보 17건으로 관찰됐으므로 진입기준을 낮춰 신호를 만드는 것이 우선 문제가 아니다.

Wave 41에서 K 시간봉 가설은 같은 연구기간의 진단 OOS 일부가 양수였으나 bootstrap·DSR·PBO와 독립성 gate를 실패했다. Wave 46에서는 K의 조건을 문서와 코드로 먼저 고정한 뒤 아직 사용하지 않은 앞선 공개시장 구간을 다운로드해 재현했다. 2025-12-01부터 2026-04-26까지 147일·166건의 결과는 BASE 기대값 -18.263bp·PF 0.856, STRESS 기대값 -30.263bp·PF 0.775, bootstrap 기대값 95% 하한 -60.868bp였다. 같은 Wave의 사전등록 15분·30분 후보 4개도 개발 STRESS gate를 통과하지 못했다.

기존 거래 원장은 진입과 최종 종료 보유시간은 기록하지만, 진입 후 TP1·TP2·손절에 각각 얼마나 걸렸는지 독립적으로 보존하지 않았다. 이 때문에 전략의 결과 도달속도를 화면과 통계에서 비교할 수 없었다.

관련 연구 근거는 암호자산 수익률의 모멘텀 요인을 설명한 [NBER Risks and Returns of Cryptocurrency](https://www.nber.org/papers/w24877), 여러 후보 선택에서 과적합 확률을 다루는 [The Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253), 비용과 실행가능성을 포함한 [A Realistic Approach to Cryptocurrency Momentum Trading](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565)이다. 이 문헌은 후보를 연구할 근거이지 현재 전략의 수익성을 증명하지 않는다.

## 결정

1. 기본 공동계좌 `ACTIVE` 전략을 0개로 둔다. 미입증 B는 기존 기본 `ACTIVE`에서 독립 `SHADOW`로 이관하며 C·F·G·I·J도 `SHADOW`를 유지한다.
2. A·D·E·H·K는 `RETIRED`·`OFF`다. K의 퇴역 사유는 `FIXED_HISTORICAL_REPLICATION_FAILED_WAVE46`으로 별도 기록한다. 소스·불변 거래·BASE/STRESS 계좌와 감사 이력은 삭제하지 않는다.
3. 15분·30분 후보 4개는 Registry에 추가하지 않는다. 실패 증거와 사전등록 문서는 보존하며 기준을 사후 변경하지 않는다.
4. LIVE의 Strategy Governor는 15분마다 현재 전략 버전의 독립 `LIVE_PUBLIC` 표본을 평가한다. 새 표본이 생긴 주기만 연속 악화 횟수에 포함한다.
5. 자동 Governor는 기술 fault나 충분한 표본의 반복 비용후 악화에서 격리·강등할 수 있다. formal OOS·강건성·표본 gate가 없는 전략은 자동 승격하지 않는다. 수동 고정은 기존 충돌·감사 계약을 유지한다.
6. 신규 거래는 `tp1_hit_ts_ms`, `tp2_hit_ts_ms`, `time_to_tp1_ms`, `time_to_tp2_ms`, `time_to_stop_ms`를 PAPER 원장·복구 payload·API에 기록한다. 목표가에 최초 도달한 시각만 고정한다.
7. `time_to_stop_ms`는 실제 `STOP` 종료에만 기록한다. `EDGE_DECAY`, `PROFIT_PROTECTION`, `MAX_HOLD`, 데이터 안전종료를 손절로 오표시하지 않는다.
8. 전략 통계는 TP1·TP2·손절 각각의 표본 수와 중앙 소요시간을 분리한다. 과거 거래는 추정하지 않고 해당 필드를 `null`로 유지한다.
9. 거래 상세와 성과 화면은 진입→TP1·TP2·손절·실제 종료시간을 분리한다. 테스트·fixture 표본과 자연 `LIVE_PUBLIC` 표본을 섞어 수익성을 표시하지 않는다.

## 결과

- 사용자 화면의 기본 공동계좌에는 검증된 대표 전략이 없다고 정직하게 표시된다. 독립 SHADOW 계좌는 자연 공개시장 데이터를 계속 수집한다.
- 낮은 승률을 숨기기 위한 기준 완화나 승률만 높은 과적합 전략 채택을 방지한다.
- 거래 빈도 목표는 수익성 gate와 별개다. 하루 2~3건은 연구 목표일 뿐 보장값이 아니며, 자연신호와 비용후 강건성을 모두 통과해야 한다.
- 새 거래부터 결과 도달속도를 전략별로 비교할 수 있다. 과거 원장은 조작하지 않는다.
- 실제 주문·private API·인증·API Key·secret·wallet 경로는 계속 0이다.

## 검증 경계

단위·회귀·replay·브라우저 PASS는 구현·결정성·화면 계약을 뜻한다. 독립 과거구간의 실패는 K의 퇴역 근거지만 다른 전략의 수익성을 입증하지 않는다. 6시간·24시간 장기 실행과 미래 자연 표본은 실제 시간을 채우기 전 `NOT_RUN` 또는 `NOT_PROVEN`으로 유지한다.
