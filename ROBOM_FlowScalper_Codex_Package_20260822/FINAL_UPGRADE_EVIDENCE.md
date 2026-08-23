# ROBOM FlowScalper 0.2.0-paper 최종 업그레이드 증거

작성일은 2026-08-23이며, 기준 소스는 기존 `0.1.0-paper`, 구현 기준은 `IMPLEMENT.md`와 `UPGRADE_EXEC_PLAN.md`, 진행 기준은 `PLANS.md`다. 문서에 없는 수익성·안전성·실기기 호환성은 주장하지 않는다.

## 1. 제품 경계와 최종 상태

- 실제 Binance USDⓈ-M 또는 별도 Run의 Bybit Linear 공개 시장데이터만 읽는다.
- 주문·체결·포지션·손익은 1,000 USDT 내부 PAPER 계좌에서만 계산한다.
- 거래소 로그인, API Key, 비밀키, 지갑, private endpoint, 실제 주문 경로는 없다.
- Fresh 실행은 `READY`, 1,000.00 USDT, 총손익·순손익·수수료·슬리피지·거래 0에서 시작한다.
- `DEMO_FIXTURE`는 LIVE Run·원장·성과에서 분리된다.
- 실제 공개 REST 메타데이터와 sequence-valid WebSocket depth가 확인되기 전에는 LIVE로 표시하지 않는다.
- 지연·gap·저장 실패·디스크 압박·복구 불일치는 신규 PAPER 진입을 fail-closed로 잠근다.

## 2. 외장하드 정식 위치

| 항목 | 실제 값 |
|---|---|
| 물리 외장하드 | `/Volumes/One Touch`, ExFAT, 약 4TB |
| 전용 작업 이미지 | `/Volumes/One Touch/ROBOM_AUTOTRADING/FlowScalper_v0.2_20260822/ROBOM_FlowScalper_Workspace.sparsebundle` |
| 정식 APFS 작업공간 | `/Volumes/ROBOM_FLOWSCALPER/01_WORKSPACE/자동매매` |
| 정식 프로젝트 | `/Volumes/ROBOM_FLOWSCALPER/01_WORKSPACE/자동매매/ROBOM_FlowScalper_Codex_Package_20260822` |
| Finder 실행기 | `/Volumes/One Touch/ROBOM_AUTOTRADING/FlowScalper_v0.2_20260822/START_ROBOM_FlowScalper.command` |
| 호환 링크 | `/Users/runner706/Documents/ChatGPT/자동매매` → 외장 작업공간 |

정식 Git 작업과 완성본은 외장하드에 있다. ExFAT가 실행권한·심볼릭 링크를 보존하지 못하는 문제를 피하기 위해 실제 저장소는 외장하드 안의 APFS sparsebundle에 두었다. 실행기는 예상 APFS Volume UUID `CFA4ACD9-40F2-4825-845E-137F76AA1C62`가 일치할 때만 앱을 연다. 이동 증거는 `evidence/EXTERNAL_MIGRATION_EVIDENCE.json`에 있다.

## 3. 빌드 식별자와 환경

| 항목 | 실제 값 |
|---|---|
| 버전 | `0.2.0-paper` |
| 기준 커밋 | `c1de1165bd25d4ebba7346416f2fb6aa8f1e69d7` |
| 릴리스 소스 커밋 | `6a3eb0e9d781dca54ba1aca766264c3998ba34ee` |
| 운영체제 | macOS 26.5.2, build 25F84, arm64 |
| Python / uv | 3.12.13 / 0.11.26 |
| Node.js / pnpm | 26.4.0 / 9.15.9 |
| Playwright | 1.62.1 |

## 4. 구현 결과

### LIVE와 데이터

- 일회성 LIVE bootstrap을 FastAPI 수명주기와 결합된 장시간 WebSocket supervisor로 교체했다.
- 공개 wide 최대 50종목을 계속 감시하고 deep 기본 10종목을 sequence-valid order book으로 정밀 분석한다.
- Binance `/public` depth와 `/market` trade를 분리하고 Bybit public linear를 별도 Run fallback으로 유지한다.
- bounded queue, reconnect, resync, gap, drop, lag, 계획 rotation을 계측한다.
- 실제 agg trade에서 `1s, 5s, 15s, 30s, 1m, 3m, 5m, 10m, 15m` 캔들을 만든다.

### 전략·계획·PAPER 체결

- 확장 가능한 A/B/C/D Strategy Registry를 같은 LIVE PAPER 런타임에 연결했다.
- 전략별 `ACTIVE`·`SHADOW`·`OFF`와 LONG·SHORT 허용을 Run 설정과 원장에 저장한다.
- 각 전략은 BASE·STRESS 두 독립 shadow 가상계좌를 가져 총 8개 shadow 계좌가 격리된다.
- C의 mean-reversion과 D의 liquidity-vacuum 전략은 PAPER 전용이며 실제 주문 경로가 없다.
- 적격 후보는 진입 전에 entry·worst entry·TP1·TP2·SL·수량·최대손실·비용·edge-decay를 불변 `CandidatePlan`으로 확정한다.
- 250/500ms 지연 뒤 LONG 진입은 실제 ask, SHORT 진입은 실제 bid 깊이를 IOC로 소비하고 부분체결을 지원한다.
- TP1·TP2·SL·edge decay·stale exit, 실현·미실현 순손익, 수수료, 슬리피지를 main·shadow 원장에 연결한다.
- 120초 고정 강제종료는 없고, initial stop은 불리한 방향으로 넓어지지 않는다.

### 원장·리플레이·성과

- SQLite schema v6에 PAPER 상태, candle, candidate, strategy setting/account, main·shadow trade, replay 목록용 `market_event_stats`와 불변 archive manifest를 Run 범위로 저장한다. 자동 서비스의 고빈도 공개시장 event는 외장 ZSTD Parquet으로 분리한다.
- backend ReplayEngine은 저장 event를 같은 A/B/C/D·후보·PAPER 실행 경로에 다시 넣고 checksum과 결정 경로를 반환한다.
- 전략별 표본, 승률, 기대값, Profit Factor, 총·순손익, 비용, drawdown, BASE·STRESS, `CALIBRATING` 상태를 계산한다.

### UI·UX

- 라이브, 전략, 거래내역, 리플레이, 성과분석, 위험관리, 시스템의 한국어 7개 화면을 제공한다.
- Lightweight Charts 5.2.1 실제 캔들에 bid·ask·microprice, entry·TP1·TP2·SL, PAPER 체결·청산 마커를 표시한다.
- 초보자 요약을 기본으로 두고 queue·gap·원시 상태는 접이식 고급진단에 둔다.
- 데스크톱·태블릿·모바일의 핵심 제어 높이는 48px 이상이고 관찰된 console/page error는 0이다.

## 5. 실제 공개시장 저장과 결정적 리플레이

2026-08-22 Binance 공개 데이터 실행 결과는 다음과 같다.

