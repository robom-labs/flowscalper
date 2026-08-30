# ADR-123. 일봉 추세 희소 실패와 상태조건 모멘텀 분리

- 상태. `ACCEPTED`.
- 결정일. 2026-08-30.
- 범위. HYP-128 결과 판정과 후속 PAPER 연구 방향.
- 제외. 실제 주문, private API, 실자금 승격, HYP-128 결과 뒤 임계값 완화.

## 맥락

HYP-128은 일봉 채널 돌파, 첫 재시험, 첫 눌림, 일목 눌림과 EMA 눌림 30개를 결과 전에
고정했다. 12개 대형 종목의 24,804개 완성 일봉에 실제 공개 펀딩과 BASE 13bp·STRESS
25bp를 적용했다.

일부 후보는 development 합계와 최근 두 fold에서 양수였지만 모든 후보가 최소 표본 또는
시간순 안정성 gate를 실패했다. 평가 가능한 6개 fold의 최댓값은 4개였고 양수 fold 최댓값은
3개였다. 선발 후보는 0개였으며 PBO 0.2571428571은 상한 0.20보다 높았다.

최근 연구는 암호화폐 모멘텀이 모든 상태에 대칭적으로 존재한다기보다 연속 상승 상태에
집중될 수 있고, 위험조정 모멘텀도 비용·기간·공매도 제약을 함께 검증해야 한다고 보고한다.
이는 HYP-128 후보의 수치를 바꾸는 근거가 아니라 새 가설을 만드는 근거다.

## 결정

1. HYP-128의 30개 후보는 순위를 매기거나 Registry·SHADOW로 승격하지 않는다.
2. development 44·51건 또는 Validation 19건인 근접 후보를 최소 60·20건 통과로 간주하지
   않는다.
3. HYP-128 결과, 후보·데이터·구현·비용 지문과 trial record를 append-only로 보존한다.
4. 후속 HYP-129는 `상승→상승` 시장 상태, 상대·시계열 모멘텀, 변동성 위험조정을 하나의
   사전등록된 PAPER 후보군으로 분리한다.
5. HYP-129는 더 많은 거래를 만들기 위해 HYP-128 기준을 완화하지 않는다. 주별 또는 일별
   독립 리밸런싱으로 후보당 표본을 늘리되 거래비용과 실제 펀딩을 그대로 적용한다.
6. 후속 후보는 롱 중심 가설과 대칭 롱·숏 반증 후보를 함께 두며, 최근 구간과 상승 외 레짐의
   실패를 별도로 표시한다.
7. HYP-129 역사 통과도 실제 bid·ask 깊이 PAPER SHADOW와 독립 미래표본 전에는
   `NOT_PROVEN`, `NOT_READY`다.
8. 실제 주문, private API, API Key, secret, 인증, wallet, 입출금과 runtime AI 주문판단은
   계속 0이다.

## 근거

- `State transitions and momentum effect in cryptocurrency market`는 주별 2015~2023 표본에서
  모멘텀이 `UP–UP` 상태에 집중된다고 보고한다.
  <https://doi.org/10.1016/j.frl.2025.108356>
- `Cryptocurrency market risk-managed momentum strategies`는 위험조정 모멘텀을 거래비용,
  공매도 제약과 여러 기간에서 검증한다.
  <https://doi.org/10.1016/j.frl.2025.107879>
- `Cryptocurrency anomalies and economic constraints`는 대형 코인의 모멘텀도 상당한 비용과
  최근 성과 약화를 겪으며, 롱 중심·최근 구간·거래가능성 검증이 필요하다고 보고한다.
  <https://doi.org/10.1016/j.irfa.2024.103218>
- `Are simple technical trading rules profitable in bitcoin markets?`는 75,360개 규칙의 비용후
  다중검정과 OOS를 요구한다.
  <https://doi.org/10.1016/j.iref.2024.05.003>

논문 결과는 이 프로그램의 수익성 증거가 아니다. 후속 구현은 별도 가설 번호, 후보 지문,
시간순 선발, 비용후 결과와 실패 보존을 갖춰야 한다.
