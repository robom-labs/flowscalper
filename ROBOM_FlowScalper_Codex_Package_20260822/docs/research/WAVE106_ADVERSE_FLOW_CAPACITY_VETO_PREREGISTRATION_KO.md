# Wave 106. 역방향 압력·흡수여력 진입 거부필터 사전등록

## 상태

`PRE_REGISTERED_NOT_RUN`이다. 사전등록 시각은 `2026-08-29T03:53:48Z`, 기준 개발 commit은
`1ec2e59463870f2dc68c761c4cfe71cd4511fcb4`다. 이 후보는 기존 전략이 이미 통과시킨 PAPER
진입을 추가로 거부할 수만 있으며, 아직 구현·저장 replay·설치 서비스 배포를 하지 않았다.
수익성은 `NOT_PROVEN`, 실자금 준비상태는 `NOT_READY`다.

## 왜 별도 후보로 두는가

- 현재 revision의 AGGRESSOR 전략은 자연 `LIVE_PUBLIC` BASE 6건에서 승률 16.67%, 비용 후
  기대값 약 `-1.0475 USDT`, Profit Factor 약 `0.0467`이고 STRESS 5건은 승리 0건이다.
  표본이 30건 미만이므로 순위를 매기지는 않지만 현재 결과는 운영 승격 근거가 아니다.
- 최근 자연 BTC SHORT 한 쌍은 방향상 총손익이 양수였으나 900.9초 최대보유 종료 뒤 BASE와
  STRESS 모두 수수료·슬리피지 후 음수였다. 1~3초 조기종료 재발은 아니며, 비용을 감당하지
  못하는 진입을 청산 규칙만 바꿔 해결할 수 없다는 진단이다.
- Wave 105B는 최근 가격범위와 4-of-6 방향 합의를 검사한다. 이번 후보는 같은 조건을 다시
  조정하지 않고, 역방향 공격체결 압력이 실제 표시 흡수여력보다 큰지와 지지 호가가 동시에
  취약한지를 결합하는 별도 거부가설이다.

## 단일 가설

가설 ID는 `HYP-W106-ADVERSE-FLOW-CAPACITY-VETO-V1`이다.

기존 전략이 `QUALIFIED`한 시점에 다음 두 값이 함께 높은 진입만 거부하면, 방향 예측을 새로
만들지 않고도 역방향 체결압력을 버티지 못할 진입과 불필요한 비용을 줄일 수 있다는 가설이다.

1. LONG의 `adverse_pressure_to_capacity`는
   `max(0, -signed_notional_3s) / depth_bid_10`이다.
2. SHORT의 `adverse_pressure_to_capacity`는
   `max(0, signed_notional_3s) / depth_ask_10`이다.
3. LONG의 `support_fragility`는
   `bid_cancel_ratio_3s - bid_refill_ratio_3s`이다.
4. SHORT의 `support_fragility`는
   `ask_cancel_ratio_3s - ask_refill_ratio_3s`이다.
5. 두 값 중 하나라도 유한하지 않거나 지지 depth가 0 이하이면 fail-closed로 거부한다.
6. 동결 Train 6개 Run의 기존 `QUALIFIED` 결정시점만 사용해 각 값의 75백분위를 한 번
   계산한다. 거래 결과, TP·SL 도달과 미래가격은 임계값 계산에 사용하지 않는다.
7. 두 값이 각 Train 75백분위 이상인 상태가 event time 기준 1,000ms 연속 유지될 때만
   거부한다. 하나라도 임계값 아래로 돌아오면 지속시간을 초기화한다.
8. q75, 1,000ms와 위 계산식은 Validation이나 미래표본 결과를 본 뒤 바꾸지 않는다. 다른
   분위수·지속시간·논리조합을 시험하면 각각 별도 candidate와 다중시험 수로 기록한다.

## 변경하지 않는 항목

- 기존 11개 전략의 신호 생성, 방향, 진입조건과 확인시간.
- 수량, 최대손실, entry, TP1, TP2, SL, 최대보유와 Wave 102 관리청산 계약.
- 실제 bid·ask 호가깊이 체결, BASE 13bps와 STRESS 25bps, 수수료와 슬리피지.
- Strategy Registry의 lifecycle, ACTIVE·SHADOW·OFF와 LONG·SHORT 제어.
- 실제 주문, private API, 인증, API Key, secret, wallet과 입출금 경로는 계속 0이다.
- 신호가 적다는 이유로 어떤 임계값도 낮추지 않는다.

## 데이터 경계와 평가 순서

1. 먼저 Wave 104의 실제 6시간 관찰과 Wave 105의 동결 13개 Run 전체 baseline replay를
   끝낸다. 이 작업들이 완료되기 전에는 설치 서비스를 교체하지 않는다.