| 항목 | 실제 값 |
|---|---:|
| 저장 DB | 23,490,560 bytes, SHA-256 `fee911e295563ad0105c1f0c291b45774a9fb1c3ad574812c6c69cf647a3afcd` |
| 공개 market events | 21,620 |
| 캔들 / 종목 | 53 / 50 |
| 전략 평가 | 3,224 |
| 적격 신호 / 계획 / main 거래 / shadow 거래 | 0 / 0 / 0 / 0 |
| 두 replay checksum | `b3eae11e3f77b9ea741197436619b8bcd3bf2c056246957d21dac14b99aab247` |
| 두 replay checksum 일치 | PASS |
| 실제 주문 / 인증 | false / false |

자연 적격신호가 발생하지 않았으므로 전략 기준을 낮추지 않았다. 거래 0이라는 결정도 같은 입력에서 두 번 동일하게 재현했다. 후보→계획→부분체결→TP1·TP2/SL→완료 거래 종단 경로는 결정론적 fixture와 통합테스트로 별도 검증했다.

## 6. 복구와 30분 soak

최초 30분 실행은 6,503,324 events, drop 0, queue max 2, memory +188.547MB였지만 임계 지연이 PAPER 진입잠금에 계속 결합되지 않은 문제를 발견해 `FAIL`로 보존했다. 원인을 수정한 60초 검증은 최대 p95 12,861ms의 12개 임계 표본에서 fail-open 0과 종료 시 supervisor lock·runtime pause 동시 유지를 확인했다.

최종 수정 후 30분 결과는 다음과 같다.

| 항목 | 실제 값 |
|---|---:|
| 상태 | `PASS` |
| 실제 시간 / events | 1,800.001초 / 3,120,256 |
| wide / deep | 50 / 10 |
| reconnect / gap / resync / drop | 39 / 0 / 0 / 0 |
| queue max / event memory max | 2 / 9,997 |
| max p95 / 임계 표본 / fail-open | 21,161ms / 171 / 0 |
| memory 증가 | 132.922MB |
| 전략 평가 / 적격 신호 / main 거래 | 252,552 / 16 / 0 |

상세 결과와 실패 이력은 `SOAK_TEST_REPORT.md`, 원본 표본은 `evidence/WAVE07_SOAK_*.json`에 있다. 6시간·24시간 실행기는 제공하지만 이번 세션에서는 실제 시간을 경과시키지 않았으므로 둘 다 `NOT_RUN`이다.

## 7. UI 브라우저 증거

| 검증 | 상태 | 실제 결과 |
|---|---|---|
| Playwright | PASS | desktop·tablet·mobile 3/3, console error 0, page error 0, root overflow 없음 |
| 핵심 조작 | PASS | pause/resume, 종목·시간구간, 전략 모드·방향, 원장 상세, backend replay, 성과·위험·진단 |
| 데스크톱 | PASS | `evidence/screenshots/wave06-dashboard-desktop.png`, 2816×1428, SHA-256 `66f6f5777e2d9bb781d7f1e04f1efd9da9062e62dda2c3b081251a75d91151c5` |
| 태블릿 | PASS | `evidence/screenshots/wave06-dashboard-tablet.png`, 820×2541, SHA-256 `62449852224cbff2df2d04639e222cb8559883f1e8dc10e13693c8c0857e3bea` |
| 모바일 | PASS | `evidence/screenshots/wave06-dashboard-mobile.png`, 390×2933, SHA-256 `f139dba8c0da8b5ada83b3bf8286cdbe14c0491bd6d826d2c60d4b47aa19817f` |

## 8. 최종 검증

| 명령 또는 검증 | 상태 | 실제 결과 |
|---|---|---|
| backend pytest / frontend Vitest | PASS | backend 105/105, frontend 5/5 |
| Ruff / ESLint | PASS | 오류 0 |
| mypy / TypeScript | PASS | mypy 68개 source 오류 0, TypeScript 오류 0 |
| Vite production build | PASS | 39 modules, JS 431.18kB, gzip 135.95kB |
| `make e2e` | PASS | fixture API 8/8, desktop·tablet·mobile Playwright 3/3 |
| `make security-scan` | PASS | 88개 source, 위반·비밀 유사 파일·실제 주문 경로 0 |
| `make network-smoke` | PASS | Binance 적격 527, 공개 WebSocket 2 events, p95 7,197.163ms, credentials false |
| macOS root launcher smoke | PASS | `127.0.0.1:8890` 실제 부팅과 HTML 200. READY, 1,000 USDT, 손익·비용·거래 0, auth·real order false |
| macOS LaunchAgent | PASS | `kr.robom.flowscalper` running, `RunAtLoad`·`KeepAlive`, 고정 `127.0.0.1:8870`, PID 종료 후 자동 복구 |
| schema v6 archive replay | PASS | 최신 Run SQLite raw event 0, 외장 Parquet 77,274 events, row·batch checksum·경로 검증, `PRAGMA quick_check=ok` |
| Windows setup/run 실제 실행 | NOT_RUN | macOS 환경이며 Windows 실기기 실행을 주장하지 않는다. |
| 6시간 / 24시간 soak | NOT_RUN | 실행 스크립트 제공. 실제 경과시간 검증은 하지 않았다. |

## 9. 업그레이드 수용 행렬

| 요청 항목 | 상태 | 증거 |
|---|---|---|
| Fresh LIVE PAPER 1,000 USDT와 모든 성과 0 시작 | PASS | READY 상태 API·통합테스트·UI |
| OFFLINE FIXTURE의 LIVE 기본화면·성과 분리 | PASS | 별도 DEMO Run·필터·DB 테스트 |
| 장시간 WebSocket supervisor | PASS | lifecycle 결합, 30분 soak와 recovery 테스트 |
| 수십 종목 지속감시와 8~12 deep 분석 | PASS | 실제 wide 50·deep 10 |
| 실제 candle chart와 시간구간 | PASS | Lightweight Charts 5.2.1, 9개 interval |
| A/B LIVE PAPER 종단 연결과 확장 Registry | PASS | 실제 public 평가, Registry API·원장·테스트 |
| ACTIVE·SHADOW·OFF, LONG·SHORT | PASS | 전략 관리 UI·API·복구 테스트 |
| 전략별 독립 shadow 계좌와 성과 | PASS | A/B/C/D × BASE/STRESS 8계좌 |
| 신규 전략 C/D PAPER 전용 | PASS | Registry·결정론 테스트·real-order 0 |
| 진입 전 계획 확정 | PASS | 불변 CandidatePlan과 원장 |
| 실제 bid·ask 보수적 PAPER 체결 | PASS | latency·IOC·partial·보호 통합테스트 |
| 현재 포지션·순손익 실시간 표시 | PASS | runtime snapshot·WebSocket·chart |
| 거래내역 실제 원장 연결 | PASS | SQLite main·shadow ledger API·UI |
| backend ReplayEngine과 저장 event 연결 | PASS | 공개 21,620건 두 replay checksum 일치 |
| 기대값·PF·비용·drawdown·표본상태 | PASS | strategy analytics API·UI·테스트 |
| 비전문가용 한국어 UI·고급진단 | PASS | 7화면·3 viewport E2E |
| 재연결·복구·장시간·보안·회귀 | PASS | recovery tests, 최종 30분 soak, 전체 검증 |
| 실제 주문·private API 0 | PASS | runtime invariant·security scan·UI |
| 최종 실행증거와 스크린샷 | PASS | 이 문서와 `evidence/screenshots/` |

## 10. 릴리스 아티팩트

