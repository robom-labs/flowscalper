# ADR-008. 비차단 대용량 원장, 자동 복구 실행, 쉬운 고정형 홈

## 상태

승인 및 구현 완료. 2026-08-23.

## 배경

장시간 공개시장 Run으로 `market_events`가 수백만 건, SQLite 파일이 약 1.2GB까지 증가했다. 과거 재생 목록은 Run마다 `COUNT(*)`를 수행했고 이 동기 조회가 FastAPI event loop에서 실행되어 홈페이지와 WebSocket까지 함께 멈췄다. 종목 스캐너와 차트는 같은 grid 행 높이에 영향을 주어 데이터 갱신 때 비율이 흔들렸고, 기본 화면에는 비전문가에게 불필요한 점수·비용·전략 진단이 과도하게 노출됐다. 수동 Terminal 실행은 프로세스 종료나 재로그인 뒤 사이트를 복구하지 못했다.

## 결정

1. SQLite schema version 6에 `market_event_stats`와 불변 `market_event_archives` manifest를 추가한다. 신규 이벤트 통계는 row trigger 대신 저장 batch 단위로 누적해 고빈도 INSERT 비용을 없앤다.
2. 기존 대용량 Run은 전수 재계수하지 않는다. 저장 이벤트 존재만 sentinel로 표시하고 정확한 과거 수가 필요하지 않은 목록 UI에서는 `저장됨`으로 표현한다.
3. replay·analytics 원장 조회는 `asyncio.to_thread`에서 실행해 느린 저장장치 조회가 홈페이지, 상태 API, WebSocket을 막지 않게 한다.
4. 종목 목록은 알파벳 순서와 고정 내부 스크롤을 사용한다. 기본 정보는 종목·상승/하락 관찰·진입 준비 상태만 두고 전략·비용·손익비·거절 이유는 종목별 상세에 둔다.
5. 차트는 고정 높이와 ResizeObserver의 animation-frame 병합을 사용한다. 실제 캔들·거래량·5선·10선을 기본 표시하고 20선·60선·호가선은 사용자가 선택한다. 이동평균은 선택한 시간구간의 캔들 수 기준이며 일봉이 아닌 구간에서 `5일선`으로 오인시키지 않는다.
6. 홈은 프로그램 상태, 진행 거래, 완료 거래, 현재 순손익, 정밀 관찰 종목을 먼저 표시한다. 연결 지연과 전문 통계는 접이식 상세로 분리한다.
7. macOS LaunchAgent는 로그인 후 고정 localhost `127.0.0.1:8870` 서버를 자동 시작하고 종료 시 재시작한다. canonical 소스와 고빈도 공개시장 archive는 외장 APFS 작업공간에 유지한다. 외장 볼륨에서 대형 Python binary를 직접 불러올 때 1분 이상 시작이 지연되어 약 283MB 실행환경 복사본, plist, bytecode cache와 로그를 내장 Application Support에 둔다.
8. 자동 서비스는 안전한 `READY`로 시작한다. 사용자가 `자동 관찰 시작`을 누르기 전에는 공개시장 PAPER Run을 자동 생성하지 않는다.
9. 자동 서비스는 기존 1.3GB 원장을 삭제·덮어쓰기·강제 재계수하지 않는다. macOS LaunchAgent가 One Touch 직접 경로를 `Operation not permitted`로 거부했고, APFS sparsebundle의 SQLite checkpoint가 공개시장 유입을 따라가지 못한 실측 결과에 따라 소형 거래 상태·설정·manifest용 활성 SQLite만 내장 `~/Library/Application Support/ROBOM FlowScalper/active-ledger/run-ledger.sqlite3`에 둔다. 이전 `data/run-ledger.sqlite3`와 실패 진단 원장은 외장 과거 기록으로 보존한다.
10. supervisor 지연 백분위는 매 이벤트마다 2,000개 표본을 정렬하지 않고 256개 신규 표본마다 갱신한다. 단일 임계 지연은 즉시 진입잠금을 걸고, p95가 회복된 갱신 전까지 잠금을 유지한다.
11. 120초 피처 계산은 OFI·체결·호가창을 각각 한 번만 순회한다. 체결·OFI는 최대 10초, refill/cancel은 3초, 가격 경로만 120초를 보존해 계산 결과는 유지하면서 CPU·메모리 증가를 제한한다.
12. deep 호가 수신과 PAPER 포지션 관리는 계속 250ms 경로를 사용하되, A/B/C/D 전체 피처·전략 재평가는 종목별 최대 500ms 주기로 제한한다. 250ms 입력·계획 체결 지연 계약은 유지하면서 화면과 실행 루프가 CPU 포화로 수십 초 뒤처지는 현상을 막는 쪽을 우선한다.
13. 공개시장 원본 이벤트는 리플레이에 필요한 상위 10단계 호가를 보존한 뒤 1,000건 단위 ZSTD Parquet으로 외장 `data/market-parquet-v6`에 기록한다. SQLite에는 batch checksum·경로·시간범위·종목·event type manifest와 PAPER 거래 상태만 저장한다. 읽을 때는 허용된 외장 root 내부 경로인지, 각 row checksum과 batch checksum이 모두 일치하는지 확인한 뒤 기존 SQLite event와 시간순으로 병합한다.
14. 5,000건 Parquet batch는 압축 순간 Python GIL 경합으로 실행 경로 p95가 5,978ms까지 상승해 폐기했다. 1,000건 batch는 4분 이상 LIVE에서 p95 70~140ms 구간, pause·drop·gap·reconnect·persistence fault 0을 유지했으므로 채택했다.

