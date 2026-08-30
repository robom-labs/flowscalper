# HYP-129. 연속 상승 상태·위험감쇠 주별 모멘텀 30후보 사전등록

- 사전등록 상태. `LOCKED_BEFORE_EXECUTION`.
- 실행 상태. `NOT_RUN`.
- 등록일. 2026-08-30.
- 가설 ID. `HYP-129-UP-UP-STATE-RISK-CAPPED-MOMENTUM-TOURNAMENT`.
- 후보 지문. `deceb5868087ed4989064b03361eab2b34952ba6b457505db801e93d9e6c19cb`.
- 성과 상태. `NOT_PROVEN`.
- 실자금 준비. `NOT_READY`.

## 적응 경계와 이번 한 가지 질문

HYP-128은 일봉 30후보 모두에서 사전등록 표본·시간순 안정성 선발을 통과하지 못했다. 일부
후보의 합계가 양수여도 최근 구간과 최소 표본이 부족했으므로 기준을 낮추지 않았다.

그 결과와 아래 2024~2025 연구를 읽은 뒤 이번 가설을 설계했다. 따라서 마지막 30%도 독립
미래표본이라고 주장하지 않는다. 이번에 고정해서 묻는 질문은 하나다.

> 4주 시장수익이 두 번 연속 음수가 아닌 `UP-UP` 상태일 때, 2주 또는 4주 모멘텀을 다음 주
> 시가부터 보유하고 위험을 늘리지 않는 변동성 감산을 적용하면, 같은 비용·펀딩·시간순 gate에서
> 무조건 모멘텀과 비상승 상태보다 안정적인가?

## 연구 근거와 그대로 복제하지 않는 이유

- `State transitions and momentum effect in cryptocurrency market`은 2015~2023 주별 자료에서
  모멘텀이 `UP-UP` 상태에 집중됐다고 보고한다.
  <https://doi.org/10.1016/j.frl.2025.108356>
- `Cryptocurrency market risk-managed momentum strategies`는 2주 형성·1주 보유 WML과 변동성
  조정을 연구했다.
  <https://doi.org/10.1016/j.frl.2025.107879>
- `Cryptocurrency anomalies and economic constraints`는 대형 코인 모멘텀도 회전과 비용이 크고,
  비용 뒤 alpha가 크게 줄며 최근성과 롱·숏을 따로 봐야 한다고 경고한다.
  <https://doi.org/10.1016/j.irfa.2024.103218>
- `Cryptocurrency momentum has (not) its moments`는 위험조정 모멘텀의 조건부·반대 결과를
  보존하게 한다.
  <https://doi.org/10.1007/s11408-025-00474-9>
- NBER `Risks and Returns of Cryptocurrency`의 일·주 time-series momentum은 후보 계열의
  초기 근거일 뿐 현재 수익 보증이 아니다.
  <https://www.nber.org/papers/w24877>

논문의 미래정보, value-weighted 전 종목 universe 또는 자동 레버리지 확대를 흉내 내지 않는다.
고정 데이터에는 과거 시가총액이 없으므로 시장수익은 12개 대형 종목 동일가중을 사용한다.
논문과의 이 차이를 결과에 명시한다. 변동성 조정은 위험을 키울 수 없고 `0.25~1.0` 배율로
줄이기만 한다.

## 고정 데이터와 시계열 경계

- UTC `2021-01-01` 이상 `2026-08-30` 미만 Binance USDⓈ-M 공개시장 데이터다.
- 종목은 `BTCUSDT`, `ETHUSDT`, `BNBUSDT`, `SOLUSDT`, `XRPUSDT`, `DOGEUSDT`,
  `ADAUSDT`, `AVAXUSDT`, `LINKUSDT`, `DOTUSDT`, `LTCUSDT`, `BCHUSDT`다.
- 완성 4시간봉 6개가 연속인 UTC 날짜만 일봉으로, 월요일 UTC부터 7개 완성 일봉이 연속인
  주만 주봉으로 집계한다.
