# ADR-051. 실행·replay 상태 전환 감사 정규화

## 상태

승인. 2026-08-27.

## 배경

실제 활성 원장의 `PAPER_ENTRY_INTENT_TRANSITION` 4행은 `transition_id`, 이전·새 상태, 원인, 한국어 설명, actor, Run·전략·계좌·종목, 요청·응답 revision과 되돌릴 수 있는지 여부를 각각 기록했다. 반면 `CONTROL_STATE_TRANSITION` 4행과 `REPLAY_STATE_TRANSITION` 17행은 작업 전체 snapshot과 history만 저장했고 같은 정규 필드는 한 행에서 직접 조회할 수 없었다.

전체 작업 snapshot은 복구와 진단에 유용하지만, 상태 전환별 불변 감사 계약으로는 부족하다. 특히 이전 상태와 새 상태, 요청·응답 revision, terminal 여부를 소비자가 history 배열에서 다시 추론해야 했다.

## 결정

1. ControlOperation과 ReplayOperation의 기존 incident ID, category, 전체 작업 snapshot과 history를 보존한다.
2. 각 신규 incident payload에 `transition_id`, `previous_state`, `new_state`, `occurred_ts_ms`, `cause`, `cause_code`, `description_ko`, `actor`, `run_id`, `strategy_id`, `account_id`, `symbol`, `request_revision`, `response_revision`, `reversible`을 같은 행에 추가한다.
3. 최초 전환의 이전 상태는 `NONE`, 요청 revision은 0으로 기록한다. 이후 전환은 직전 history revision을 요청 revision으로 사용한다.
4. `COMPLETED`, `FAILED_RETRYABLE`, `FAILED_BLOCKED`, `CANCELLED`는 terminal이라 `reversible=false`로 기록한다. 진행 중 상태는 `reversible=true`다.
5. control은 연결된 현재 Run이 있을 때만 `run_id`를 기록한다. replay는 source Run과 선택 종목을 기록한다. 이 작업에 해당하지 않는 strategy와 account는 명시적인 null로 둔다.
6. 과거 incident를 다시 쓰거나 schema migration을 하지 않는다. 정규 계약은 배포 뒤 생성되는 신규 행에만 적용하며 기존 전체 snapshot 읽기 호환성을 유지한다.
7. PAPER 진입·청산·비용·전략 임계값·Registry·Governor·원장 거래와 실제주문 0 경계는 변경하지 않는다.

## 결과

- control·replay·PAPER 진입 의도를 같은 종류의 상태 전환 필드로 조회할 수 있다.
- 기존 operation snapshot과 history 소비자는 변경 없이 동작한다.
- actor·원인·revision·terminal 여부를 전환 행 자체로 감사할 수 있다.

## 검증 경계

단위·HTTP 통합·복구·storage 회귀는 신규 payload 계약과 기존 호환성을 검증한다. 현재 설치 서비스는 이 결정 이전 commit을 실행 중이므로 배포 뒤 실제 원장에 생성된 신규 control·replay 행 확인은 `NOT_RUN`이다. 실행 중인 Wave 49의 6시간·24시간 observer도 기존 설치 commit의 안정성 범위이며 이 변경의 배포 증거가 아니다.
