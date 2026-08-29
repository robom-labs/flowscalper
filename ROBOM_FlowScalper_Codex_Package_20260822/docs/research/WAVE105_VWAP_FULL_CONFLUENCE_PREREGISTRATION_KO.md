# Wave 105. VWAP 전체 진입조건 지속 확인 사전등록

## 상태

`PRE_REGISTERED_NOT_RUN`이다. 이 문서는 저장시장 데이터 재평가 결과를 보기 전에 후보와
판정 기준을 고정한다. 후보는 아직 설치 서비스에 배포하지 않았고 수익성은
`NOT_PROVEN`, 실자금 준비상태는 `NOT_READY`다.

## 기준선

- 기준 Git commit은 `ff2f5e0cde45e069969e96387b09ac96529a0a40`이다.
- 기준 불변 release는 `0f09703ea973361c3f8d1c52c55dd0437d671f6f`이다.
- 관찰 Run은 `run-2b7135a972dd`다.
- 기준 전략 구현 revision은 `2026-08-28-wave102`다.
- 2026-08-29 현재 revision·Run의 완료 독립 `LIVE_PUBLIC` 거래는 27건이며 BASE·STRESS를
  합쳐 비용 후 양수 3건, 15초 이하 0건, 순손익 약 `-40.5326140660 USDT`다.
- 현재 revision의 15초 조기종료는 재현되지 않았다. 이번 후보는 보유시간이나 청산기준을
  바꾸는 작업이 아니다.
- VWAP 전략은 BASE 8건·STRESS 8건 모두 비용 후 음수이고 TP1·TP2 도달은 0건이다. 이
  표본만으로 임계값을 맞추거나 승률을 주장하지 않는다.

## 결함 가설과 단일 후보

`HYP-W105-VWAP-FULL-CONFLUENCE-PERSISTENCE-V1`을 다음처럼 고정한다.

기준 구현은 VWAP 재진입 확인시간을 데이터 정상·RANGE·구조 재진입·microprice 방향만으로
미리 누적했다. deviation z, excursion 방향, flow z, 가격진행 정지, 반대호가 refill과 OFI
반전은 확인시간에 포함되지 않았다. 따라서 약한 일부 조건만 오래 유지된 뒤 나머지 필수조건이
한 번 깜빡여도 300ms 확인을 이미 끝낸 것으로 처리될 수 있다.

후보는 기존 필수조건 전부가 동시에 참인 동안에만 같은 300ms 확인시간을 누적한다. 조건 하나가
거짓이면 해당 확인시간을 초기화한다. 다음 항목은 바꾸지 않는다.

- deviation·flow·spread·refill·가격반응 임계값.
- RANGE 레짐과 LONG·SHORT 방향 규칙.
- 실제 bid·ask, BASE·STRESS 비용, 수수료와 슬리피지.
- 위험예산, 수량, entry, TP1, TP2, SL과 포지션 관리·청산 규칙.
- Strategy Registry 상태. VWAP은 계속 `SHADOW`다.
- 실제 주문, private API, 인증, API Key, secret과 wallet 경로는 계속 0이다.

## 동결 입력과 실행 방법

- 기준 결과는 `evidence/WAVE34_EXISTING_STRATEGY_RESEARCH.json`이다.
- 동일한 13개 저장 Run, 파일 checksum, 종목, 시간순 train·validation·OOS 경계를 그대로
  사용한다.
- `scripts/research_strategy_revision.py`를 15·30·60·180초 horizon으로 한 번 실행한다.
- 실제 복원 호가의 LONG ask 진입·bid 종료, SHORT bid 진입·ask 종료를 사용한다.
- BASE 13bps, STRESS 25bps를 그대로 사용한다.
- 결과를 본 뒤 조건이나 horizon을 다시 조정해 같은 입력을 재선택하지 않는다.

## 유지·폐기 기준

1. 부분조건만 미리 누적한 뒤 첫 전체조건 snapshot에서 진입하는 회귀가 반드시 실패해야 하고,
   전체조건이 300ms 지속된 뒤에만 통과해야 한다.
2. 기존 전략·포지션·PAPER 안전 회귀, lint와 typecheck가 통과해야 한다.
3. 동일 입력의 각 split·horizon에서 VWAP 후보 신호 수는 기준보다 늘지 않아야 한다. 늘면
   논리 또는 상태 초기화 결함으로 보고 배포하지 않는다.
4. 같은 split·horizon의 비교 가능한 OOS BASE 또는 STRESS 기대값이 악화되면 배포하지 않고
   원인을 조사한다. 표본이 사라지거나 30건 미만이면 개선으로 주장하지 않고
   `NOT_PROVEN`으로 둔다.
5. 저장 replay 결과가 양수여도 Registry 승격에는 사용하지 않는다. 새 revision의 독립 미래
   `LIVE_PUBLIC` 표본을 다시 모으고 30건 전에는 순위를 매기지 않는다.
6. 현재 실행 중인 Wave 104 수정 후 6시간 관찰을 실제로 끝내기 전에는 서비스를 교체하지
   않는다. 24시간을 채우지 않았으면 계속 `NOT_RUN`이다.
7. 자연신호가 적다는 이유로 진입·비용·TP·SL 기준을 낮추지 않는다.

## 별도 후속 연구

과거 표본에서 고정 목표가 존재한다는 사실만으로 목표 도달 가능성을 추정하는 현재 구조는
비용 후 기대 이동폭을 직접 보장하지 않는다. 예상 이동폭이 왕복비용을 충분히 넘을 때만
거래하는 사전등록 후보는 별도의 offline 연구로 다룬다. 이 후보는 이번 Wave 105 변경에
포함하지 않으며 runtime AI 주문판단도 추가하지 않는다.
