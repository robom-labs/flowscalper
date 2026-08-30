# Wave 118. YouTube·TradingView 중단타 아이디어 중복 대조

## 판정 원칙

- 영상은 후보 발굴 자료이며 성과 증거가 아니다.
- 설명만으로 진입·손절·익절을 결정적으로 고정할 수 없으면 구현하지 않는다.
- 기존 후보와 같은 계산이면 새 이름을 만들지 않고 기존 계열에 병합한다.
- 수수료·슬리피지·다중시험·시간순 OOS를 생략한 승률과 수익률은 가져오지 않는다.
- 제휴 링크, 유료방, 비공개 지표와 사후 차트 설명은 신뢰도 가산점이 아니다.

## 확인한 공개 자료와 결정

| 공개 자료 | 추출 가능한 개념 | 기존 대응 | 결정 |
|---|---|---|---|
| [My Best Liquidity Sweep Trading Strategy](https://www.youtube.com/watch?v=RnP08K2SAZs) | 고가·저가 sweep 뒤 범위 복귀와 구조 확인 | 동일한 결정 규칙 없음 | `NEW_HYP_118`, 모호한 기관·수급 라벨은 제거 |
| [Market Structure & Liquidity Sweep Masterclass](https://www.youtube.com/watch?v=tFHyr0nRZaU) | sweep, break of structure, 다중시간 확인 | 첫 자료와 중복 | `MERGE_HYP_118`, 별도 후보 수를 늘리지 않음 |
| [Ichimoku Cloud Trading Strategy](https://www.youtube.com/watch?v=32vqaQa-wvY) | 추세 방향의 구름·전환선·기준선 정렬 | 기존 124후보에 없음 | `NEW_HYP_118`, 공식 9·26·52 계산으로 재정의 |
| [VWAP bearish crypto scalping](https://www.youtube.com/watch?v=dYWjZPX7Bbg) | 하락 레짐에서 VWAP 되돌림 | F08 VWAP pullback과 HYP-117 레짐 | `MERGE_EXISTING` |
| [Volume Profile and VWAP Pullbacks](https://www.youtube.com/watch?v=pKzXxB9Blts) | 가격대별 거래량 구역과 anchored VWAP | F09 anchored VWAP 일부 중복 | `DEFER`, 현재 5분봉만으로 세션 가격대별 체결량을 정확히 복원하지 않음 |
| [TradingView Ichimoku 공식 설명](https://www.tradingview.com/support/solutions/43000589152-ichimoku-cloud/) | 9·26·52 선과 26봉 이동 | HYP-118 계산 근거 | `FORMULA_SOURCE` |
| [TradingView KAMA 공식 설명](https://www.tradingview.com/support/solutions/43000773012-kaufman-s-adaptive-moving-average-kama/) | 효율비 기반 적응형 추세 | EMA·ADX 후보와 상당 부분 중복 | `DEFER_DEDUP`, 별도 미래 가설로만 검토 |
| [TradingView Choppiness Index](https://www.tradingview.com/support/solutions/43000501980-choppiness-index-chop/) | 추세와 횡보 구분 | 기존 ADX·레짐 filter와 역할 중복 | `MERGE_FILTER_RESEARCH` |
| [TradingView Volume Profile](https://www.tradingview.com/support/solutions/43000502040-volume-profile-indicators-basic-concepts/) | 과거 가격대별 활동과 POC·VAH·VAL | 현재 이벤트 schema에 완전한 profile 없음 | `DEFER_DATA_PARITY` |

## 실제 추가 범위

- 기존 본선 100개와 HYP-117 24개는 유지한다.
- 새 HYP-118은 비중복 2계열 12개만 추가한다.
- 총 136가설은 같은 결과표에 억지로 섞지 않고 각 사전등록·코드 hash·입력 hash·비용 계좌를
  분리한다.
- 희소표본은 높은 승률이어도 순위를 매기지 않고 `NOT_PROVEN`으로 유지한다.
- 역사결과를 본 뒤 성적이 나쁜 후보를 삭제하지 않고 결과와 버전을 보존한다.

## 해석 경계

공개 영상에서 “많이 벌었다”거나 “승률이 높다”는 표현은 ROBOM에서 검증되지 않았다. 새 후보는
수익 약속이 아니라 기존 계열과 다른 결정적 가설 두 개를 비용 후 반증하기 위한 것이다.
