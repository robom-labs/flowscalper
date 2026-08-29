# ADR-097. 누적 회귀계약과 전략후보 중복 방지

## 상태

채택한다. 기존 Run·거래·판단·연구결과는 삭제하거나 새 성과로 덮지 않고 계속 누적한다. 해결된
결함은 설명만 남기지 않고 실제 test anchor와 묶어, 이후 변경에서 해당 테스트가 사라지거나
표본·PAPER 안전 기준이 약해지면 CI가 실패하도록 한다.

## 문제

여러 Wave에서 시작 상태, 시간동기화, 대시보드 크기, 거래기록 범위, 리플레이 커서와 조기종료를
각각 고쳤다. 전체 회귀검사가 있어도 어떤 테스트가 어떤 과거 결함을 보호하는지 명시적 연결이
없으면, 기능을 교체할 때 관련 테스트까지 함께 지워 과거 증상을 다시 만들 수 있다.

전략 연구도 같은 문제가 있다. 현재 Registry와 100후보 funnel에는 OFI, aggressor flow,
microprice, 다중호가, VWAP, 추세·돌파·반전과 여러 청산방식이 이미 있다. 이름과 임계값만 바꾼
후보를 계속 추가하면 실행속도가 느려지고 다중시험·선택편향만 커진다. 높은 표시승률을 빨리
만들기 위해 같은 시장기회를 BASE·STRESS 또는 여러 유사 전략에서 중복 집계해서도 안 된다.

## 결정

1. `config/regression_contracts.json`을 해결된 고위험 결함의 append-only 색인으로 둔다.
2. 각 계약은 사용자에게 보였던 증상, 수정 근거와 실제 backend·frontend·browser test anchor를
   한 개 이상 가진다.
3. `scripts/verify_regression_contracts.py`는 계약 ID 중복, 파일·anchor 삭제, 필수 명령 누락,
   순위 최소 30개 독립기회와 `NOT_PROVEN`, PAPER·실주문 0 경계를 fail-closed로 검증한다.
4. GitHub Actions는 구조 검증 뒤 기존 전체 backend·frontend·browser 회귀를 그대로 실행한다.
   구조 PASS는 실제 테스트 PASS를 대신하지 않는다.
5. 새 결함을 고치면 같은 Wave에서 재현 테스트와 계약을 추가한다. 기존 계약은 버전관리 없이
   삭제하지 않는다. 설계상 폐기해야 하면 대체 anchor와 이유를 같은 변경에 기록한다.
6. 후보는 기존 100개 및 Registry의 feature·방향·시간축·진입·청산 조합과 먼저 대조한다.
   동일 가설의 이름 변경이나 결과를 본 뒤의 임계값 변경은 새 독립 근거가 아니라 다중시험으로
   기록한다.
7. 동결된 공통 baseline은 commit, reader checksum, archive byte, Run·이벤트 순서가 같을 때만
   여러 veto 후보에 재사용한다. 이 방식으로 반복 계산을 줄이되 성과 gate는 낮추지 않는다.
8. 새 데이터는 완료된 불변 구간으로만 연구 manifest에 추가한다. 실행 중 파일이나 미래결과를
   보며 Train·Validation 경계를 다시 쓰지 않는다.
9. `backend/app/research/trial_history.py`는 가설·실제 파라미터·동결 데이터·구현·비용 모델 지문을
   함께 비교한다. 같은 다섯 지문의 완료시험은 파일명만 바꿔 다시 실행하지 못한다.
10. 같은 전략·파라미터의 새 표본은 기존 데이터 종료보다 엄격히 뒤까지 늘어난 새 dataset 지문일
    때만 순방향 갱신으로 허용한다. 이전 Run ID와 checksum 집합을 모두 포함해야 하므로 과거 Run을
    빼고 다른 구간을 골라 승률을 다시 만드는 재표집은 차단한다. 실패·중단시험 재시도, 코드수정
    재검증, 비용모델 재검증과 실제 파라미터 변형은 별도 사유로 구분하고 모든 변형을 다중시험 수에
    포함한다.

## 수익성 경계

- 독립 시장기회 30건 전에는 전략 순위와 70% 달성을 주장하지 않는다.
- 70%는 BASE·STRESS 각각의 최소 조건일 뿐이다. 비용 후 기대값·순손익·Profit Factor,
  bootstrap 하한, DSR, PBO, drawdown과 종목·레짐·시간 집중도까지 함께 통과해야 한다.
- 기준을 못 넘은 전략은 거래·판단 근거를 남긴 채 `RETIRED/OFF` 또는 미등록으로 보존한다.
- 실제 주문, private API, 인증, API Key, secret, wallet과 입출금 경로는 계속 0이다.

## 검증

- 계약 구조 자체는 `backend/tests/test_regression_contracts.py`가 정상·anchor 삭제·순위표본 약화를
  검사한다.
- `backend/tests/test_research_trial_history.py`는 완료시험 중복, 과거구간 재표집, 새 데이터 갱신,
  실제 파라미터 변형과 PAPER 안전 경계를 검사한다.
- `make regression-contracts`는 구조와 연결을 빠르게 확인한다.
- 실제 동작은 기존 `make test`, `make e2e`, lint, typecheck, build, PAPER safety, security와
  실제 설치 브라우저 검증으로만 PASS를 판정한다.