| 항목 | 실제 값 |
|---|---|
| One Touch ZIP | `/Volumes/One Touch/ROBOM_AUTOTRADING/FlowScalper_v0.2_20260822/02_RELEASES/ROBOM_FlowScalper_0.2.0-paper-wave10-20260823.zip` |
| APFS ZIP | `/Volumes/ROBOM_FLOWSCALPER/02_RELEASES/ROBOM_FlowScalper_0.2.0-paper-wave10-20260823.zip` |
| checksum 파일 | 각 ZIP 옆의 동일 이름 `.zip.sha256` |
| ZIP SHA-256 | `1f433e47f4b3e405dcc483239206e13a3bbd9caa244a4b7b84a52ee70f7ccfe9` |
| 크기 / 파일 수 | 10,970,142 bytes / 243 ZIP entries |
| 내부 BUILD_COMMIT | `23a709ca2e40f39c16e20f28b960f67492bbb1f6` |
| `unzip -t` | PASS, 압축 데이터 오류 0 |
| 내부 `SHA256SUMS.txt` 전수검사 | PASS, 242개 entry 전부 일치 |
| APFS·One Touch 복사본 비교 | PASS, 두 복사본과 작업공간 생성본 SHA-256 일치 |
| 패키징 직전 소스 회귀 | PASS, backend 105/105·frontend 5/5·lint·typecheck·build·security |

릴리스에는 backend·frontend 소스와 테스트, 빌드된 frontend, macOS LaunchAgent 설치·해제 스크립트, macOS·Windows 실행기, 설정, fixture, migration, 문서, 스크린샷, notices, third-party licenses와 내부 checksum이 포함된다. `.venv`, `node_modules`, 캐시, SQLite/Parquet 원시 실행데이터와 비밀 유사 파일은 제외한다. 기존 Wave 09 표준 이름 ZIP은 덮어쓰지 않고 보존했다.

## 11. 알려진 제한

- 이 프로그램은 PAPER 연구 도구이며 수익성이나 미래 성과를 보장하지 않는다.
- 자연 적격신호가 없었던 공개시장 기록에서는 거래 0을 그대로 보존했다.
- 6시간·24시간 soak와 Windows 실기기 실행은 `NOT_RUN`이다.
- 거래소의 지역 제한·유지보수·protocol 변경은 로컬 코드로 없앨 수 없다. 연결이 검증되지 않으면 LIVE 대신 fail-closed 상태를 표시한다.
- 외장 APFS 작업 이미지는 현재 약 32GiB 상한이며 약 29GiB가 비어 있다. 장기수집으로 한계에 가까워지면 One Touch의 별도 데이터 볼륨으로 확장해야 한다.

## 12. Wave 09 LIVE 지연·차트·시각 핫픽스

2026-08-22 사용자 화면에서 확인된 13~23초 지연, scanner 높이로 늘어난 chart, UTC/KST 불일치를 수정했다. 세부 결정은 `docs/adr/ADR-007-live-backpressure-chart-and-kst.md`에 기록했다.

### 원인과 수정

- 50개 real-time `bookTicker`과 10개 `depth@100ms`가 WebSocket 내부 queue에 백로그를 만들었다. wide 1초 `24hrTicker`, deep 250ms depth, real-time trade를 세 경로로 분리했다.
- sequence가 없는 wide event ID가 충돌해 SQLite batch가 매 이벤트마다 재시도되었다. 수신 monotonic nanosecond를 ID에 포함하고, 저장은 독립 worker thread에서 bounded batch로 수행한다.
- UI client마다 dashboard를 새로 계산·JSON 직렬화했다. 0.5초마다 snapshot과 JSON을 한 번만 만들어 모든 localhost client에 broadcast한다.
- SQLite WAL checkpoint 중 대시보드가 같은 ledger lock을 기다리며 멈출 수 있었다. LIVE Run 시작 시 이전 거래를 cache하고 화면 snapshot은 cache와 현재 메모리 거래만 사용해 거래 원장 API와 UI 갱신 경로를 분리했다.
- 차트를 snapshot마다 remove/create하던 효과를 없애고 series·marker·plan line만 갱신한다. CSS grid는 `align-items: start`와 영역 배치를 쓰고 chart는 viewport 기반 360~560px로 제한한다.
- chart axis, event log, replay, system 시각을 `Asia/Seoul` KST로 고정했다. system 화면은 server snapshot과 browser 수신 시각 차이도 표시한다.
- quote가 없는 trade event에 bid/ask 100 기본값을 넣어 차트 선을 튀게 만들 수 있던 오류를 제거했다.

### 실제 LIVE 결과

`run-ef96cc96a072`를 1,000 USDT, 손익·비용·거래 0에서 새로 시작한 후 176.709초 실제 Binance 공개시장을 측정했다.

| 항목 | 실제 값 |
|---|---:|
| wide / deep | 50 / 10 |
| 처리 events / candles / chart points | 35,966 / 156 / 30 |
| 실행 경로 p50 / p95 | 0ms / 0ms |
| 넓은 감시 age p95 | 1,355ms |
| queue / reconnect / gap / resync / drop | 0 / 0 / 0 / 0 / 0 |
| persistence fault / buffer drop | 0 / 0 |
| CPU / memory | 41.432% / 231.344MB |
| paused / auth headers / real orders | false / false / false |

전체 표본에서 저장 batch 전후 실행 경로 p95는 0~1,224ms였고 1,500ms 진입잠금 기준 이하였다. 원본 값은 `evidence/WAVE09_LIVE_UI_LATENCY_FIX.json`에 보존했다.

이후 SQLite checkpoint와 장시간 화면 응답을 다시 검증하기 위해 최신 코드로 `run-b74c8bad6fca`를 625.957초 연속 실행했다.

| 항목 | 실제 값 |
|---|---:|
| 처리 events / candles / chart points | 129,849 / 604 / 30 |
| 실행 경로 p50 / p95 | 20ms / 71ms |
| 화면 API 주기 표본 | 38/38 HTTP 200 |
| 주기 표본 최대 응답 | 120.584ms |
| 별도 100회 연속 API 평균 / 최대 / 실패 | 8.883ms / 20.293ms / 0 |
| server와 local 수신 시각 차이 / 표시 시간대 | 5ms / Asia/Seoul |
| queue / reconnect / gap / resync / drop | 0 / 0 / 0 / 0 / 0 |
| persistence fault / buffer drop | 0 / 0 |
| CPU / memory | 52.921% / 250.984MB |
| paused / auth headers / real orders | false / false / false |

기존 멈춤이 발생했던 약 10분 구간을 넘겼고, 저장 buffer는 증가 후 반복적으로 정상 배출되었다. LIVE 대시보드가 SQLite writer lock을 읽지 않는 회귀테스트도 추가했다.

### 회귀 검증

| 검증 | 결과 |
|---|---|
| backend pytest | PASS, 96 tests |
| frontend Vitest | PASS, 3 tests |
| Ruff / ESLint | PASS, 오류 0 |
| TypeScript / Vite build | PASS, 39 modules, JS 424.05kB, gzip 134.22kB |
| localhost WebSocket 다중 client | PASS, 두 client에 같은 PAPER snapshot 전달 |
| KST 결정론 변환 | PASS, Unix epoch 0→09:00:00 KST |
| 실제 주문·private API·인증 | 0, false, false 유지 |

