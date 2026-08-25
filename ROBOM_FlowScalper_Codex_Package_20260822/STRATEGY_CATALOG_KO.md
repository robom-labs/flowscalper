# ROBOM FlowScalper v0.2 전략 카탈로그

## 공통 원칙

열 전략은 모두 공개시장 데이터와 내부 PAPER 체결에만 사용된다. 전략은 주문 권한이 없고 거래소 계정이나 private API를 호출하지 않는다. 같은 symbol snapshot과 과거 이력만 사용하며 현재값 이후 정보를 참조하지 않는다.

| 구분 | Strategy ID | 화면 이름 | 안정성 | 주 레짐 | 핵심 확인 |
|---|---|---|---|---|---|
| A | `LSA_REVERSAL_V1` | 유동성 쓸기 반전 | STABLE | RANGE, TREND_UP, TREND_DOWN | 쓸기, 흡수, 호가 재충전, 범위 복귀 |
| B | `CBR_CONTINUATION_V1` | 압축 돌파 재가속 | STABLE | TREND_UP, TREND_DOWN | 압축, 돌파, 눌림, 재가속 |
| C | `VWAP_EXHAUSTION_REVERSION_V1` | VWAP 과도이탈 평균복귀 | EXPERIMENTAL | RANGE | micro-VWAP 이탈, 공격 흐름 소진, 구조 복귀 |
| D | `OFI_CONTINUATION_PULLBACK_V1` | OFI 추세 눌림 지속 | EXPERIMENTAL | TREND_UP, TREND_DOWN | 다중 OFI 정렬, 약한 역방향 눌림, 원 흐름 재가속 |
| E | `QUEUE_MICROPRICE_MOMENTUM_V1` | 호가 쏠림 순간추세 | EXPERIMENTAL | RANGE, TREND_UP, TREND_DOWN | top5·top10 호가, OFI, microprice 정렬 |
| F | `AGGRESSOR_FLOW_CONTINUATION_V1` | 강한 체결 흐름 지속 | EXPERIMENTAL | TREND_UP, TREND_DOWN | 방향성 체결금액, OFI, 가격반응 지속 |
| G | `MULTILEVEL_MICROPRICE_MOMENTUM_V1` | 다중호가 공정가 추세 | EXPERIMENTAL | RANGE, TREND_UP, TREND_DOWN | top10 공정가, OFI, 체결, 가격반응 |
| H | `DEPTH_ADJUSTED_OFI_IMPULSE_V1` | 깊이보정 OFI 충격 | EXPERIMENTAL | RANGE, TREND_UP, TREND_DOWN | 깊이보정 OFI robust z, 가격반응 |
| I | `OFI_RETURN_CONFLUENCE_V1` | OFI·단기수익률 동행 | EXPERIMENTAL | RANGE, TREND_UP, TREND_DOWN | 깊이보정 OFI와 prefix 3초 수익률 동행 |
| J | `BOOK_SLOPE_ASYMMETRY_V1` | 호가 기울기 비대칭 | EXPERIMENTAL | RANGE, TREND_UP, TREND_DOWN | top10 가격거리 대비 깊이의 방향 비대칭 |

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

## 모드와 방향 제어

| 화면 선택 | main PAPER 후보 | 독립 BASE·STRESS shadow | 평가 |
|---|---:|---:|---:|
| 실전 PAPER, `ACTIVE` | 포함 | 포함 | 실행 |
| 가상 관찰, `SHADOW` | 제외 | 포함 | 실행 |
| 끄기, `OFF` | 제외 | 제외 | 중지 |

LONG과 SHORT는 각 전략에서 별도로 허용하거나 차단한다. 설정 변경은 같은 Run의 원장에 시각과 함께 기록되며 자동 승격, 자동 중지, 자동 임계 완화는 하지 않는다.

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
