# ADR-082. 데이터 건강 진입잠금의 fresh-depth 자동복구

- 상태는 `ACCEPTED_IMPLEMENTATION_NOT_DEPLOYED`다.
- 범위는 LIVE 공개시장 관찰 중 PAPER 신규 진입 안전잠금이다.
- 실제 주문·인증·private API를 추가하지 않는다.

## 문제

`HEALTH`나 sequence-invalid 시장 이벤트가 들어오면 런타임은
`ENTRY_LOCK_DATA_HEALTH`를 추가하고 PAPER 신규 진입을 멈춘다. 종목별
`data_gap_since_ms`는 다음 fresh sequence-valid depth에서 제거됐지만, 전역
health flag를 제거하는 경로가 없었다. 그 결과 모든 scanner 종목이
`HEALTHY`로 복구해도 화면이 `작동 중 · 안전 대기`에서 자동으로
벗어나지 못할 수 있었다.

## 결정

1. fresh sequence-valid depth가 실제 호가·feature·포지션 건강 경로를 통과한 뒤
   종목의 gap을 제거한다.
2. 전체 `data_gap_since_ms`가 비었고 stale trade 종목도 0일 때만
   `ENTRY_LOCK_DATA_HEALTH`를 제거한다.
3. 해제 후에도 supervisor, critical lag, queue, persistence, storage, recovery,
   risk fault와 사용자 수동일시정지를 다시 확인한다. 이 중 하나라도 남으면
   진입을 재개하지 않는다.
4. 다른 종목의 fresh depth로 남은 gap을 풀지 않는다.
5. 해제 사실을 `MARKET_DATA` 감사 로그에 남긴다.
6. 호가 가격·수량·양방향·비교차 조건을 `BookSnapshot` 및 `BookFrame`으로
   먼저 검증한 뒤에만 `latest_books`·PAPER 체결·포지션 경로에 전달한다.
   교차호가·0 이하 수량·비유한 가격이나 수량은 실행 호가로 저장하지 않는다.
   정상 레벨은 bid 내림차순·ask 오름차순으로 정규화한 같은 snapshot을 피처와
   PAPER 체결 양쪽에 전달한다.
7. 피처 입력 결함은 종목별로 보존하고, 결함 종목 모두의 정상 snapshot이
   확인된 뒤에만 `ENTRY_LOCK_FEATURE_INPUT`을 제거한다.
8. 비유한 값이나 0 이하 가격·수량의 공개 체결은 캔들·피처·전략 경로에
   넣지 않고 같은 피처 입력 안전잠금으로 격리한다. 다음 정상 호가 snapshot이
   전체 검증을 통과해야 복구한다.

## 검증과 배포 경계

- 두 종목 sequence gap이 모두 fresh depth로 복구되기 전에는 잠금이 풀리지
  않고, 둘 모두 복구된 뒤 gap·flag·paused가 해제되는 회귀를 작성했다.
- 교차호가와 수량 0 호가는 latest book·PAPER 실행 경로 전에 거부된다.
  두 종목이 동시에 실패했을 때 한 종목만 정상화해서는 피처 잠금이 풀리지
  않고, 둘 모두 정상 snapshot을 만든 뒤에만 복구되는 회귀를 작성했다.
- 다종목 잠금·비정상 호가·비유한 체결 표적과 관련 296건, backend 전체 635건은
  PASS했다. Ruff·mypy 106 source·`py_compile`도 PASS했다.
- 변경 릴리스 장시간 실제 복구·브라우저는 아직 `NOT_RUN`이다.
- 수정 전 불변 release `6caad216…`의 6시간 observer는 실제 21,600.025초·720표본을
  중단 없이 완료했고 `FAIL`을 기록했다. event 1,806,796건·전략평가 5,834,040회는
  전진했지만 sequence gap·resync 각 1건 뒤 최종 scanner 12종목이 모두 `HEALTHY`인
  표본에서도 `ENTRY_LOCK_DATA_HEALTH`와 `SAFETY_WAITING`이 남았다. 이 기준 실패를
  삭제하거나 새 수정의 PASS로 바꾸지 않는다. 원본은
  `evidence/WAVE99_POST_QUARANTINE_CLEAN_6H.json`이다.