장시간 UI 응답 fix의 구현 커밋은 `3d5792bfc96d3116a1bfd422a7b9ab380c86755f`다. 최종 릴리스는 증거 커밋 `6a3eb0e9d781dca54ba1aca766264c3998ba34ee`를 내부 BUILD_COMMIT으로 포함하며 238개 entry, 10,944,817 bytes다. ZIP SHA-256은 `4215e5570f6f283c2f7c9de742db1dad5b49334af3e629b06c2cc0a6f6a98acc`이고 `unzip -t`와 내부 checksum 237개가 모두 PASS다. 작업공간과 One Touch 복사본의 SHA-256도 일치한다.

### 화면 재검수 제한

수정 후 localhost 애플리케이션은 `http://127.0.0.1:8870/`에서 실행 중이다. Codex in-app browser는 admin-enforced security policy 확인이 일시적으로 불가해 DOM snapshot과 수정 후 screenshot 캡처를 허용하지 않았다. 보안 제어를 우회하지 않았으며, 기존 Wave 06 screenshot을 수정 후 화면 증거로 잘못 재사용하지 않는다. 따라서 Wave 09 수정 후 screenshot 항목은 `BLOCKED`이고 API·소스·빌드·런타임 검증만 `PASS`다.

## 13. Wave 10 항상 실행·초보자 화면·저장 병목 최종화

2026-08-23 사용자의 사이트 미응답, 복잡한 scanner, 차트 비율 흔들림, 이동평균 부재, 어려운 PAPER 용어, 높은 지연과 내장 용량 부족 요청을 함께 수정했다.

### UI와 자동 실행

- 홈을 프로그램 상태, 진행 중 모의거래, 완료 거래, 현재 순손익, 정밀 관찰 종목 중심으로 단순화했다. `페이퍼 진입` 대신 `자동 관찰 시작`, `상승 관찰`·`하락 관찰`·`기다리기`를 사용한다.
- scanner는 알파벳순 고정 목록과 내부 스크롤을 사용하고 종목·관찰 방향·진입 준비만 기본 노출한다. 전략, 점수, 비용, 손익비와 거절 이유는 `상세`를 열 때만 보인다.
- chart와 scanner의 grid 높이를 분리하고, chart는 viewport 범위 안의 고정 높이와 animation-frame으로 병합된 ResizeObserver를 사용한다. 목록 행 수나 상세 열림이 chart 비율을 바꾸지 않는다.
- Lightweight Charts 인스턴스를 snapshot마다 다시 만들지 않고 실제 candle·거래량을 갱신한다. 5선·10선은 기본, 20선·60선·호가선은 선택이며 각 숫자는 현재 시간구간의 candle 개수다.
- 화면, chart axis, 이벤트와 시스템 시각은 `Asia/Seoul`로 통일했다.
- 설치된 LaunchAgent `kr.robom.flowscalper`는 로그인 후 `127.0.0.1:8870`을 자동 시작하고 비정상 종료 후 다시 실행한다. Mac 전원이 꺼진 동안 localhost가 열릴 수 있다는 주장은 하지 않으며, 외장 APFS 소스가 마운트되어야 한다.

### 병목 조사와 채택한 저장 구조

- 기존 약 1.3GB SQLite의 Run별 `COUNT(*)`, event loop 안의 replay·analytics 조회, 매 이벤트 지연 표본 정렬, 반복 다중-window feature scan, 외장 sparsebundle의 WAL checkpoint를 각각 분리해 재현했다.
- replay·analytics는 worker thread, 지연 p95는 256표본 cache, feature는 단일 순회와 500ms 전략 평가, PAPER 포지션 관리는 모든 250ms deep 호가 경로로 바꿨다.
- transient critical lag가 회복돼도 `paused`가 남던 상태를 수동 일시정지와 안전 자동잠금으로 분리했다. 안전 지표가 회복되면 자동잠금만 풀리고 사용자가 누른 일시정지는 유지된다.
- 활성 SQLite에는 PAPER 거래 상태·설정·통계·archive manifest만 둔다. 공개시장 원본은 리플레이에 필요한 상위 10단계 호가를 보존해 1,000건 단위 ZSTD Parquet으로 외장 `data/market-parquet-v6`에 기록한다.
- 5,000건 batch는 p95 5,978ms와 자동 일시정지를 만들어 실패로 폐기했다. 1,000건 batch는 아래 최종 실행에서 통과했다.

### 최종 실제 LIVE 공개시장 증거

최종 Run ID는 `run-9b9d508c689d`다. Fresh 시작은 1,000.00 USDT, 손익·수수료·슬리피지·거래 0, wide 50, deep 10, auth false, real orders false였다.

| 항목 | 실제 값 |
|---|---:|
| 연속 측정 구간 | 4분 이상, 13개 20초 표본 |
| 측정 종료 events / p95 | 37,984 / 140ms |
| 측정 중 p95 관찰 범위 | 33~140ms |
| paused / drop / gap / reconnect / persistence fault | false / 0 / 0 / 0 / 0 |
| 주기 dashboard 최대 / replay 최대 | 122.291ms / 62.307ms |
| 내부 SQLite 증가 / 외장 archive 증가 | 1,232,896 bytes / 4,036KiB |
| 측정 후 지속 저장 | 77,274 events, 147 Parquet, 7,987,803 bytes |
| 최신 Run의 SQLite raw `market_events` | 0 |
| `market_event_stats` / archive manifest 합계 | 77,274 / 77,274 |
| SQLite / archive replay | `quick_check=ok` / 실제 timeline 20건 checksum 검증 반환 |
| 현재 자산 / 손익 / 수수료 / 거래 | 1,000.00 / 0 / 0 / 0 |
| 인증 / 실제 주문 | false / false |

장시간 검증 후 병렬 회귀 테스트 부하 중 순간 p95 1,288ms도 1,500ms 잠금 기준 아래였고 `paused=false`, queue·drop·gap·reconnect·fault 0을 유지했다.

시각은 dashboard 응답 왕복시간을 보정해 로컬 KST +22.7ms, Binance +43.6ms, Bybit +40.4ms 차이였다. 프로세스 실행을 포함한 별도 명령 시작시간을 서버 시각 차이로 잘못 계산하지 않았다.

### 최종 회귀와 화면 증거 경계

| 검증 | 결과 |
|---|---|
| backend pytest | PASS, 105 tests |
| frontend Vitest | PASS, 3 files·5 tests |
| Ruff / mypy / ESLint / TypeScript | PASS |
| Vite production build | PASS, 39 modules, JS 431.18kB, gzip 135.95kB |
| security scan | PASS, 88 source, 위반·비밀 유사 파일·실제 주문 경로 0 |
| service shell syntax / installed plist | PASS / PASS |
| `git diff --check` | PASS |
| 수정 후 in-app browser DOM·screenshot | BLOCKED, admin-enforced policy 확인 불가 |

localhost HTTP, WebSocket 데이터, 실제 LIVE API, 컴포넌트 테스트, production build와 원장 replay는 검증했다. 다만 수정 후 화면을 Codex in-app browser로 다시 캡처하는 작업은 보안 정책이 허용하지 않아 `BLOCKED`다. 다른 브라우저 자동화로 우회하지 않았고 과거 screenshot을 최신 화면 증거라고 주장하지 않는다.

