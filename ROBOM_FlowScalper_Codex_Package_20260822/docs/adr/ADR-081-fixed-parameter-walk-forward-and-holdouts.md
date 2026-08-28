# ADR-081. 고정 파라미터 walk-forward와 point-in-time holdout

- 상태는 `ACCEPTED_IMPLEMENTATION_NOT_EXECUTED`다.
- 범위는 100후보 Stage 1 Train·Validation 보고서다.
- Final OOS·Forward LIVE_PUBLIC, 런타임 Registry와 ACTIVE 상태는 변경하지 않는다.

## 문제

네 개 시간순 Validation fold와 purge·embargo만 만들면 데이터 경계는
보호할 수 있지만 anchored·rolling 결과와 종목·거래소·장세·변동성·비용
holdout이 실제로 생성되지 않는다. 특히 한 거래소 표본을 여러 거래소에서
견고한 결과로 표시하거나, 신호 후의 전체 기간분포로 변동성 구간을 다시
만들면 검증 누수가 발생한다.

## 결정

1. anchored walk-forward는 평가 fold 이전의 모든 fold를 training 창으로,
   rolling walk-forward는 직전 한 fold만 training 창으로 사용한다.
2. 두 방식 모두 사전등록된 trial parameter를 고정한다. training 결과로
   parameter를 다시 맞추거나 승자를 바꾸지 않는다.
3. training과 evaluation 창 모두에 실제 거래가 있을 때만 해당 창을
   `EXECUTED_FIXED_PARAMETERS`로 기록한다. 나머지는 `INSUFFICIENT_DATA`다.
4. Validation 거래는 symbol, venue, regime, volatility, bull·bear·range,
   BASE·STRESS cost profile을 leave-one-group-out 형식으로 집계한다. 이 집계는
   선택·재튜닝을 하지 않는 안정성 진단이다.
5. 변동성 라벨은 신호 시점에 완료된 본으로 산출한 `fast / slow`
   실현변동성 비율로 고정한다. 0.75 미만은 `LOW`, 0.75에서 1.5까지는
   `NORMAL`, 1.5 초과는 `HIGH`다. slow가 0이면 추정하지 않고 `UNKNOWN`이다.
6. 거래소·종목·장세·변동성 그룹이 하나뿐이면
   `INSUFFICIENT_GROUP_VARIATION`으로 남긴다. `UNKNOWN` 라벨이 하나라도 있으면
   `INCOMPLETE_POINT_IN_TIME_LABELS`다.
7. bull·bear·range는 세 그룹이 모두, cost는 BASE·STRESS가 모두 있어야
   coverage를 충족한다. 그룹이 부족하면 결과를 숨기지 않는다.
8. 이 보고서는 Final OOS를 열지 않고 Registry·ACTIVE·LIVE SHADOW를
   자동 변경하지 않는다. 실행 전 상태는 수익성 `NOT_PROVEN`이다.
9. 1차 screening이 낸 최대 25개는 예비후보다. BASE·STRESS의 anchored·rolling과
   여섯 holdout 차원을 모두 충족한 trial만 상세 event replay 선택에 남긴다.
   현재 동결 소스처럼 venue 그룹이 하나면 선택 0개가 정상이다.

## 검증 상태

- Ruff, mypy, `py_compile`은 통과했다.
- anchored·rolling, 단일 거래소 부족, BASE·STRESS coverage와 변동성
  고정 경계 회귀를 작성했다.
- 보고서 생성·부족 holdout 예비후보 차단과 임의 Validation 거래 삽입·
  Final OOS 혼입 거부를 가벼운 직접 호출로 확인했다. 이는 pytest 전체
  통과를 뜻하지 않는다.
- 6시간 observer 종료 전이므로 pytest·실제 screening은 `NOT_RUN`이다.
- 현재 동결 소스가 BINANCE_USDM 한 거래소라면 venue holdout은 필연적으로
  `INSUFFICIENT_GROUP_VARIATION`이며 이를 PASS로 바꾸지 않는다.
