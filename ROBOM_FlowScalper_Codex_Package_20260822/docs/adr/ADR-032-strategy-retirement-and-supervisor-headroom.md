# ADR-032. 비용후 전략 중지와 공개시장 처리 여유 확보

- 상태: Accepted
- 날짜: 2026-08-25
- 범위: LIVE_PUBLIC PAPER 전략 기본상태, 저장 공개시장 검증, Binance 공개 depth·trade 전달률

## 배경

현재 구현 revision `2026-08-25-wave23`의 독립 BASE PAPER 성과에서 A는 18건 중 1승, E는 96건 중 12승, H는 20건 중 0승이었다. 기대값과 Profit Factor도 모두 비용후 음수였다. 이는 단순히 표본이 부족한 문제만이 아니라, E와 H가 예측한 30초 가격변화가 실제 bid·ask와 왕복비용을 넘지 못하는 구조적 문제였다.

같은 실제 서비스에서 provider queue가 4,096/4,096로 포화되고 drop이 270,796건까지 증가했다. 현재 실행호가 지연은 약 33ms로 회복됐지만 화면 지연은 약 12.1초로 남아 신규 PAPER 진입 안전잠금이 계속됐다. 원시 depth를 로컬 호가장에 모두 적용한 뒤에도 완성 snapshot과 aggregate trade를 소비자가 처리할 수 있는 속도보다 자주 전달한 것이 직접 원인이었다.

## 검증 방법

`scripts/research_strategy_revision.py`는 저장된 `LIVE_PUBLIC` Parquet만 사용한다. 먼저 시간순 train 8개 Run과 더 늦은 holdout 5개 Run을 분리하고, 같은 종목의 현재시각 이전 기록만 피처에 사용한다. 500ms 간격 평가, 실제 ask 진입·bid 청산 또는 그 반대, 30초 horizon, BASE 13bp와 STRESS 25bp 비용을 사용했다.

- E baseline은 train 958건에서 비용후 승률 12.735%, 기대값 -13.222bp, PF 0.124였다. holdout 188건에서는 승률 9.043%, 기대값 -14.067bp, PF 0.160이었다.
- H baseline은 train 102건과 holdout 47건 모두 비용후 승리 0건이었다. 비용전 방향 승률은 각각 54.902%, 57.447%였지만 평균 가격변화가 약 0.444bp라 비용을 넘지 못했다.
- 사전에 정한 strict 후보와 중간 cost-aware 후보는 train과 holdout 모두 자연신호 0건이었다. 결과를 본 뒤 임계값을 낮추거나 grid search를 하지 않았고 배포하지 않았다.

## 결정

1. 승률 하나를 목표로 최적화하지 않는다. 실제 bid·ask, 수수료와 슬리피지를 반영한 기대값·PF·표본수를 함께 본다.
2. A는 공동 main PAPER의 `ACTIVE`에서 독립 연구계좌만 사용하는 `SHADOW`로 내린다. 과거 거래와 계좌는 삭제하지 않는다.
3. 비용후 train·holdout 실패가 분명한 E `QUEUE_MICROPRICE_MOMENTUM_V1`과 H `DEPTH_ADJUSTED_OFI_IMPULSE_V1`은 기본 `OFF`로 둔다. 사용자가 직접 다시 켤 수 있는 제어와 과거 원장은 보존한다.
4. B만 공동 main PAPER의 `ACTIVE`로 유지한다. C/D/F/G/I/J는 `SHADOW`로 계속 엄격한 자연신호를 연구한다. 모든 전략의 LONG·SHORT 제어는 유지한다.
5. 구현 revision을 `2026-08-25-wave31`로 올리고 이전 revision 거래는 불변 원장에 보존하되 현재 기본 성과에서 분리한다.
6. Binance raw depth delta는 모두 `BinanceOrderBook`에 먼저 적용한다. 그 뒤 종목별 500ms bucket의 마지막 완성 snapshot만 전략·저장 경로로 전달하되 첫 sequence 시작과 마지막 sequence 끝을 보존한다. aggregate trade도 500ms로 합친다.
7. 실제 주문, private API, API Key, secret과 wallet 경로는 계속 0으로 유지한다.

## 결과와 한계

- 공개시장 입력률에 처리 여유가 생겨 새 Run의 queue와 drop이 정상 범위로 돌아왔다. sequence 검증과 stale fail-closed는 제거하지 않았다.
- 화면은 8개 감시, 검증 중지 2개와 문제 수를 분리해 OFF를 고장처럼 보이지 않게 한다.
- 이 결정은 손실 전략 노출을 줄이는 안전한 기본값이지 높은 승률이나 수익성을 증명한 결과가 아니다. B와 나머지 SHADOW 전략의 수익성은 충분한 현재 revision 자연표본 전까지 `NOT_PROVEN`이다.
- 10분 연속 관찰은 배포 직후 queue headroom 회귀검사일 뿐 6시간 또는 24시간 soak를 대신하지 않는다.