## 14. GitHub AI 인계와 반복 업그레이드 정리

2026-08-23 다른 AI가 GitHub만 읽어도 제품과 사용자 요구를 이해하고, 반복 업그레이드에서 old·backup·복사본이 현재 source와 섞이지 않게 하는 정리를 적용했다.

### 현재 source와 과거 보존 경계

- `VERSION`을 제품 버전의 단일 원본으로 만들고 README·frontend·Python base version 일치를 자동 검사한다.
- `CHANGELOG.md`에는 0.1과 0.2의 사용자-visible 변화만 짧게 남긴다.
- `main`은 현재 구현 한 벌만 유지하고 과거 source는 Git history·tag, 배포 ZIP·checksum·최종 증거는 GitHub Release로 보존한다.
- 기능·UI 교체 때 이전 component·route·state·문구·CSS·test를 같은 변경에서 제거하는 규칙을 `docs/18_VERSIONING_AND_UPGRADE_POLICY_KO.md`와 ADR-009에 기록했다.
- CI와 `make repo-hygiene`는 old·legacy·backup·copy 이름, 운영 DB·Parquet·ZIP·log·cache·TypeScript build info가 추적되면 실패한다.
- 현재 문서로 대체된 0.1 실행 프롬프트 381줄과 `FINAL_EVIDENCE.md` 386줄, 자동 생성 TypeScript build info 2개를 현재 tree에서 제거했다. 삭제된 tracked 파일은 Git history와 기존 Wave 10 ZIP에서 복원할 수 있다.

### 로컬 구형 실행자료의 recoverable 정리

LaunchAgent PID 51549의 열린 파일을 확인해 현재 활성 원장이 `~/Library/Application Support/ROBOM FlowScalper/active-ledger/run-ledger.sqlite3`이고 프로젝트의 `data/active`, `data/active-v5`, `data/active-v6`, `data/e2e`, 기존 1.3GB `data/run-ledger.sqlite3`를 연 프로세스가 0임을 확인했다.

해당 원장과 과거 build·test·release 산출물은 삭제하지 않고 `/Volumes/ROBOM_FLOWSCALPER/04_MIGRATION_ARCHIVE/legacy-project-state-20260823`으로 이동했다. `MANIFEST.md`에 원래 경로·KiB·주요 SQLite SHA-256을 기록했고 이동 후 핵심 6개 checksum을 다시 계산해 모두 일치했다. 현재 외장 공개시장 archive인 `data/market-parquet-v6`는 이동하지 않았다. `02_RELEASES`에는 최종 ZIP·ZIP checksum·최종 증거·증거 checksum 네 파일만 남겼다.

### 검증과 GitHub source 증거

| 검증 | 결과 |
|---|---|
| backend pytest | PASS, 107 tests |
| frontend Vitest | PASS, 3 files·5 tests |
| Ruff / mypy / ESLint / TypeScript | PASS |
| Vite production build / PAPER build safety | PASS, 39 modules / PASS |
| repository hygiene | PASS, 위반 0 |
| security scan | PASS, 88 source, 위반·비밀 유사 파일·실제 주문 경로 0 |
| package release 대상 | PASS, AI 인계·GPT 요청·CHANGELOG·VERSION·버전정책·ADR-009 포함 |
| GitHub source push | PASS, private `robom-labs/flowscalper` main `7aef302ceb0251e774f031efbed4f0aa30379bb9` |
| GitHub 최상위 구조 | PASS, 프로그램 폴더 하나 + GitHub 필수 자동화 메타폴더 `.github` |

초기 push 뒤 프로그램 폴더 내부 `.github/workflows`는 GitHub Actions가 자동 발견하지 않는 구조임을 확인했다. workflow와 PR template은 저장소 최상위 `.github`로 옮기고 제품 자료는 계속 `ROBOM_FlowScalper_Codex_Package_20260822` 한 폴더에만 유지했다. 최상위 `.github`에는 CI·PR checklist 외 제품 파일이 없다.

### 현재 장시간 runtime 재관찰

GitHub 정리 뒤 실행 중인 `run-9b9d508c689d`를 다시 읽었을 때 mode는 `LIVE_SHADOW_PAPER`, 공개시장 상태는 LIVE, 실행은 PAPER, 실제 주문·인증은 false였다. 그러나 처리 지연 p95는 54,760ms였고 `CRITICAL_MARKET_LAG_ENTRY_LOCK`, `PAPER_ENTRIES_PAUSED`가 적용됐다. 이는 안전잠금이 작동했다는 PASS이지 장시간 지연 문제가 해결됐다는 PASS가 아니다. Wave 10의 4분 p95 140ms와 이 현재 장시간 재발을 구분하며 다음 runtime upgrade의 P0로 남긴다.

## 15. Strategy League 1차 백엔드 증거

2026-08-23 기준 전략 A-F, 전략별 BASE/STRESS 12계좌, 다중 포지션·위험·복구 v2를 기존 PAPER 런타임에 통합했다. 시작 HEAD는 `eb7455b11e16a3a2f2e752c4932bb0b1cbcc14a9`였다.

| 검증 | 상태 | 실제 결과 |
|---|---|---|
| 직접 관련 pytest | PASS | 67 passed |
| `uv run ruff check backend` | PASS | 오류 0 |
| `uv run mypy` | PASS | 70 source files, 오류 0 |
| `uv run pytest backend/tests -q` | PASS | 144 passed |
| `make security-scan` | PASS | 90 source, 위반·비밀 유사 파일·실제 주문 경로 0 |
| `make repo-hygiene` | PASS | 위반 0 |
| E/F 500ms·reset·대칭·거부 | PASS | 전용 신호 테스트 포함 |
| 12계좌·3종목·partial·v1/v2 복구 | PASS | 전용 포트폴리오 테스트 포함 |
| UI·browser·network·30분·6시간·24시간 soak | NOT_RUN | 1차 백엔드 범위 제외 |
| Release ZIP | NOT_RUN | 1차 범위에서 금지 |

이 섹션의 PASS는 위 명령을 이번 작업에서 실제로 실행한 결과만 뜻한다. 기존 UI·LIVE·soak 수치를 이번 Strategy League 변경의 재검증 결과로 쓰지 않는다. 실제 주문·private API·API Key·secret·wallet 기능은 추가하지 않았다.

## 16. 2차 UI·버튼·차트 업그레이드 증거

2026-08-23 기준, 기존 `0.2.0-paper` Strategy League 백엔드를 그대로 사용해 비동기 제어·초보자 UI·고정 scanner·전문 chart를 연결했다.

