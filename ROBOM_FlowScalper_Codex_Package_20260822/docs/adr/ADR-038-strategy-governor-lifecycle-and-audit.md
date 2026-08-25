# ADR-038. 전략 Governor 생명주기와 불변 변경 증거

- 상태: Accepted
- 날짜: 2026-08-25
- 범위: Strategy Registry, 독립 PAPER 계좌, 설정 API와 전략 화면

## 배경

최근 승률만 보고 전략을 켜거나 끄면 작은 표본과 비용 변동에 따라 상태가 반복해서 뒤집힌다. 기존 `ACTIVE`·`SHADOW`·`OFF` mode만으로는 연구 후보, 자연표본을 모으는 도전자, 기술 결함 격리와 근거를 보존한 퇴역을 구분할 수도 없었다. 두 탭의 동시 변경, 자동 평가와 사용자 설정 충돌, 재시작 뒤 과거 revision 복원도 명시적인 계약이 필요했다.

## 결정

1. 전략 생명주기를 `RESEARCH`, `SHADOW`, `CHALLENGER`, `ACTIVE`, `QUARANTINED`, `RETIRED`로 분리한다. 실행 mode는 생명주기에서 결정하며, `SHADOW`와 `CHALLENGER`는 BASE·STRESS 독립 PAPER 계좌를 계속 사용한다.
2. 자동 승격은 BASE·STRESS 비용후 기대값과 PF, OOS 기대값 하한, 파라미터 강건성, 위험계약, 독립 기간, DSR, PBO를 통과해야 한다. `SHADOW`에서 `CHALLENGER`로 갈 때는 30건·7일·2개 레짐의 자연 `LIVE_PUBLIC` 표본과 cooldown을 추가로 요구한다. `ACTIVE` 교체는 100건·21일·3개 레짐, 더 엄격한 DSR/PBO/PF와 전략 상관 한도를 요구한다.
3. 최소 표본 전에 성능 때문에 자동 격리하지 않는다. 성능 격리는 전체 OOS와 최근 OOS의 비용후 기대값·PF가 두 평가 주기 연속 악화된 경우에만 허용한다. 데이터 누수, 원장 오염, 비정상 PAPER 주문 루프와 명시적 운영 결함은 즉시 기술 격리한다.
4. champion 교체는 기존 `ACTIVE`를 `CHALLENGER`로 내리고 새 후보를 `ACTIVE`로 올리는 모든 revision과 manual lock을 먼저 검증한 뒤 한 원자적 Registry 변경으로 적용한다. 실행 중 source code는 변경하지 않는다.
5. 모든 설정은 CAS revision, `USER_UI`·`AUTO_GOVERNOR`·`RECOVERY`·`MIGRATION` 주체, 이유와 시각을 저장한다. 사용자 `manual_lock`은 자동 Governor가 덮어쓰지 못한다.
6. rollback은 과거 행을 삭제하거나 현재 revision을 되감지 않는다. 선택한 과거 내용을 새 revision으로 복원하고 원장과 incident audit에 목표 revision을 남긴다. 재시작은 현재 Run의 모든 revision을 순서대로 검증해 변경 이력과 rollback 대상을 복구한다.
7. 열린 PAPER 포지션은 전략이 격리되거나 mode가 바뀌어도 기존 TP·SL·근거감쇠 계획으로 계속 관리한다. Registry 변경은 새 진입 자격만 바꾼다.

## 결과와 한계

현재 런타임 화면에서 계산하는 자연표본만으로는 OOS 하한, 강건성, DSR와 PBO를 새로 만들어낼 수 없다. 해당 연구 증거가 없으면 상태는 `NOT_PROVEN`이고 자동 승격하지 않는다. 코드 경로와 회귀 테스트 통과는 수익성 증거가 아니며, 실제 저장 공개시장 연구와 장기간 자연표본은 별도로 기록해야 한다.
