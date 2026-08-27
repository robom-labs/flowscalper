# ADR-069 거래 기록·집중 재생의 빠른 읽기와 launchd 안전 인계

- 상태는 승인이다.
- 날짜는 2026-08-27이다.

## 맥락

ADR-068의 선택적 cache 쓰기 제거와 비용 분리 뒤 commit `1af448f3e2690fe382d128b6eed181c6c3d0ec80`
실제 릴리스를 열었다. 원장 총비용 분리는 맞았지만 첫 ZECUSDT 집중 재생은 브라우저에서 41초,
같은 API 재호출은 22.160초가 걸렸다. 계측 결과 같은 전략의 비교 거래 조회 한 번이 5.931초였고,
저우선순위 `nice(19)` 공용 replay worker를 UI 조회도 기다렸다.

전용 비교 인덱스를 추가한 뒤 API는 1.685초까지 줄었지만 재시작 직후 거래 기록 cache가
61,917.006ms 동안 준비되며 공유 원장 read lock을 점유했다. SQLite query plan은 focus 비교용
인덱스를 전체 거래 시간순 조회에도 사용하고 별도 temp B-tree를 만드는 상태였다. 이때 기록
화면은 빈 것처럼 오래 보였고 집중 재생도 같은 read lock을 기다렸다.

불변 릴리스 전환에서는 `launchctl bootout` 직후 이전 Python PID의 안전 종료가 끝나기 전에
`bootstrap`을 호출해 macOS error 5가 반복됐다. 수동 재등록은 이전 PID가 사라진 뒤 항상
성공했다. stage 명령의 frontend build stdout도 JSON 결과 파일 앞에 섞여 기계판독을 깨뜨렸다.

## 결정

- `trades`와 `shadow_trades`에 전체 history 시간순 인덱스와 Run별 history 시간순 인덱스를
  각각 둔다. focus 비교는 Run·전략 전용 인덱스와 SQL의 종목·방향 필터를 사용한다.
- 거래 집중 재생은 최대 2,000개 저장 이벤트와 한 거래·후보·비교 행만 읽는 제한된 UI 작업으로
  유지하고 `asyncio.to_thread`에서 기존 query-only 연결을 사용한다. LIVE에서는 선택적 cache를
  쓰지 않는다. 전체 Run ReplayEngine과 정밀 timeline은 계속 `nice(19)` 격리 process와 replay
  lock을 사용한다.
- 교체된 focus process 함수와 회귀는 같은 변경에서 제거하고, bounded reader 경로와 index query
  plan을 새 회귀로 고정한다.
- 설치기는 기존 service PID를 `kill -0`으로 최대 60초 기다린 뒤 bootstrap한다. 그 뒤에도 남는
  일시 오류만 1초 간격 최대 3회 재시도하고, 세 번 실패하면 명시적으로 종료한다.
- frontend build stdout·stderr는 stage JSON의 stderr 쪽으로 분리하고, installer는 결과 JSON의
  `ACTIVATED` 상태를 파싱해 검증한 뒤에만 서비스를 전환한다.

## 결과

실제 commit `3e4e728b7524a53965014f49c526042fb1dc07f5` 릴리스에서 dashboard 거래 cache는
61,917.006ms에서 6,610.263ms로 줄었다. 거래 기록 81건은 실제 브라우저에서 308ms에 표시됐고,
ZECUSDT 집중 재생은 2,223ms에 준비됐다. 직접 API는 ZECUSDT 1.051초, SOLUSDT 0.190초였으며
두 거래 모두 진입·종료 수수료 합과 슬리피지 합이 불변 원장 총액과 일치했다. 실제 화면에서
진입 수수료 0.025 USDT, 종료 수수료 0.025 USDT, 28초 보유, 최종 순손익 -0.0495 USDT를
확인했고 콘솔 오류는 0이었다.

300.029초 무부하 관찰은 event +23,229, 최대 queue 1, 처리 p95 42.443ms, trade p95
81.730ms였다. 비계획 reconnect·gap·resync·drop·저장결함·buffer drop은 0이고 실제 주문과
인증은 계속 false였다. 전략 임계값, 진입·TP·SL·청산정책, 비용률, 계좌, 위험예산은 바꾸지
않았으며 수익성은 `NOT_PROVEN`이다.