## 결과와 한계

- 대용량 원장에서도 `/api/replay/runs`가 `market_events` 본문을 스캔하지 않는다.
- 느린 replay 조회와 LIVE 화면 갱신이 분리된다.
- scanner 길이와 상세 열림이 차트 크기를 바꾸지 않는다.
- 최신 LIVE Run의 고빈도 이벤트는 활성 SQLite `market_events`에 0건이고 외장 Parquet에 저장된다. 내부 SQLite 증가량은 4분 측정에서 1,232,896 bytes였고, 외장 archive 증가량은 37,984 events에 약 4,036KiB였다.
- 실제 주문, 인증, private API 경로는 계속 0이다.
- 컴퓨터가 꺼져 있거나 외장 APFS 작업공간이 마운트되지 않았으면 localhost 사이트를 제공할 수 없다. 로그인·재부팅·프로세스 종료 뒤에는 LaunchAgent가 외장 작업공간을 사용할 수 있는 즉시 재시도한다.
- 자동 서비스의 Python 실행환경 복사본과 소형 활성 SQLite는 내장에 있지만 소스와 고빈도 archive는 외장에 있다. 따라서 외장 볼륨 없이 독립 실행되는 복제본은 아니다.
- 기존 Run의 정확한 전체 event count를 표시하기 위해 1.2GB 본문을 강제 재스캔하지 않는다. replay는 종목별 최대 2,000개 표시 제한과 저장 이벤트 자체를 사용한다.

## 검증

- schema v6 migration·batch 통계·archive manifest 불변성·중복 삽입·row/batch checksum·경로 이탈 차단·필터·limit·리플레이 회귀테스트.
- 느린 replay 목록을 강제로 대기시킨 동안 `/api/status`가 250ms 안에 응답하는 비차단 테스트.
- READY 상태의 실제 1.2GB 원장에서 `/api/replay/runs` 2.213ms, `/api/dashboard` 2.110ms 응답 확인. 활성 LIVE 목록은 동기 flush 없이 메모리 buffer와 O(1) 통계를 결합한다.
- LaunchAgent PID 종료 후 새 PID와 HTTP 200 자동 복구를 확인했고, 로그인 세션에서 `KeepAlive`·`RunAtLoad` 상태와 고정 포트 8870을 확인했다.
- dashboard 왕복시간을 보정한 시각 차이는 로컬 KST +22.7ms, Binance +43.6ms, Bybit +40.4ms였다.
- 최종 `run-9b9d508c689d`는 4분 이상, 37,984 events 측정 구간에서 p95 140ms, pause false, queue·drop·gap·reconnect·fault 0이었다. 측정 뒤에도 실행을 유지해 77,274 events를 147개 Parquet, 7,987,803 bytes로 보존했고 SQLite raw event는 0이었다.
- backend 105 PASS, React 5 PASS, Ruff·mypy·ESLint·TypeScript·production build·security scan·shell syntax·installed plist·SQLite quick check·archive replay가 통과했다.
- Codex in-app browser의 admin-enforced policy 확인이 불가능해 수정 후 DOM·screenshot 재캡처는 `BLOCKED`로 남겼다. 보안 제어를 우회하거나 기존 screenshot을 새 증거로 재사용하지 않았다.
