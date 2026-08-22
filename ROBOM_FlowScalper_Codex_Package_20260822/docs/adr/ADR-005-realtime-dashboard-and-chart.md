# ADR-005. 실제 차트와 하나의 실시간 PAPER 연구 화면

- Status: Accepted
- Date: 2026-08-22
- Owners: ROBOM / Codex

## Context

0.1 화면은 단일 fixture snapshot을 그리는 관찰용 UI였다. 표시 종목과 실제 scanner 선택이 연결되지 않았고, 가격 영역도 거래소 이벤트·캔들·현재 포지션·리플레이 cursor를 한 시간축에서 설명하지 못했다. LIVE와 fixture 문구도 한 화면에서 충돌했다. v0.2는 같은 backend 원장을 바탕으로 비전문가용 일상 화면과 전문가용 진단을 함께 제공해야 한다.

## Decision

1. React 애플리케이션은 `라이브`, `전략`, `거래내역`, `리플레이`, `성과분석`, `위험관리`, `시스템` 일곱 화면을 하나의 PAPER 안전 header 아래 둔다.
2. 최초 상태는 `GET /api/dashboard`, 이후 상태는 `/ws` snapshot으로 갱신한다. 끊김·재연결 중에는 LIVE라고 표시하지 않으며, UI 조작으로 stale·sequence·storage 잠금을 우회하지 않는다.
3. 가격 시각화는 `lightweight-charts 5.2.1`을 사용한다. backend가 선택 종목과 시간구간에 맞춘 실제 candle, bid, ask, microprice를 제공하고 frontend는 entry, TP1, TP2, SL과 PAPER fill·exit marker를 같은 시간축에 표시한다.
4. LIVE scanner 점수·regime·비용·R:R·상태는 실제 A/B/C/D evaluator 결과에서만 계산한다. 화면을 채우기 위한 순환 regime, 임의 score, 확률형 TP 데이터는 사용하지 않는다.
5. 전략 제어는 backend Registry API의 ACTIVE·SHADOW·OFF 및 LONG·SHORT 설정을 그대로 편집한다. 전략별 BASE·STRESS shadow 계좌와 기대값·Profit Factor·비용·drawdown·표본상태를 같은 화면에서 조회한다.
6. 리플레이 화면은 저장 이벤트를 frontend에서 흉내 내지 않는다. backend ReplayEngine 실행 결과와 Run·symbol별 timeline API를 사용하고 재생·step·speed·scrub cursor가 차트·호가·이벤트 설명을 함께 이동시킨다.
7. 기본 화면은 한국어 업무 문구만 노출하고 endpoint·인증·sequence·연결 진단은 접이식 고급진단에 둔다.
8. 화면 전환은 최상단으로 이동한다. 데스크톱은 원본의 scanner·chart·position 3열을 유지하며, 태블릿·모바일에서는 의미 순서대로 쌓고 표만 내부 가로 스크롤한다. 모든 현재 조작 요소는 최소 48px 높이다.

## Safety impact

UI는 PAPER 상태를 영구 표시하고 실제 주문·private API·credential 입력을 제공하지 않는다. `OFFLINE DEMO`, `READY`, `LIVE PUBLIC`을 서로 다른 문구와 상태로 표현한다. LIVE 공개시장 데이터가 끊기면 마지막 화면은 관찰 자료일 뿐 신규 진입 허용 근거가 되지 않는다.

## Validation

- Vitest로 일곱 화면 이동 중 PAPER 안전 banner가 유지되는지 검증한다.
- Playwright로 데스크톱 1408 × 714, 태블릿 820 × 1180, 모바일 390 × 844에서 라이브 제어, 차트 선택, 전략 설정, 거래 상세, backend replay, 고급진단을 실행한다.
- 세 viewport에서 브라우저 console/page error 0건, 문서 가로 overflow 없음, visible button/select 48px 이상을 확인한다.
- 사용자 기준 화면과 구현 캡처를 같은 전체·집중 비교판으로 정규화해 `design-qa.md`에 기록한다.
