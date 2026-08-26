# ROBOM FlowScalper v0.2 전략 카탈로그

## 공통 원칙

열한 전략은 모두 공개시장 데이터와 내부 PAPER 체결에만 사용된다. 전략은 주문 권한이 없고 거래소 계정이나 private API를 호출하지 않는다. 같은 symbol snapshot과 과거 이력만 사용하며 현재값 이후 정보를 참조하지 않는다.

| 구분 | Strategy ID | 화면 이름 | 안정성 | 주 레짐 | 핵심 확인 |
|---|---|---|---|---|---|
| A | `LSA_REVERSAL_V1` | 유동성 쓸기 반전 | EXPERIMENTAL | RANGE, TREND_UP, TREND_DOWN | 쓸기, 흡수, 호가 재충전, 범위 복귀 |
| B | `CBR_CONTINUATION_V1` | 압축 돌파 재가속 | STABLE | TREND_UP, TREND_DOWN | 압축, 돌파, 눌림, 재가속 |
| C | `VWAP_EXHAUSTION_REVERSION_V1` | VWAP 과도이탈 평균복귀 | EXPERIMENTAL | RANGE | micro-VWAP 이탈, 공격 흐름 소진, 구조 복귀 |
| D | `OFI_CONTINUATION_PULLBACK_V1` | OFI 추세 눌림 지속 | EXPERIMENTAL | TREND_UP, TREND_DOWN | 다중 OFI 정렬, 약한 역방향 눌림, 원 흐름 재가속 |
| E | `QUEUE_MICROPRICE_MOMENTUM_V1` | 호가 쏠림 순간추세 | EXPERIMENTAL | RANGE, TREND_UP, TREND_DOWN | top5·top10 호가, OFI, microprice 정렬 |
| F | `AGGRESSOR_FLOW_CONTINUATION_V1` | 강한 체결 흐름 지속 | EXPERIMENTAL | TREND_UP, TREND_DOWN | 방향성 체결금액, OFI, 가격반응 지속 |
| G | `MULTILEVEL_MICROPRICE_MOMENTUM_V1` | 다중호가 공정가 추세 | EXPERIMENTAL | RANGE, TREND_UP, TREND_DOWN | top10 공정가, OFI, 체결, 가격반응 |
| H | `DEPTH_ADJUSTED_OFI_IMPULSE_V1` | 깊이보정 OFI 충격 | EXPERIMENTAL | RANGE, TREND_UP, TREND_DOWN | 깊이보정 OFI robust z, 가격반응 |
| I | `OFI_RETURN_CONFLUENCE_V1` | OFI·단기수익률 동행 | EXPERIMENTAL | RANGE, TREND_UP, TREND_DOWN | 깊이보정 OFI와 prefix 3초 수익률 동행 |
| J | `BOOK_SLOPE_ASYMMETRY_V1` | 호가 기울기 비대칭 | EXPERIMENTAL | RANGE, TREND_UP, TREND_DOWN | top10 가격거리 대비 깊이의 방향 비대칭 |
| K | `HOURLY_MOMENTUM_BREAKOUT_V1` | 시간봉 추세 돌파 | EXPERIMENTAL | TREND_UP, TREND_DOWN | 완성 1시간봉 EMA 정렬, 24시간 모멘텀, Donchian 돌파, ADX, 상대거래량 |

## 전략 A. 유동성 쓸기 반전

가격이 구조 수준을 순간적으로 넘어선 뒤에도 공격 체결이 가격을 계속 밀지 못하는지 확인한다. 반대편 호가 재충전, OFI 반전, microprice 회복, 범위 재진입이 함께 확인돼야 한다. 단순 꼬리나 한 번의 대량 체결만으로는 진입하지 않는다.

## 전략 B. 압축 돌파 재가속

낮은 변동성과 압축 뒤 발생한 돌파를 즉시 추격하지 않는다. 초기 충격 이후 눌림이 과하지 않고, 역방향 흐름의 가격 영향이 약하며, 호가 재충전과 OFI·microprice 재정렬이 확인될 때만 후보가 된다.

## 전략 C. VWAP 과도이탈 평균복귀

범위 레짐에서 micro-VWAP로부터 과도하게 이탈했지만 공격 흐름 대비 가격 진전이 둔화되는 상황을 찾는다. 반대 호가 재충전, OFI·microprice 반전, 구조 재진입이 필요하다. 신규 실험 전략이므로 main 참여 여부와 무관하게 독립 shadow 결과를 먼저 관찰해야 한다.

## 전략 D. OFI 추세 눌림 지속

추세 레짐에서 250ms와 3초 OFI, 공격 체결, microprice가 같은 방향인지 확인한다. 짧은 역방향 눌림의 가격 충격이 약하고 원래 흐름이 재가속할 때만 후보가 된다. C와 마찬가지로 EXPERIMENTAL PAPER 전략이다.

