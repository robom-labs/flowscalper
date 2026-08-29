# Wave 105B. 비용·TP1 도달가능성 진입 거부필터 사전등록

## 상태

`PRE_REGISTERED_NOT_RUN`이다. 저장 공개시장 데이터의 후보 결과를 보기 전에 가설, 입력,
계산식과 판정 기준을 고정한다. 이 후보는 연구용 거부필터일 뿐 설치 서비스에 배포하지 않았고,
수익성은 `NOT_PROVEN`, 실자금 준비상태는 `NOT_READY`다.

## 문제와 가설

- 현재 revision의 VWAP·Aggressor 전략은 BASE·STRESS 비용 후 열세이고 TP1·TP2 도달이 거의
  없다. 짧은 양수 표본이나 높은 표시승률만으로 전략을 채택하지 않는다.
- 현재 진입 계획은 구조적 순손익비와 비용비율을 검사하지만, 신호 직전 실제 가격 변동범위가
  그 계획의 TP1 거리와 비용을 현실적으로 감당했는지를 별도로 검사하지 않는다.
- 가설 `HYP-W105B-COST-AWARE-TP1-FEASIBILITY-CONFLUENCE-V1`은 기존 전략이 이미
  `QUALIFIED`한 신호 가운데 최근 120초 prefix 가격범위와 현재 호가·체결 방향 합의가 충분한
  신호만 남기면 비용만 내고 끝나는 저품질 진입을 줄일 수 있다는 것이다.
- 이 가설은 결과를 보아가며 승률을 맞추는 학습모델이 아니다. 고정된 설명가능 거부필터이며
  runtime AI 주문판단을 추가하지 않는다.

## 고정 필터

각 기존 `QUALIFIED` 신호마다 미래값 없이 현재와 과거 snapshot만 사용한다.

1. `risk_bps = abs(planned_entry - initial_stop) / planned_entry * 10,000`을 계산한다.
2. `tp1_required_bps = risk_bps * StrategyDescriptor.take_profit_1_r`을 계산한다.
3. 현재를 포함한 최근 120초 mid의 `range_bps = (max(mid) - min(mid)) / current_mid *
   10,000`을 계산한다.
4. `range_bps`가 `max(tp1_required_bps, expected_cost_bps * 2)`보다 작으면 거부한다.
5. LONG은 양수, SHORT은 음수를 유리한 방향으로 보고 다음 6개 중 4개 이상이 방향 합의해야
   한다.
   - top-1 microprice 변위.
   - top-10 multi-level microprice 변위.
   - top-5 queue imbalance 절댓값 0.05 이상.
   - top-10 queue imbalance 절댓값 0.03 이상.
   - 1초 aggressive trade imbalance 절댓값 0.10 이상.
   - 3초 aggressive trade imbalance 절댓값 0.05 이상.
6. 계산에 필요한 entry·SL·비용·가격 이력이 없거나 유한하지 않으면 fail-closed로 거부한다.
7. 필터는 기존 신호를 통과 또는 거부만 할 수 있다. 신호 생성, 방향 변경, threshold 완화와
   거래횟수 증가는 금지한다.

다음 항목은 바꾸지 않는다.

- 전략별 원래 진입조건과 확인시간.
- 실제 bid·ask와 호가깊이 PAPER 체결.
- BASE 13bps·STRESS 25bps 비용조건, 수수료와 슬리피지.
- 수량, 최대손실, entry, TP1, TP2, SL, 최대보유와 관리청산.
- Strategy Registry의 lifecycle과 mode.
- 실제 주문, private API, 인증, API Key, secret, wallet과 입출금 경로는 계속 0이다.

## 동결 데이터와 시간 경계

- 공용 수신순 reader의 `(receive_ts_ms, receive_monotonic_ns, venue_ts_ms, symbol,
  payload_json)` 순서를 사용한다.
- Wave 105와 같은 13개 저장 `LIVE_PUBLIC` Run과 checksum을 사용한다.
- train 6개, validation 2개, 최종 OOS 5개의 기존 시간순 경계를 그대로 사용한다.
- baseline은 `signal_gate=NONE`, 후보는
  `signal_gate=TP1_FEASIBILITY_CONFLUENCE_V1`로 실제 PAPER 전략→후보→호가깊이 체결→
  TP1·TP2·SL→관리종료 경로를 각각 실행한다.
- archive 끝의 열린 포지션과 대기 진입은 강제 청산하지 않고 `censored`로 분리한다.
- OOS 결과를 본 뒤 120초, 2배 비용, 4-of-6 또는 방향 임계값을 바꾸지 않는다.

## 통과·퇴역 기준

