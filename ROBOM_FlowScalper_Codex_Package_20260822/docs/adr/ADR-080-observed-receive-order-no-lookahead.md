# ADR-080. 공개시장 연구와 replay의 실제 수신 순서 고정

## 상태

Accepted, implementation and regression tests complete, archive screening in progress.

## 문제

시장 이벤트에는 거래소 사건시각 `venue_ts_ms`와 이 컴퓨터가 실제로 받은 순서를 나타내는
`receive_ts_ms`와 `receive_monotonic_ns`가 함께 저장된다. 기존 SQLite 조회,
ReplayEngine, Parquet 연구 reader는
거래소 사건시각을 먼저 정렬했다. 네트워크에서 늦게 도착한 과거 사건은 실제 결정 뒤에
관측됐더라도 거래소 시각만으로 앞에 배치될 수 있다. 이 순서는 결정 재현에는 편리하지만
실제 관측 정보집합을 보장하지 못하므로 전략 성과 검증의 no-lookahead 경계를 약화한다.

## 결정

1. 전략 정보집합과 replay 처리 순서는
   `(receive_ts_ms, receive_monotonic_ns, venue_ts_ms, event_id)`로 고정한다.
2. `venue_ts_ms`는 candle, split, purge·embargo, 화면 축과 시간범위 필터에 계속 사용한다.
3. 신규 Parquet batch에는 `receive_ts_ms`와 `receive_monotonic_ns`를 top-level column으로도
   보존한다. 기존 archive는 checksum이 묶인 `payload_json`의 지연과 거래소 시각으로
   수신 wall-clock을 복구한다.
4. SQLite와 archive가 함께 있는 제한 조회는 exchange-time metadata만 보고 뒤 archive를
   건너뛰지 않는다. 관련 batch를 검증·병합한 뒤 실제 수신 순서에서 최종 limit을 적용한다.
5. 수신 wall-clock이 같은 이벤트는 monotonic 시각, 거래소 시각, event ID로
   결정적으로 tie-break한다. wall-clock이 먼저라 process·운영체제 재시작에서도
   monotonic clock reset이 이전 이벤트 앞으로 소급되지 않는다.
6. 수신순 순회에서 한 이벤트의 `venue_ts_ms`가 연구 종료경계를 넘었다고
   전체 순회를 종료하지 않는다. 해당 이벤트만 제외하고, 뒤에 수신된 느린
   과거 exchange-time 이벤트를 계속 확인한다.

## 영향

- 늦게 수신된 사건이 과거 결정 전에 나타나는 look-ahead 경로를 차단한다.
- 과거 exchange-time-first checksum과 새 checksum은 달라질 수 있다. 이는 성과 개선 증거가
  아니며 새 규칙으로 전수 재계산하기 전의 전략 결과는 `NOT_PROVEN`이다.
- 제한 replay가 일부 archive를 더 읽을 수 있어 조회비용은 늘 수 있다. 무결성 우선이며,
  향후 receive-range manifest를 별도로 검증하기 전에는 부정확한 조기종료를 복구하지 않는다.

## 검증 상태

- Ruff, backend 대상 mypy, `py_compile`, `git diff --check`는 통과했다.
- receive 순서가 exchange 순서와 반대인 SQLite·ReplayEngine·Parquet 회귀 테스트를 작성했다.
- 종료경계 밖 exchange-time 이벤트 뒤의 느린 경계 내 이벤트가 계속
  처리되는 가벼운 직접 호출을 PASS했다.
- 100후보 runner가 실제로 import하는 공통 archive reader와 호가·체결 변환기까지 trial
  manifest source checksum에 포함했다. 공통 변환기는 비유한 값과 0 이하 공개 체결을
  candle 생성 전에 거부하며, 해당 표적 회귀 1건은 PASS했다.
- 관련 표적 296건과 backend 전체 635건은 PASS했다. 13개 Run dataset freeze와
  200,000-event bounded 수신순 benchmark도 PASS했다. 전체 Stage 1 archive screening과
  별도 ReplayEngine 결정성 검증은 아직 진행 중·`NOT_RUN`이다.