## 전략 E. 호가 쏠림 순간추세

top5·top10 호가 불균형, 250ms·3초 OFI, 1초 체결과 microprice 변위가 500ms 이상 같은 방향일 때만 후보가 된다. 순간 호가 하나만으로 진입하지 않는다.

## 전략 F. 강한 체결 흐름 지속

방향성 체결금액의 robust z와 3초·10초 체결 흐름이 추세 레짐에서 OFI·microprice·실제 가격반응과 함께 500ms 이어지는지 확인한다.

## 전략 G. 다중호가 공정가 추세

최우선 호가만 보지 않고 top10 가격과 수량을 반영한 공정가를 계산한다. 이 공정가, 최우선 microprice, OFI, 체결과 가격반응이 750ms 정렬돼야 한다.

## 전략 H. 깊이보정 OFI 충격

3초 OFI를 top10 양방향 평균 깊이로 보정하고 이전 동일 종목 표본의 robust z와 비교한다. OFI·체결·microprice·가격반응이 함께 500ms 유지돼야 한다.

## 전략 I. OFI·단기수익률 동행

깊이보정 OFI와 직전 3초 가격수익률이 같은 방향으로 이어지는지를 별도로 검증한다. 기준가격은 현재보다 3초 이전의 가장 가까운 과거 표본만 사용하고 미래값을 보지 않는다. 1,000ms 지속과 공통 비용 gate를 통과해야 하며 기본값은 독립 SHADOW PAPER다.

## 전략 J. 호가 기울기 비대칭

top10 각 호가의 중간가격 거리와 누적 명목깊이로 매수·매도 기울기를 계산한다. LONG은 매도호가 기울기가 동일 종목 과거창의 하위 15%이고 매수호가 기울기가 중앙값 이상이며 양쪽 비율이 1.5배 이상일 때만 구조 조건을 통과한다. SHORT는 이를 대칭 적용한다. 32개 이상의 과거표본, OFI·공격체결·microprice·가격반응과 1,000ms 지속이 모두 필요하며 기본값은 독립 SHADOW PAPER다. 공식 연구는 연구가설의 근거일 뿐 수익성 증거가 아니다.

## 전략 K. 시간봉 추세 돌파

완성된 공개 1시간봉 200개 이상에서 EMA20·50과 EMA80·200의 방향 및 EMA80 기울기를 확인한다. 같은 방향의 24시간 수익률이 2% 이상이고 직전 20개 완성봉 Donchian 고가·저가를 돌파하며 ADX 20 이상, 상대거래량 1.1 이상일 때만 후보가 된다. 새 완성봉 뒤 5초 안의 실제 bid·ask만 사용한다. TP1은 2.2R에서 40%, TP2는 4.5R에서 60%이고 안전 최대보유는 36시간이다. 초기 진단의 일부 양의 구간은 bootstrap 하한·DSR·PBO와 미래 OOS를 통과하지 못했고, 이후 고정된 독립 과거구간 147일·166건에서 BASE·STRESS 모두 비용후 실패해 현재 `RETIRED·OFF`다. 소스·과거 거래·BASE·STRESS 계좌는 보존하며 수익성은 `NOT_PROVEN`이다.

## 모드와 방향 제어

현재 안전 기본값은 공동계좌 `ACTIVE` 0개, B/C/F/G/I/J `SHADOW` 6개, A/D/E/H/K `RETIRED·OFF` 5개다. 이 상태는 과거 거래를 지우지 않으며 퇴역 전략의 BASE·STRESS 계좌와 명시적인 새 연구 경로도 보존한다. 퇴역 전략은 별도 사전등록 연구와 코드 변경 없이 화면에서 다시 켤 수 없다.

| 화면 선택 | main PAPER 후보 | 독립 BASE·STRESS shadow | 평가 |
|---|---:|---:|---:|
| 실전 PAPER, `ACTIVE` | 포함 | 포함 | 실행 |
| 가상 관찰, `SHADOW` | 제외 | 포함 | 실행 |
| 끄기, `OFF` | 제외 | 제외 | 중지 |

LONG과 SHORT는 각 전략에서 별도로 허용하거나 차단한다. 설정 변경은 revision, actor, 이유와 함께 원장에 기록된다. Strategy Governor는 짧은 승률로 설정을 뒤집지 않고, 최소표본·OOS·STRESS·PBO·DSR·강건성 gate와 사용자 manual lock을 모두 확인한다. runtime은 source code나 임계값을 자동 수정하지 않는다.