- 12종목 모두 존재하는 공통 주만 시장상태와 횡단면 순위에 사용한다.
- 주 t 종료 뒤 알 수 있는 수익만으로 현재 4주 상태와 한 칸 전 4주 상태를 계산한다.
- 주 t 일요일 종가에서 후보를 확정하고 주 t+1 월요일 UTC 일봉 시가에 진입한다.
- 미래 주를 바꿔도 과거 상태·후보가 바뀌지 않아야 한다.
- 실제 공개 펀딩 이력은 방향별 cashflow로 적용한다.
- 원본 bar·funding과 파생 일봉·주봉 SHA-256을 종목별 manifest에 기록한다.

## 고정 후보 30개와 대조군

아래 5계열마다 `UP_UP`, `ALL_REGIMES`, `NON_UP_UP`와 `FIXED_RISK`, `VOL_CAPPED`을
조합한다. 따라서 5계열 × 3상태 × 2위험방식 = 30개다.

| 계열 | 고정 형성·방향 | 고정 진입 후보 |
|---|---|---|
| 2주 횡단면 승자 롱 | 최근 2주 수익 상위 2개 | 절대 모멘텀 +1% 이상 |
| 4주 횡단면 승자 롱 | 최근 4주 수익 상위 2개 | 절대 모멘텀 +2% 이상 |
| 2주 승자-패자 | 최고 승자 롱·최저 패자 숏 | 각각 절대 모멘텀 1% 이상 |
| 2주 시계열 모멘텀 | 절대 수익 상위 2개 | 양수 롱·음수 숏, 절대 2% 이상 |
| 2주 승자·느린 정렬 | 최근 2주 승자 롱 | 주봉 종가 > EMA4 > EMA12 |

`NON_UP_UP` 10개는 상태효과의 음성 대조군이다. 결과가 좋아도 선발·승격할 수 없다.
`ALL_REGIMES`는 무조건 모멘텀 대조군이다. 정확한 candidate ID와 모든 수치는
`scripts/research_state_conditioned_momentum_tournament.py`의
`PREREGISTERED_STATE_MOMENTUM_CANDIDATES`가 유일한 실행계약이다. 결과 뒤 같은 ID의 값을
바꾸지 않는다.

## 진입·보호·계좌 위험 계약

- entry, 구조적 SL, TP1, TP2, 수량에 대응하는 계좌 위험을 진입 전에 확정한다.
- SL은 신호일까지의 7일 또는 10일 극값에 0.25ATR 또는 0.30ATR 여유를 둔다.
- 최초 위험거리는 0.65~4.0ATR만 허용한다.
- TP1은 1.5R에서 40%, TP2는 계열별 3.5R·4.0R·4.5R에서 60%다.
- 같은 일봉에서 SL과 TP가 모두 닿으면 SL을 먼저 적용한다.
- TP1 뒤 잔여 손절은 STRESS 왕복비용을 확보하는 방향으로만 이동한다.
- 고정 최대보유와 일반 근거약화 청산은 없다.
- 데이터 끝까지 TP·SL이 닿지 않은 포지션은 `CENSORED_OPEN`으로 보존하고 채점하지 않는다.
- 후보별 최대 동시 2포지션·UTC 하루 최대 2진입을 적용한다.
- 1회 계좌 위험예산은 40bp다. 구조적 위험거리로 notional fraction을 계산하고 1.0을 넘지
  않는다.
- `VOL_CAPPED`은 최근 8주 변동성 대비 주 8% 목표를 쓰되 배율은 최소 0.25, 최대 1.0이다.
  따라서 자동 레버리지·위험증가는 없다.
- 손절 확대, 물타기, 마틴게일, 피라미딩과 실제 주문은 없다.

## 비용·펀딩 계약

- BASE 왕복 실행비용 13bp와 STRESS 25bp를 notional fraction만큼 각각 차감한다.
- 포지션 방향과 보유구간에 맞는 실제 공개 펀딩을 계좌 손익에 반영한다.
- 진입·종료 일봉 경계에서 보유 여부가 모호한 유리한 펀딩 credit은 제외하고 불리한 cost는
  포함한다.
- 역사 일봉에는 실제 bid·ask 깊이와 봉 내부 순서가 없으므로 통과해도 체결 가능성을 증명하지
  않는다.

## 선발·시간순·과최적화 gate