2. 동결 Train 6개에서 미래값 없이 q75 두 개를 계산하고 고정한다.
3. Validation 2개에서는 구현결함, 후보 수 비증가, 비용후 방향성만 확인한다.
4. 이미 열어 본 과거 Final OOS 5개는 진단에 사용할 수 있지만 이 가설의 독립 OOS 증거로
   사용하지 않는다.
5. 사전등록 시각 이후에 수집한 새 `LIVE_PUBLIC` 기간을 코드·설정·reader checksum과 함께
   먼저 동결한 뒤 미래 OOS로 사용한다. 같은 기간을 보고 임계값을 다시 조정하지 않는다.
6. baseline과 후보는 동일 이벤트 순서, 동일 전략·후보·호가깊이 체결·TP1·TP2·SL·종료
   경로에서 한 번에 비교한다. 후보의 기존 `QUALIFIED`·계획·거래 수는 baseline보다 늘 수
   없다.
7. BASE·STRESS 한 쌍은 서로 다른 시장기회 두 건으로 세지 않는다. 서로 다른 기회 30건
   전에는 순위를 매기지 않고 `NOT_PROVEN`으로 둔다.

## 유지·폐기 기준

- 구현 회귀, PAPER safety, 실제 주문 0, 인증 0, lint, typecheck와 동일입력 결정성이 모두
  통과해야 한다.
- 후보가 신호를 만들거나 방향을 바꾸거나 baseline보다 거래 수를 늘리면 즉시 `REJECTED`다.
- 15초 이하 거래는 TP·SL·데이터·시스템 안전종료와 일반 관리청산을 구분한다. 비용대 안의
  비정상 일반 조기종료가 재발하면 배포하지 않는다.
- 30개 이상의 서로 다른 미래 OOS 기회에서 BASE와 STRESS 승률이 각각 70% 이상이어야 한다.
  이 조건만으로는 통과가 아니다.
- BASE·STRESS 비용 후 기대값 양수, 순손익 양수, Profit Factor 1 초과, bootstrap 기대값
  95% 하한 양수, DSR 0.95 이상, PBO 0.20 이하, drawdown 제한, 종목·레짐·시간 집중도 제한을
  함께 통과해야 한다.
- 거래가 0이거나 표본이 30건 미만이거나 결과가 한 종목·한 레짐·한 짧은 시간대에 몰리면
  `NOT_PROVEN`이다. 높은 표시승률로 바꾸어 쓰지 않는다.
- 저장 replay를 통과해도 Registry를 자동 승격하지 않는다. 별도 새 revision의 독립 자연
  `LIVE_PUBLIC` SHADOW 표본과 Governor 승격조건이 추가로 필요하다.

## 연구 근거와 거절한 지름길

- Cont, Kukanov, Stoikov의
  [The Price Impact of Order Book Events](https://arxiv.org/abs/1011.6402)는 단기 가격변화와
  OFI의 관계가 시장깊이에 따라 달라짐을 미국 주식에서 보였다. 계산식의 경제적 출발점일 뿐
  암호화폐 PAPER 수익성 증거가 아니다.
- Chang의
  [Do Order-Book States Predict Passive-Buy Toxicity?](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6693260)는
  BTC perpetual에서 원시 flow보다 flow 대비 근접호가 흡수여력과 유동성 취약성을 함께 보는
  가설을 보고한다. 현재 확인 범위는 공개 초록 수준이며 ROBOM 데이터로 아직 재현하지 않았다.
- Bysik과 Ślepaczuk의
  [Machine Learning-Based Bitcoin Trading Under Transaction Costs](https://arxiv.org/abs/2606.00060)는
  방향 예측만 거래로 바꾸면 비용 후 실패할 수 있고 비용을 넘는 예상 움직임을 거르는 접근을
  검토한다. 시간봉 ML 연구이므로 이번 미세구조 가설의 성과 증거는 아니다.
- [Deep Learning for Digital Asset Limit Order Books](https://arxiv.org/abs/2010.01241)의 2초
  walk-forward 분류정확도 71%를 거래승률 70%로 바꾸어 해석하지 않는다. Coinbase spot의
  분류정확도이며 실제 bid·ask 깊이, ROBOM BASE·STRESS 비용, TP·SL과 독립 미래 PAPER
  순수익을 증명하지 않는다. 현재 데이터·GPU·짧은 보유목표에 CNN을 바로 배포하는 경로는
  이번 후보에서 명시적으로 제외한다.

위 자료는 가설 선택 근거일 뿐 통과증거가 아니다. 최종 판단은 동일한 공개시장 입력과 실제
PAPER 경로의 비용후 결과, 이후 독립 미래표본으로만 한다.