A~J 런타임 descriptor는 `MICRO_SCALP`, 예상 보유 10~180초, 신호 반감기 30초, 250ms~120초 공개시장 피처와 900초 안전상한을 공개한다. K는 `INTRADAY_SWING`, 예상 보유 1~36시간, 5초 신호 반감기, 완성 1시간봉과 36시간 안전상한을 공개한다. 각 descriptor는 strategy version, 필요한 공개시장 데이터, 최소 warmup, 진입 가설·반증 조건, 비용모델, 공동·독립 PAPER 위험예산, 대상 종목·레짐, 미래정보 방지 규칙, 1차 연구 Source ID와 현재 lifecycle·변경 이유를 API와 상세 화면에 함께 제공한다. 예상 운용범위는 건강한 포지션을 그 시간에 고정 종료한다는 뜻이 아니다.

## 연구 전용 multi-timeframe 후보

Wave 34의 후보는 런타임 A~J Registry와 분리된 연구 전용 계층이다. 1m canonical completed candle에서 3m·5m·15m·30m·1h·4h를 결정적으로 집계하고, MICRO_SCALP·FAST_INTRADAY·INTRADAY_SWING의 12개 시간축에 다섯 alpha family와 ORIGINAL·MECHANICAL_MIRROR·HYPOTHESIS_REVERSE를 사전등록했다. 전체 180개 key 중 mirror를 제외한 120개를 승격 가능 가설 수로 multiple-testing 보정에 포함했다.

13개 저장 `LIVE_PUBLIC` Run 전수 OOS에서 선택 후보도 BASE 기대값 -4.893bp, PF 0.554, 표본 2건, STRESS 기대값 -16.893bp, PBO 0.629였고 모든 승격 gate가 실패했다. 따라서 신규 strategy ID나 SHADOW 계좌를 만들지 않았으며, 자연신호를 늘리기 위해 기준을 낮추지 않았다. 전체 JSON·HTML은 `evidence/WAVE34_INTRADAY_RESEARCH.*`에 보존한다.

Wave 39의 Binance USDⓈ-M 12종목·완성 5분봉 414,720개 사전등록 후보 6개도 BASE와 STRESS가 모두 음수여서 선택하지 않았다. Wave 41은 완성 1시간봉의 비용인식 추세가 진단 OOS 42건에서 BASE +32.212bp·PF 1.346, STRESS +20.212bp·PF 1.202였으나 bootstrap 95% 하한 -48.537bp, DSR 0, PBO 0.3714로 승격 gate를 통과하지 못했다. Wave 46의 고정 독립 과거구간 재현도 147일·166건에서 BASE 기대값 -18.263bp·PF 0.856, STRESS -30.263bp·PF 0.775로 실패해 K는 `RETIRED·OFF`이며 수익성은 `NOT_PROVEN`이다. 기계판독 결과는 `evidence/WAVE39_PUBLIC_TREND_RESEARCH.json`, `evidence/WAVE40_PUBLIC_HOURLY_TREND_DIAGNOSTIC.json`, `evidence/WAVE41_PUBLIC_COST_AWARE_TREND_DIAGNOSTIC.json`과 `evidence/wave46-strategy-survival/fixed-hourly-prior-holdout.json`에 보존한다.

## 후보에서 불변 계획까지

전략의 `QUALIFIED` 결과만 바로 체결되는 것은 아니다. 공통 Candidate Planner가 다음 항목을 모두 확정하고 비용·위험 게이트를 통과해야 한다.

- signal event, Run, venue, symbol, 전략 버전, 방향, 레짐.
- planned entry와 worst allowed entry.
- 초기 SL과 noise buffer.
- TP1·TP2 가격과 각 수량 비율.
- 수량, 최소 수량, 위험예산, 최대 계획손실.
- 예상 수수료, 예상 슬리피지, 순 보상, 순 위험, 순 R:R.
- 데이터·신호·유동성 품질과 비용 부담.
- 거절 reason code와 비전문가용 한국어 설명.

main 계좌는 동시에 최대 한 포지션만 허용하고 여러 적격 후보가 있으면 결정적 arbitration key로 하나만 선택한다. 각 전략의 BASE·STRESS shadow 계좌는 서로 손익이나 포지션을 공유하지 않는다.

## 성과 해석

승률만으로 전략을 판단하지 않는다. 화면은 전략·비용 프로필별로 표본 수, 승률, USDT·R·bp 기대값, Profit Factor, 수수료, 슬리피지, 최대 낙폭, 보유시간 중앙값·p90, 레짐 수, 표본 기간을 함께 표시한다.

- 0~29건은 초기 수집 상태다.
- 30~99건은 제한된 표본이다.
- 100~299건은 중간 표본이다.
- 300건 이상도 시장·레짐 분산을 별도로 확인해야 한다.

표본이 없거나 부족하면 수치를 꾸미지 않고 `CALIBRATING`, 표본 없음, 판단 보류로 표시한다. PAPER 결과는 실제 수익이나 향후 성과를 보장하지 않는다.