- 전체는 50% Train·20% Validation·30% 진단 OOS이며 큰 경계마다 7일 embargo를 둔다.
- development 70%를 6개 연속 fold로 나눈다.
- fold별 양 끝 7일을 제외하고 STRESS 완료거래 8건 이상인 fold를 최소 5개 요구한다.
- 최소 4개 fold와 가장 최근 2개 fold에서 STRESS 기대값 양수·PF 1 초과를 요구한다.
- development 완료거래 60건·Validation 20건과 기존 STRESS 양수·PF gate를 요구한다.
- 같은 계열은 최대 1개, 전체 최대 5개만 진단 OOS로 보낸다.
- 진단 OOS 완료거래 30건, BASE·STRESS 기대값 양수, BASE PF 1.15, STRESS PF 1.05,
  bootstrap 95% 기대값 하한 양수, DSR 0.95, PBO 0.20 이하, 최대 한 종목 양의 기여 50%
  이하를 모두 요구한다.
- 승률 70%는 참고 진단이며 기대값·비용·drawdown·표본 gate를 대신하지 않는다.

모든 역사 gate를 통과해도 결과는 `ADAPTIVE_HISTORICAL_PASS_FORWARD_REQUIRED`일 뿐이다.
실제 공개 bid·ask BASE·STRESS SHADOW에서 현재 버전 자연표본 30건과 독립 미래구간을 통과하기
전에는 수익성 `NOT_PROVEN`, 실자금 `NOT_READY`다. 통과 후보가 없으면 Registry 변경 0을
유지하고 실패를 지우지 않는다. 실제 주문, private API, API Key, secret, 인증, wallet과
입출금 경로는 계속 0이다.

## 실행 뒤 고정 결과

- 후보·파라미터 사전등록 commit은 `038abf72de678be1571fbe79e4333f5c2b2dc18c`, 후보 지문은
  `deceb5868087ed4989064b03361eab2b34952ba6b457505db801e93d9e6c19cb`다.
- 최초 실행에서 완성 주의 일요일 종가로 신호를 만들면서 같은 주 일요일 시가를 진입가로
  사용한 미래참조 구현 결함을 발견했다. 그 결과는 전부 폐기했고 선발·승격에 사용하지 않았다.
- 후보 수치와 gate는 바꾸지 않고 다음 월요일 시가 경계를 회귀테스트로 고정한
  `0561dd474dddaeef989840dac21be5c534a2c904`에서 다시 실행했다.
- 12종목의 완성 일봉 24,804개와 주봉 3,528개, 공통 주별 상태 282개를 사용했다.
  `UP_UP`은 101주, 그 외 상태는 181주였고 dataset 지문은
  `425a7601789062f823ef62554fc7feb076992907fa7bac4bc70ac46f941b70f7`이다.
- 후보 전체 중복 평가 기준 원신호 6,772개, 포트폴리오 선택 1,372건, 완료거래 1,330건,
  데이터 끝 미결 42건이었다.
- 30개 모두 최소 표본 또는 walk-forward 안정성 gate를 실패했다. 평가 가능한 fold 최댓값은
  4개, 양수 fold 최댓값은 2개였고 Train·Validation 선발과 진단 OOS 진입은 모두 0개다.
- PBO는 `0.6571428571`로 고정 상한 0.20을 크게 넘었다.
- `T129_XSMOM_4W_LONG_ALL_REGIMES_VOL_CAPPED`은 development 46건에서 STRESS 기대값
  +10.988 계좌 bp·PF 1.787이었지만 Validation 14건과 평가 가능 fold 2개뿐이어서 순위를
  매기지 않았다.
- 표본이 가장 많았던 `T129_XSMOM_2W_LONG_ALL_REGIMES_VOL_CAPPED`도 development
  57건·Validation 21건이지만 STRESS 기대값 -1.992 계좌 bp·PF 0.874로 음수였다.
- Registry·PAPER SHADOW 승격은 0개다. 수익성은 `NOT_PROVEN`, 실자금 준비는
  `NOT_READY`다.
- 전체 결과는 `evidence/WAVE129_STATE_CONDITIONED_MOMENTUM_TOURNAMENT.json`, 요약은
  `evidence/WAVE129_STATE_CONDITIONED_MOMENTUM_TOURNAMENT_QA.json`, append-only 시험
  기록은 `RESEARCH-HYP129-425a76017890-9a89b60eaa2a`에 보존한다.