| 식별·범위 | 상태 | 실제 결과 |
|---|---|---|
| 시작 HEAD / tree | PASS | `9b3a5236ecea0c8e03f28f236cb11e3a8f25d7c3` / `efcd7a665aa01ada16acd1b8733ff52598fdb98d` |
| 최종 구현 commit / tree | PASS | `a3339a17fe6716560314ab0fa7c2c7e4875f82cb` / `0aa7033b0e34e319e74c9a97ee7424b84f76aa6d` |
| 변경 / 삭제 파일 | PASS | 66개 변경·신규, 삭제 0개. 백엔드·프런트·테스트·CI·문서·신규 screenshot을 포함한다. |
| Control API | PASS | start-live·start-demo·new-run `202`, 동일 action operation ID 재사용, 다른 action `409`, 순서형 stage·cancel·retry·blocked·20개 history 검증 |
| cancellation 안전 | PASS | start 대기 취소 후 producer·consumer 유실 0, runtime paused, 거짓 LIVE 0 |
| Dashboard·WebSocket | PASS | `control_operation`, 6전략, `league_accounts` 12행, 확장 `league_positions`, 분리 risk 계약 포함 |
| backend pytest | PASS | 150 passed |
| frontend Vitest | PASS | 9 files, 24 passed |
| Ruff / mypy | PASS | 오류 0 / 72 source files 오류 0 |
| ESLint / TypeScript | PASS | 오류 0 / 오류 0 |
| Vite production build | PASS | 47 modules, JS 461.58kB, gzip 144.16kB |
| Playwright | PASS | desktop 1408×900, tablet 820×1180, mobile 390×844, 3 passed |
| Playwright 화면 안전 | PASS | console error 0, page error 0, failed request 0, root overflow 0, 중요 control 48px 미만 0 |
| scanner·chart | PASS | 10종목, 순위 고정/자동정렬, drawer 전후 chart·scanner 크기 동일, 일반 갱신 `update`, 선택 변경 `setData` |
| chart 기능 | PASS | MA5/10/20/60, EMA20, VWAP, Bollinger, RSI, MACD, bid/ask/microprice, KST tooltip, 현재로 돌아가기, 전체화면 복귀 |
| 실제 8870 browser | PASS | 홈·operation·6카드·BASE/STRESS drawer·진행 거래·10 scanner·지표·전체화면 조작, browser error log 0 |
| 실제 반응형 실측 | PASS | desktop/tablet/mobile root overflow 0, 48px 미만 0, mobile chart 346px·scanner 374px |
| PAPER 안전 | PASS | build safety·security scan·repository hygiene PASS, 실제 주문·private API·API Key·secret·wallet 0 |
| GitHub main / Core / Browser Actions | PASS | 구현 commit의 로컬·원격 SHA·tree 일치. [Actions 32632658958](https://github.com/robom-labs/flowscalper/actions/runs/32632658958) validate 47초·browser 1분21초 PASS, browser evidence artifact 업로드 PASS. |
| public network smoke | NOT_RUN | 이번 2차는 결정적 fixture와 현재 로컬 UI 검증 범위다. |
| 30분 / 6시간 / 24시간 soak | NOT_RUN | 이번 작업에서 실행하지 않았다. |
| Release ZIP | NOT_RUN | 이번 2차 작업 범위에서 생성하지 않았다. |
| FAIL | PASS | 해결하지 못한 필수 검증 실패 0건. |
| BLOCKED | PASS | 현재 blocker 0건. |

버튼 무반응의 근본 원인은 HTTP POST가 장시간 supervisor 준비·재연결 완료를 기다리고, 프런트엔드에 request 상태·timeout·cancel·중복 방지·서버 오류 표시가 없었던 것이다. 백엔드는 operation 작업으로 분리하고 UI는 WebSocket stage를 원본으로 즉시 반응하게 교체했다.

실제 browser 증거는 `evidence/screenshots/phase02-actual-browser-*.png`, 결정적 Playwright 증거는 `evidence/screenshots/phase02-{home,league,positions,terminal}-*.png`이다. 이번 검증에서 차트 드래그 후 `현재로 돌아가기`가 `rightOffset` 기준과 어긋나 나타나지 않는 결함을 발견했고, 라이브러리 `scrollPosition()`의 실제 부호 계약으로 수정한 후 실제 캔버스 드래그·복귀를 통과했다.

## 17. 3차 최종통합 시장화면·포지션 집중·리플레이 증거

2026-08-23 기준 기존 `0.2.0-paper` 한 벌을 compact 시장 중심 프로그램으로 업그레이드했다. 시작 HEAD는 `21baa395f75fe8c0b3408dba89c7a2a5a9619bf7`, tree는 `527f45613aed9ae8b3917765fda92a421b8d7bfb`였다. 참고 이미지는 기능·정보구조·밀도 비교에 사용했고 제3자 명칭과 브랜딩은 복제하지 않았다.

### 구현과 화면 검증

| 검증 | 상태 | 이번 실행의 실제 결과 |
|---|---|---|
| compact 시장 기본화면 | PASS | 상단 5개 메뉴, Binance/Upbit rail, 기본 3분봉 200개, 거래량·MA10·MA20·RSI·MACD, 고정 chart 공간 구현 |
| 데스크톱 실측 | PASS | 1408×900 root overflow 0, 시장 chart panel 1116×780·canvas 1020×666, 포지션 chart panel 984×796·canvas 894×682 |
| 반응형 | PASS | Playwright desktop 1408×900·tablet 820×1180·mobile 390×844 3건, 계획·손익 sheet 전후 chart box 동일 |
| 전체 공개 catalog | PASS | 실제 network에서 Binance catalog 696·전략 적격 527, Upbit KRW 285를 읽고 Upbit는 `OBSERVATION_ONLY`로 고정 |
| 실제 공개 candle | PASS | BTCUSDT·Binance catalog tail GSUSDT·KRW-BTC 각각 3분봉 200개 |
| deep 20 안전회전 | PASS | 포지션 pin·30분 최소 체류·회전당 20% 상한, 30분 실행에서 계획 회전 1회 |
| 전략×종목 성과 | PASS | 30건 미만은 ranking 제외, 실제 현재 원장은 적격 조합 0건으로 빈 상태를 그대로 표시 |
| 포지션 집중 계약 | PASS | BASE 우선, 실제 fill·entry·초기/현재 stop·TP1/TP2·수량·비용·순손익·남은 위험·데이터 상태를 같은 계약으로 표시 |
| 거래 단위 replay UI | PASS | 저장 이벤트만 사용, 20분 pre-roll, 미래 marker 숨김, 0.5~80×·이벤트/핵심 이동·결정적 checksum·CLOSED_REVIEW 구현 |
| 데모 replay 구분 | PASS | DEMO_FIXTURE는 `샘플 UI 검수`로 표시하고 실제 공개시장 replay 일치로 판정하지 않음 |
| 참고 이미지 비교 | PASS | `evidence/screenshots/phase03-reference-vs-position-focus.png`에서 같은 viewport로 구조·밀도·표시 누락을 비교 후 수정 |
| 실제 Chrome 화면 | PASS | Binance 전체 목록, Upbit 관찰 목록, 전략×종목 빈 상태, fixture 포지션 집중·80× 조작을 실제 8870 화면에서 확인 |

### 자동검증과 공개시장 실행

| 검증 | 상태 | 이번 실행의 실제 결과 |
|---|---|---|
| repository hygiene / Ruff / mypy | PASS | 위반 0 / 오류 0 / 75 source files 오류 0 |
| backend pytest | PASS | 157 passed |
| frontend Vitest | PASS | 10 files, 27 passed |
| ESLint / TypeScript | PASS | 오류 0 / 오류 0 |
| production build | PASS | 46 modules, JS 477.18kB·gzip 147.99kB, PAPER build safety PASS |
| Playwright | PASS | desktop·tablet·mobile 3 passed, console error·page error·failed request·root overflow 0 |
| security scan | PASS | 106 source, 위반·비밀 유사 파일·실제 주문 경로 0 |
| GitHub main 구현 동기화 | PASS | 구현 commit `bfd19a485519b5106392c1337e25542eb4f7ed31`, tree `f640ff5d1658db0e1d94d38b3eddc4995a89691e`, 로컬·원격 SHA 일치 |
| GitHub Actions | PASS | [run 32643841024](https://github.com/robom-labs/flowscalper/actions/runs/32643841024) validate 53초·browser 1분5초 PASS, browser evidence artifact 업로드 PASS |
| public network smoke | PASS | Binance/Upbit REST와 Binance public WebSocket 16 events, lag p50 13.349ms·p95 14.621ms, 인증 header·credential·실제 주문 false |
| 30분 soak | PASS | `soak-2cb274092b81`, 1800.024초·811,154 events·deep 20·회전 1, reconnect 1·gap/resync/drop 0, queue max 2/4096 |
| 30분 지연·메모리 | PASS | 종료 p50 19ms·p95 59ms, max p95 312ms, critical lag/fail-open 0, event memory max 9,978, memory +221.344MB<256MB |
| 자연 공개시장 진입 | NOT_OBSERVED | 전략 817,464회 평가·적격 337건이 있었으나 main PAPER 거래 0건. 기준을 낮추지 않았고 실제 fill 자동집중·실거래 replay 일치는 주장하지 않음 |
| 6시간 / 24시간 soak | NOT_RUN | 이번 실행에서 수행하지 않음 |
| Release ZIP | NOT_RUN | 이번 3차 범위에서 생성하지 않음 |
| 실제 주문·private API·API Key·secret·wallet | PASS | 경로와 사용 모두 0, 인증 불필요, PAPER 전용 유지 |

원본 기계판독 증거는 `evidence/PHASE03_PUBLIC_MARKET_SMOKE.json`, `evidence/PHASE03_SOAK_30M.json`이다. 결정적 화면은 `evidence/screenshots/phase03-{market,position-focus,replay-position-focus,replay-position-focus-80x}-{desktop,tablet,mobile}.png` 중 해당 viewport 파일에 있고, 실제 Chrome 관찰 화면은 `evidence/screenshots/phase03-actual-*.png`에 있다. GitHub Actions의 코드 검증과 browser 화면 증거 생성도 같은 구현 commit에서 모두 PASS했다.

## 18. 3차 LIVE 지연·실제 클릭 시뮬레이션 최종 보강

2026-08-24 실제 8870 서비스에서 장시간 Run 뒤 처리지연 p95가 최대 34,723ms까지 상승하고 ping timeout·재연결이 발생한 상태를 재현했다. entry lock은 임계 지연에서 fail-closed로 동작했고 실제 주문·인증·거래는 0이었다. 함수 단위 profiling과 실제 공개시장 통합 측정으로 저장 partition, 고빈도 trade fan-out, 반복 전략 통계와 호가 정렬을 병목으로 좁힌 뒤 ADR-011로 수정했다.

### 수정과 실제 결과

| 검증 | 상태 | 이번 실행의 실제 결과 |
|---|---|---|
| Run별 archive | PASS | 새 Parquet 경로에 Run dimension을 추가하고 서로 다른 두 Run의 저장·replay exactness를 테스트했다. |
| 체결 병합 | PASS | 같은 종목·방향·250ms bucket의 수량·명목가치·VWAP를 보존하고 mixed-side 시간순서를 테스트했다. 전략·체결 기준은 변경하지 않았다. |
| 계산·저장 병목 | PASS | snapshot history 통계를 12개 전략방향에 공유하고 상위 호가 key 정렬·2,000건 worker batch·종료 잔여 flush를 적용했다. |
| 90초 profiling | PASS | 실제 공개시장 9,375 events·41,856 strategy evaluations·177 dashboard snapshots를 측정해 evaluator·feature·orderbook hotspot을 확인했다. |
| 180초 실제 통합 | PASS | 18표본, 38,609 events, source trades 48,687→output 17,443, p50 최대 30ms·p95 최대 458ms, queue 최대 2, reconnect·gap·drop·persistence fault 0, flush 최대 986ms, 실제 주문 0. |
| fresh LIVE 재시작 | PASS | `run-bc4d6ab899e8`은 30초 뒤 p95 35ms·7,009 events였고, 최종 화면 캡처 Run `run-f14214b3b1dd`는 p95 14ms·4,235 events였다. 둘 다 Binance public LIVE, wide 50·deep 20, queue/drop/gap/reconnect/fault 0, 자산 1,000·손익/수수료/거래 0, pause false, auth/실제주문 false였다. |
| DEMO 상태 격리 | PASS | LIVE p95·50/20 뒤 DEMO가 p95 `null`, wide/deep 10/10으로 초기화되고 390px에서 `샘플 PAPER · LIVE 아님 · 실제 주문 0`을 표시했다. |
| 실제 browser 조작 | PASS | 50개 결과 실패 0. 5개 주 메뉴, 전략 ACTIVE/SHADOW/OFF·LONG/SHORT, 기록 filter, backend replay, 0.5~80x, slider·step·play, 분석 filter, pause/resume, 종목·Upbit 관찰, 7개 시간구간, 12개 지표, fullscreen, drawer, 계획·손익 sheet, focus 신호·진입·종료와 모바일 LIVE PAPER 진실표시를 실제로 확인했다. |
| 완료 거래 focus | PASS | 실제 DEMO 원장 24 market frames에 entry/exit ledger frames를 더한 26-frame session에서 PRE_ENTRY·OPEN·CLOSED, 계획·손익 sheet, 진입·종료 이동과 재생·정지를 확인했다. |
| 반응형 화면 | PASS | actual in-app browser 1408×900·820×1180·390×844에서 제목 겹침을 제거하고 focus chart와 제어를 확인했다. |
| 전체 자동검증 | PASS | repository hygiene, Ruff, mypy 75 files, backend 162, frontend 29, ESLint, TypeScript, production build, PAPER build safety, security 106 source, Playwright desktop/tablet/mobile 3 PASS. |
| public network smoke | PASS | Binance catalog 696·eligible 527, Upbit KRW 285, 세 candle 200개, WebSocket 16 events, p95 4.513ms, credential/auth/실제주문 false. |
| 자연 공개시장 진입 | NOT_OBSERVED | 기준을 낮추지 않았고 이번 fresh LIVE와 180초 통합 실행에서 자연 main PAPER fill은 0이었다. 실제 fill 자동집중은 기존 30분 실행과 결정적 경로 검증 범위 밖으로 과장하지 않는다. |
| 6시간 / 24시간 soak | NOT_RUN | 이번 보강 실행에서 수행하지 않았다. 180초를 장시간 합격으로 표현하지 않는다. |
| Release ZIP | NOT_RUN | 이번 보강 범위는 GitHub main 동기화이며 새 Release ZIP은 만들지 않았다. |

기계판독 증거는 `evidence/PHASE03_INTEGRATED_LIVE_POSTFIX_180S.json`, `evidence/PHASE03_ACTUAL_UI_SIMULATION.json`, `evidence/PHASE03_PUBLIC_MARKET_SMOKE.json`이다. 실제 화면은 `evidence/PHASE03_ACTUAL_LIVE_MOBILE_390x844.png`, `evidence/PHASE03_UI_DEMO_MOBILE_390x844.png`, `evidence/PHASE03_ACTUAL_FOCUS_REPLAY_{DESKTOP_1408x900,TABLET_820x1180,MOBILE_390x844}.png`에 있다. PASS는 이번 실행 결과만 뜻하고 6시간·24시간과 자연 LIVE fill은 각각 `NOT_RUN`, `NOT_OBSERVED`다.

구현·증거 commit `a11cb0b1fcbabc3a65e71f31018175e035c7d2ec`은 GitHub `main`과 일치한다. GitHub Actions `32650393541`에서 validate job과 실제 Chromium desktop·tablet·mobile browser job, browser evidence upload가 모두 PASS했다.

## 19. 시작 버튼 상태·자동복귀·장시간 호가 병목 보강

2026-08-24 실제 8870 서비스에서 `자동 관찰 시작`이 무반응처럼 보이고 프로그램이 혼자 멈춘 것 같다는 사용자 보고를 재현했다. 시작 요청 자체는 서버에 전달됐지만, 장시간 Run `run-00765f8de10a`는 공개시장 supervisor가 계속 수신하는 동안 처리지연 p95 7,352ms, event 3,152,446, CPU 65.685%였고 신규 PAPER 진입은 안전잠금 상태였다. 기존 UI가 사용자 일시정지와 자동 안전 대기를 같은 `paused` 문구로 표시한 것이 상태 오인의 직접 원인이었다.

### 구현과 실패 수정

- dashboard에 시장 관찰과 새 PAPER 진입을 분리한 `operation_status`를 추가했다. READY, 연결 중, RUNNING, 사용자 일시정지, 자동 안전 대기, 수동 확인이 필요한 안전차단과 재연결을 서로 다른 한국어 상태로 표시한다.
- 임계 지연처럼 자동 회복 가능한 잠금에는 수동 재개 버튼을 숨기고 기존 안전조건이 모두 정상화되면 자동 복귀한다. 사용자가 직접 누른 일시정지만 한 번의 `새 진입 다시 시작` 클릭으로 해제한다.
- 첫 최적화 후보였던 `heapq` 상위 20단계 조회는 같은 10,000회 benchmark에서 기존 전체 정렬 0.277400초보다 느린 1.726382초로 `FAIL`해 폐기했다.
- 최종 구현은 전체 1,000단계 원장을 유지하면서 상위 20단계 가격을 캐시한다. 상위 가격 삭제 시에만 전체 순서를 다시 계산하고, 500회 결정적 추가·수정·삭제에서 전체 정렬과 정확히 같은 결과를 검증했다.
- 상태패널 추가 뒤 첫 Playwright desktop 차트 높이가 660px, 두 번째가 678px로 기존 680px 기준에 미달해 두 실행을 `FAIL`로 처리했다. 데스크톱 패널 높이만 52px로 압축하고 정보·모바일 큰 상태판은 유지한 뒤 최종 desktop·tablet·mobile 3개를 다시 통과시켰다.

### 이번 실행의 검증

| 검증 | 상태 | 실제 결과 |
|---|---|---|
| 시작 전 상태 | PASS | READY, 1,000 USDT, 손익·수수료·거래 0, PAPER, 실제 주문·인증 false와 큰 `자동 관찰 시작` 버튼을 실제 화면에서 확인했다. |
| 실제 시작 클릭 | PASS | 실제 in-app browser에서 한 번 클릭 후 즉시 `연결 중 · 요청을 받았습니다`, 약 10초 뒤 `작동 중`과 시장 관찰·새 PAPER 진입 작동·자동복구 켜짐을 확인했다. |
| 사용자 일시정지·재시작 | PASS | `새 진입 잠시 멈춤` 한 번으로 `사용자가 일시정지 · 시장 관찰 계속 작동`, `새 진입 다시 시작` 한 번으로 RUNNING 복귀를 확인했다. |
| 안전 대기 자동복귀 | PASS | 임계 지연 주입 뒤 `SAFETY_WAITING`, 수동 resume 버튼 없음, 2,000 fresh depth 뒤 RUNNING 자동복귀, 이후 사용자 일시정지 보존을 backend·frontend 테스트로 검증했다. |
| 상위 호가 정확성·성능 | PASS | 1,000단계 양쪽·top 20·10,000회에서 전체 정렬 0.275076초, 캐시 0.010393초, 결과 일치, 약 26.47배 조회 개선을 확인했다. |
| 실제 공개시장 연속 관찰 | PASS | `run-c42789c17473`을 process uptime 746.33초까지 관찰했다. 21표본 모두 RUNNING·paused false, p95 최대 1,144ms<1,500ms, queue 최대 2, drop·reconnect·gap·persistence fault 0, 최종 event 97,920이었다. |
| 최종 실행 상태 | PASS | 최종 구현 재시작 뒤 `run-7525441a7665`를 실제 버튼 한 번으로 시작했다. LIVE_SHADOW_PAPER·LIVE·RUNNING·paused false, event 3,811, 자산 1,000·손익·수수료·거래 0, 실제 주문·인증 false였다. |
| 전체 자동검증 | PASS | repository hygiene, Ruff, mypy 75 files, backend 164, frontend 31, ESLint, TypeScript, production build 47 modules, PAPER build safety, security 107 source, Playwright desktop·tablet·mobile 3 PASS. |
| 6시간 / 24시간 soak | NOT_RUN | 이번 실제 관찰은 약 12분이다. 멀티시간 안정성을 완료했다고 표현하지 않는다. |
| 자연 공개시장 PAPER fill | NOT_OBSERVED | 최종 Run의 거래는 0이며 자연신호 기준을 낮추지 않았다. |
| Release ZIP | NOT_RUN | 이번 범위는 현재 소스와 GitHub main 동기화이며 새 Release를 만들지 않았다. |

기계판독 증거는 `evidence/PHASE04_START_STATUS_AND_SOAK.json`이다. 실제 415×734 화면은 `evidence/screenshots/phase04-start-ready-mobile.png`, `evidence/screenshots/phase04-start-running-mobile.png`이고 SHA-256은 각각 `2b93f0b78859b72f7e1594299a906a9b3ae903048e44d06ae5eddf3daeb31f1f`, `28af1665e868e9a3de96c5718b65af2e87aa2fb908aefafa9e29e564767eff93`다.

구현·실행증거 commit은 `f3f2151f0ef2678c05ec40f5b6d83652d76ac26e`이고, 이를 정리한 main commit은 `d3107d29316d93b13da952017a2e2a21d0845f9b`이다. [GitHub Actions 32671925472](https://github.com/robom-labs/flowscalper/actions/runs/32671925472)에서 validate job의 repository hygiene·lint·typecheck·backend/frontend test·production build와 browser job의 실제 Chromium desktop·tablet·mobile E2E·browser evidence 업로드가 모두 PASS했다.