1. 모든 split에서 후보의 기존 `QUALIFIED` 신호와 후보계획 수는 baseline보다 늘지 않아야 한다.
2. 기준과 후보의 archive checksum, reader checksum, 이벤트 수와 순서가 같아야 한다.
3. BASE와 STRESS 각각 서로 다른 시장기회 30건 전에는 순위를 매기지 않고 `NOT_PROVEN`으로
   둔다. BASE·STRESS 한 쌍은 독립 기회 두 건으로 세지 않는다.
4. 70%는 충분조건이 아니다. 30건 이상에서 BASE·STRESS 승률이 각각 70% 이상이고, 비용 후
   기대값 양수, Profit Factor 1 초과, drawdown 제한, 시간순 OOS, bootstrap 기대값 하한,
   DSR과 PBO를 함께 통과해야 다음 독립 LIVE_PUBLIC SHADOW 관찰 후보가 된다.
5. 후보가 0건이거나 표본이 줄어 30건 미만이면 성공으로 해석하지 않는다. 해당 가설을
   `REJECTED` 또는 `NOT_PROVEN`으로 기록하고 미리 정의한 다음 가설로 이동한다.
6. 15초 이하 거래는 TP·SL·데이터·시스템 안전종료와 일반 관리청산을 분리해 감사한다. 비용대
   안의 비정상 일반 조기종료가 있으면 배포하지 않는다.
7. 저장 replay 통과만으로 Registry를 승격하지 않는다. 새 revision의 독립 미래
   `LIVE_PUBLIC` 표본과 Governor gate가 추가로 필요하다.
8. Wave 104의 실제 6시간 관찰이 끝나기 전에는 설치 서비스를 교체하지 않는다. 24시간을
   실제로 채우지 않았으면 계속 `NOT_RUN`이다.

## 연구 근거와 해석 경계

- [Deep Learning for Digital Asset Limit Order Books](https://arxiv.org/abs/2010.01241)는
  walk-forward 방향분류 성능 가능성을 보고하지만 분류정확도를 비용 후 거래승률로 해석할 수
  없다.
- [Multi-Level Order-Flow Imbalance](https://arxiv.org/abs/1907.06230)는 여러 호가단계의
  불균형이 가격변화 설명력을 보완할 수 있음을 보인다. 암호화폐 수익성 증거는 아니다.
- [High-Resolution Microprice](https://arxiv.org/abs/2411.13594)는 고차 호가불균형을 이용한
  microprice 추정 개선을 다룬다. 이 문서의 4-of-6 규칙 자체를 검증한 자료는 아니다.
- [Cost-aware Bitcoin walk-forward study](https://arxiv.org/abs/2606.00060)는 예측 방향만
  거래하면 비용 후 실패할 수 있고 예측 이동폭과 비용을 함께 거르는 접근이 필요함을 보여준다.

위 자료는 고정 가설을 세우는 근거일 뿐 ROBOM 전략의 수익성 증거가 아니다. 통과 여부는 오직
동결된 동일 입력의 PAPER 결과와 이후 독립 미래표본으로 판단한다.

## 사전등록 후 구현 확인

- 관련 PAPER 전략·포트폴리오·Registry 회귀 `133 passed`를 확인했다.
- Ruff, mypy와 `git diff --check`가 통과했다.
- `RUN-72EB83B350A7`의 첫 5,000개 저장 이벤트를 baseline과 후보에서 각각 실행했다. 두
  경로 모두 5,000개를 처리했고 실제 주문·인증은 0이었다.
- 후보 smoke 출력은 `STRATEGY_100_DATASET_MANIFEST.json`의 내부 manifest checksum
  `61765a668d29b950e50fd8c6bccc372b7e747885e0a0870206411b0e46165e20`과 선택 Run의
  동결 checksum·전체 이벤트 수를 함께 고정했다. 실행 중인 장시간 관찰을 방해하지 않도록
  현재 archive byte 재해시는 아직 `NOT_RUN`이며 관찰 종료 후 본 비교 전에 실행한다.
- 이 짧은 구간에는 기존 `QUALIFIED` 신호와 거래가 0건이었다. 따라서 이 결과는 입력형식과
  PAPER 경로 smoke일 뿐 후보 성과 검증이 아니며 전체 상태는 계속 `PRE_REGISTERED_NOT_RUN`,
  수익성은 `NOT_PROVEN`이다.
- replay 요약의 `ranking_eligible`가 관측승률 70%만으로 참이 될 수 있던 구현 차이를
  수정했다. BASE·STRESS 한 쌍을 하나로 세는 고유 시장기회 30개와 비용후 기대값·순손익·
  Profit Factor를 별도 검사하되, 시간순 OOS·bootstrap·DSR·PBO·drawdown·독립 미래
  `LIVE_PUBLIC`은 이 runner가 계산하지 않으므로 항상 명시적 blocker로 남기고 자동승격하지
  않는다. 관련 replay 표적 테스트 11건과 Ruff·mypy·diff 검사가 통과했다.
