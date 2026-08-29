# ADR-094. Wave34 고정시간 수익률과 현재 PAPER 포지션 재생의 증거 경계

## 상태

Accepted for evidence interpretation only.

## 문제

`WAVE34_EXISTING_STRATEGY_RESEARCH.json`은 13개 저장 Run의 실제 bid·ask를 이용해 10개 런타임 전략 신호를 15·30·60·180초 뒤에 평가했다. 그러나 이 연구는 신호 후 정해진 시간이 되면 첫 호가로 강제 가격 수익률을 계산한다. 실제 PAPER 진입의 수량·최대손실·TP1·TP2·SL, 부분청산, 손익분기 손절, 근거감쇠, 최대보유와 복구 상태머신은 통과하지 않는다.

따라서 Wave34의 `15초` 행은 15초에 PAPER 포지션을 종료했다는 증거가 아니다. 역으로 Wave34의 성과만으로 현재 포지션 관리가 정상이라고 증명할 수도 없다.

## 확인한 사실

- Wave34 manifest는 `EXECUTED`이고 13개 Run, 10개 런타임 전략, 4개 고정 horizon을 포함한다.
- 진입은 LONG의 실제 ask, SHORT의 실제 bid를 쓴다. 종료는 horizon 도달 후 첫 frame의 LONG bid 또는 SHORT ask를 쓴다.
- BASE는 왕복 13bps, STRESS는 25bps를 일괄 차감했다.
- 시간순 Train 6개, Validation 2개, Final OOS 5개 Run을 이미 열어 계산했다.
- OOS BASE 표본 30건 이상이 나온 런타임 전략은 `QUEUE_MICROPRICE_MOMENTUM_V1`과 `DEPTH_ADJUSTED_OFI_IMPULSE_V1`이다. 두 전략 모두 모든 해당 horizon에서 BASE·STRESS 기대값이 음수였고 Profit Factor가 1 미만이었다.
- 나머지 런타임 전략은 OOS 표본이 0~10건이어서 수익성을 판정할 수 없다.
- Wave34의 운영 성능 측정은 `NOT_RUN`이다.
- Wave34는 현재 11번째 전략인 `HOURLY_MOMENTUM_BREAKOUT_V1`을 포함하지 않는다.

## 결정

1. Wave34는 “신호 발생 후 고정시간 가격 반응”의 과거 반증·탐색 증거로만 유지한다.
2. Wave34의 Final OOS를 봤으므로 그 결과를 근거로 신호·비용·TP·SL을 바꾼 뒤 같은 OOS를 독립 OOS로 재사용하지 않는다.
3. 현재 전략의 판정은 `research_runtime_strategy_replay.py --all-strategies --verify-archive-bytes`로 수행한 동일 런타임 경로의 PAPER 진입·체결·TP·SL·종료 재생을 기준으로 한다.
4. 신규 전략·파라미터 탐색은 사전등록한 새 데이터셋 버전에서 Train·Validation만 쓴다. 다음 Final OOS와 사전등록 이후 `FORWARD_LIVE_PUBLIC`은 조정에 사용하지 않는다.
5. 70% 목표는 BASE·STRESS 각각 30개 이상의 중복 제거 기회, 양의 기대값·순손익, Profit Factor 1 초과, bootstrap 하한·DSR·PBO·drawdown·종목·Run·레짐 분산과 독립 forward를 동시에 통과할 때만 성과 게이트로 인정한다.

## 현재 판정

- Wave34 고정시간 과거 연구는 `EXECUTED` 기록이다.
- 현재 11전략·22계좌의 종단 간 TP·SL PAPER 수익성은 `NOT_PROVEN`이다.
- Wave34만으로 15초 비정상 종료를 증명하거나 반박할 수 없다. 현재 버전의 실제 진입·종료 감사경로와 동일 입력 재생을 따로 확인해야 한다.
