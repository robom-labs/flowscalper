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

## 20. 전략 진입조건·자동 TP/SL·전체 시뮬레이션 증거

2026-08-24 기준 실제 코드, 결정론적 전략 시나리오, 저장 공개시장 replay와 실제 8870 브라우저를 함께 검증했다. 이 섹션은 진입·보호·청산 코드 경로의 정확성을 다루며 전략 수익성을 증명하지 않는다.

### 발견한 결함과 수정

- 수정 전 저장 공개시장 Run `run-f14214b3b1dd`의 15,045 events는 전략평가 41,628회·적격신호 19건이었지만 CandidatePlan과 거래가 모두 0이었다. 모든 적격신호가 최종 `LIVE_PLAN_INADEQUATE_NET_REWARD_RISK`에서 거부됐다.
- 1차 전략 계획은 최소 13bp 비용만 반영했지만 최종 planner는 실제 bid·ask, worst entry, 양방향 fee, exit slippage와 분할익절을 추가 계산했다. 기존 stop 거리는 두 게이트를 동시에 통과할 수 없었다.
- 최소 순손익비 1.20과 비용 기준을 낮추지 않고 REVERSION A/C는 최소 0.80%, TREND B/D/E/F는 최소 0.30% 구조거리로 맞췄다. 위험예산은 기존 main 0.1%, League 0.5% 그대로이며 stop 거리가 커지면 수량이 줄어든다.
- A~D runtime에 있던 고정 `pullback_seconds`·confirmation 값을 제거했다. A refill·재진입, C 구조 재진입, B/D 눌림·되돌림·재가속을 실제 event timestamp와 현재 이전 history prefix로 계산하고 조건 이탈 시 확인시각을 초기화한다.
- 리플레이에서 League 후보가 있는데 `candidate_plan_count=0`으로 보이던 집계는 main·BASE·STRESS에 배포된 고유 `candidate_id` 기준으로 수정했다.
- 서비스 재시작 뒤 거래목록에는 없는 이전 상세 drawer가 남는 UI 상태 누수를 제거하고 프런트 회귀 테스트를 추가했다.

### 전략별 진입조건과 자동 보호

| 전략 | 기본 상태 | 핵심 진입조건 | 최종 계획·청산 |
|---|---|---|---|
| A LSA 반전 | ACTIVE | sweep 0.5~2.5 noise, flow z≥1.8, 흡수효율≤30%, refill≥500ms, 재진입≥300ms, OFI·microprice 반전 | REVERSION, 최소 0.80%, TP1 70%·TP2 30%, 초기 SL |
| B CBR 돌파 | ACTIVE | 방향 trend, 압축≤20 percentile, 실제 1~10초·20~60% 눌림, 약한 counterflow, 유동성 회복, 실제 재가속≥300ms | TREND, 최소 0.30%, TP1 40%@1.5R·TP2 60%@3R, 초기 SL |
| C VWAP 소진 | SHADOW | RANGE, VWAP 이탈 z≥2.0, flow z≥1.5, 가격진행 정체, 반대호가 refill, OFI·microprice 반전, 구조복귀≥300ms | REVERSION, 최소 0.80%, TP1 70%·TP2 30%, 초기 SL |
| D OFI 눌림 | SHADOW | 방향 trend, 다중 OFI·공격체결·microprice 정렬, 효율 percentile≥50%, 실제 1~15초·10~60% 눌림과 재가속≥300ms | TREND, 최소 0.30%, TP1 40%@1.5R·TP2 60%@3R, 초기 SL |
| E 호가 쏠림 | SHADOW | spread≤8bp, top5≥0.18·top10≥0.12, 250ms·3s OFI, 1s 체결≥0.15, microprice≥0.25bp, 실제 지속≥500ms | TREND, 최소 0.30%, TP1 40%@1.5R·TP2 60%@3R, 초기 SL |
| F 체결흐름 | SHADOW | 방향 trend, signed-notional z≥1.8, 3s≥0.25·10s≥0.10 체결, OFI, 가격반응≥0.55, microprice, 실제 지속≥500ms | TREND, 최소 0.30%, TP1 40%@1.5R·TP2 60%@3R, 초기 SL |

모든 적격 CandidatePlan은 진입 전에 planned entry, worst entry, 초기 SL, TP1, TP2, 수량, 최대손실, 예상 fee·slippage와 순손익비를 고정한다. LONG은 ask, SHORT는 bid를 지연 후 소진하고, 체결 수량에 대해서만 TP1·TP2·SL PAPER 보호주문을 즉시 만든다. 초기 stop은 불리한 방향으로 넓히지 않는다.

### 이번 실행의 검증

| 검증 | 상태 | 실제 결과 |
|---|---|---|
| A~F 비용후 계획 | PASS | 6전략 × LONG/SHORT 12개 계획이 실제 호가·fee·slippage 후 순손익비 1.20 이상을 통과했다. |
| A~F 종단 TP/SL | PASS | 6전략 × LONG/SHORT × TP1→TP2/초기손절의 24개 시나리오에서 진입 직후 보호주문, 부분익절, 손절, 수량·비용·순손익 회계를 검증했다. |
| event-time·no-lookahead | PASS | A~F 확인시간 reset, B/D 롱·숏 눌림 대칭, 실제 duration·최대 retrace·가격 재가속과 미래 timestamp 제외를 검증했다. |
| 저장 공개시장 replay | PASS | 15,045 events를 두 번 replay해 checksum `f0c9ea71ef2952b35c0b86f68f284676bd6714f64376b0ffa1a00549dd8b2275`, 평가 41,628·적격 8·고유후보 5·main 종료 0·shadow 종료 7이 모두 일치했다. |
| replay 실행 전략 | PASS | 이 짧은 실제 공개시장 표본에서는 E만 엄격한 조건을 통과했다. A/B/C/D/F를 억지로 진입시키지 않았다. |
| 실제 browser 시작 | PASS | 실제 8870에서 한 번 클릭 후 READY→연결 중→작동 중, 초기 p95 65ms, PAPER·실제 주문 0을 확인했다. 최종 `run-2d24583436d9`는 RUNNING·paused false·p95 144ms·자산 1,000·main 거래 0·auth/실제주문 false였다. |
| 전체 자동검증 | PASS | repository hygiene, Ruff, mypy 75 files, backend 204, frontend 11 files·32 tests, ESLint, TypeScript, Vite 47 modules, PAPER build safety, security 107 source, Playwright desktop·tablet·mobile 3 PASS. |
| 최종 자연 main 진입 | NOT_OBSERVED | 최종 엄격한 코드의 짧은 LIVE 관찰에서는 A/B main 거래가 없었다. 자연신호 기준을 낮추지 않았다. |
| 전략 수익성 | NOT_PROVEN | replay의 7개 shadow 종료표본과 현재 전략별 표본은 성과 판단에 부족하다. 테스트 통과를 수익 보장으로 표현하지 않는다. |
| 6시간 / 24시간 soak | NOT_RUN | 이번 검증에서 전체 시간을 실행하지 않았다. |
| Release ZIP | NOT_RUN | 이번 범위는 현재 소스와 GitHub main 동기화이며 새 Release를 만들지 않았다. |

기계판독 증거는 `evidence/PHASE05_STRATEGY_ENTRY_EXIT_SIMULATION.json`이다. 최종 작동 화면은 `evidence/screenshots/phase05-final-live-running.png`이고 SHA-256은 `8784082d68bc49a8a1b43faa96d32106baaa21f98a8794d5bb5811564dae7646`다. `evidence/screenshots/phase05-prefinal-temporal-defect-trade-detail.png`는 고정 시간값을 제거하기 전에 관찰한 CBR 거짓 양성 가능성의 결함 증거일 뿐 최종 코드의 거래 성과 증거가 아니다.

구현 commit은 `8e24ffe1ea00b60d90297fdb5a85d209ea626bb5`이고, 실행증거 정리 commit은 `2a40186b293f00ebba4772091a3a015cd650f3f3`이다. [GitHub Actions 32674493842](https://github.com/robom-labs/flowscalper/actions/runs/32674493842)에서 validate 55초, browser 1분 15초, 실제 Chromium desktop·tablet·mobile E2E와 browser evidence 업로드가 모두 PASS했다.

## 21. 초단기 보유·표시 자릿수·독립 전략 통계 보강

2026-08-24 실제 불변 원장과 종료 감사를 먼저 조사했다. 기존 `run-2d24583436d9`의 대표 EDGE_DECAY 거래는 1.518초·1.696초·1.872초·5.396초만 보유했고, 체결 직후 유예 없이 불리 신호 하나가 800ms 지속되면 일반 근거약화 종료를 준비한 것이 직접 원인이었다. 정책 변경 뒤에도 초기 SL·TP와 데이터·시스템 안전청산은 즉시 유지한다.

### 수정과 범위

- 일반 EDGE_DECAY에만 체결 뒤 10초 유예, 서로 다른 불리 신호 최소 2개, 실제 event-time 3초 지속을 적용했다. 일반 근거약화 종료의 가장 이른 시각은 13초다. MFE가 0.8R 이상이면 이익 보호를 위해 유예만 생략할 수 있고 복수 신호·3초 확인은 유지한다.
- 공동계좌 main 거래와 독립 League 거래의 전략 표본 중복을 제거했다. 전략·프로필과 전략·종목 통계는 실제 공개시장의 독립 `shadow_trades`만 집계하고 공동계좌 성과와 오프라인 fixture는 별도 거래기록·자산곡선·DEMO 화면에 둔다.
- 승·패·보합, Wilson 95% 승률 범위, 기대값, Profit Factor, 비용, 낙폭, MAE/MFE와 보유시간을 표시한다. 현재 Run 자산·손익과 저장된 전체 독립표본을 같은 값처럼 보이지 않도록 설명을 분리했다.
- A/B는 공동·독립 PAPER, C~F는 독립 PAPER로 6개 모두 켜고 모든 LONG·SHORT를 허용한다. 실험전략 C~F를 공동계좌 ACTIVE로 승격하지 않았다.
- 원장 Decimal은 바꾸지 않고 화면에서만 자산 2자리와 값 크기에 맞는 가격·수량·손익·비용·거래량·보유시간 자릿수를 사용한다. 거래기록은 `이번 Run`을 기본으로 하고 불변 과거기록은 `전체 Run`에서 계속 볼 수 있다.

### 이번 실행의 실제 결과

| 검증 | 상태 | 실제 결과 |
|---|---|---|
| 초단기 종료 회귀 | PASS | 1~2초 복수 신호는 유지, 단일 불리 신호는 장시간이어도 유지, 일반 EDGE_DECAY는 13초 이후에만 종료, 0.8R 이익보호와 즉시 stale 경로를 검증했다. |
| A~F 진입·TP/SL | PASS | 6전략 × LONG/SHORT × TP1→TP2/초기손절 24개 시나리오에서 진입 즉시 보호주문·수량·비용·순손익 회계를 다시 통과했다. |
| 실제 변경 후 main 거래 | PASS | `run-c3f9aff1acb6`의 `paper-candidate-d804488428b94ed7-main-base`, PENGUUSDT LONG은 18.354초 보유 뒤 EDGE_DECAY 종료했다. 순손익은 -0.207608784 USDT로 수익 증거는 아니다. |
| 실제 변경 후 League 거래 | PASS | `run-2bcc02ff9d86`에서 E 전략 BASE/STRESS 6건은 15.664초, 15.704초, 18.448초 2건, 38.354초, 38.382초 보유했다. 종료 감사에는 FLOW_DECAY·MICROPRICE_ADVERSE·OPPOSITE_AGGRESSION_EFFICIENT 세 신호가 함께 기록됐다. |
| 1~2초 재발 | PASS | 새 정책 Run의 관찰된 main·League 7건에서 1~2초 종료 0건이다. 기존 원장은 수정·삭제하지 않았다. |
| 저장 공개시장 replay | PASS | `run-f14214b3b1dd`의 15,045 events를 두 번 replay했다. checksum `7a44e652f962f6fe46cdcc0c279fc34294fbbbee6845178912e4a2f409e239eb`, 전략평가 41,628·적격 8·후보 7·main 0·shadow 7이 두 번 일치했다. |
| 실제 8870 상태 | PASS | 최종 배포 `run-07ad829dbe61`, LIVE_SHADOW_PAPER·LIVE·RUNNING, wide 50·deep 20, p95 18ms, queue 0/4096, drop·reconnect·gap·persistence fault 0, critical lag·entry lock false, 실제주문·인증 false를 읽었다. |
| 실제 browser 화면 | PASS | 최종 서비스 재시작 뒤 한 번 클릭해 READY→연결 중→작동 중을 확인했다. PAPER 실제 주문 0, 거래기록 `이번 Run` 기본과 `전체 Run` 전환, 적응형 자릿수, 6/6 전략과 12/12 방향, 전략 통계 drawer를 직접 확인했다. |
| 전체 자동검증 | PASS | backend 207, frontend 12 files·36 tests, Playwright desktop·tablet·mobile 3, repository hygiene, Ruff, mypy 75 files, ESLint, TypeScript, Vite 48 modules, PAPER build safety, security 108 source 모두 PASS다. |
| 전략 수익성 | NOT_PROVEN | 실제 변경 후 관찰 거래는 모두 손실이며 대부분 전략은 30건 미만이다. 승률과 기대값은 표시되지만 표본 부족이며 수익을 보장하지 않는다. |
| 6시간 / 24시간 soak | NOT_RUN | 이번 실행은 약 7분 runtime snapshot과 개별 자연거래 관찰이다. 장시간 안정성으로 과장하지 않는다. |
| Release ZIP | NOT_RUN | 이번 범위는 현재 source와 GitHub main 동기화이며 새 Release를 만들지 않았다. |

기계판독 증거는 `evidence/PHASE06_POSITION_CHURN_AND_STRATEGY_STATISTICS.json`이고, 실제 전략 화면은 `evidence/screenshots/phase06-position-churn-strategy-statistics.jpg`다. screenshot SHA-256은 `59472a982ed1ee46287a39684228f0665de087cc8f3bdd11c73b45ae7d877d46`다. PASS는 이번 실행에서 실제 확인한 범위만 뜻한다.

구현·실행증거 commit `0c7203e3bd123a415825b841cfccf0c8710839a8`을 GitHub `main`에 동기화했다. [GitHub Actions 32690932657](https://github.com/robom-labs/flowscalper/actions/runs/32690932657)에서 validate 53초, browser 1분 8초, 실제 Chromium desktop·tablet·mobile E2E와 browser evidence 업로드가 모두 PASS했다.

## 22. 장시간 처리지연·전수 작동 점검 보강

2026-08-25 실제 8870 서비스와 앱 내 브라우저를 기준으로 시작·정지·재개, 시장·전략·기록·분석·설정, 차트, 자연 PAPER 진입·보호, 원장과 replay를 다시 점검했다. 실제 주문, private API, 인증과 API Key 경로는 계속 0이다.

### 발견한 결함과 수정

- 약 10시간 실행된 `run-07ad829dbe61`에서 실행경로 지연 P95가 5,318~7,875ms, P50이 최대 4,085ms까지 커졌고 신규진입이 `SAFETY_WAITING`으로 잠겼다. queue·drop·저장 fault는 0이어서 저장보다 CPU 처리경로를 조사했다.
- 실제 서비스 cProfile에서 `LocalOrderBook._apply_levels`와 `StrategySignalEvaluator.evaluate`가 주요 누적시간이었다. 상위호가 삭제 시 전체 1,000단계를 다시 정렬하고, 1,200개 과거창이 찬 뒤 통계 입력을 매번 다시 만들고 정렬하는 비용이 실행시간과 함께 증가했다.
- bid·ask 전체 가격을 정확한 증분 정렬 인덱스로 유지하고 전략 통계 6종도 동일 1,200표본 창에서 정확히 삽입·퇴출하게 바꿨다. robust z·percentile의 기준 결과 일치 테스트를 추가했으며 전략 임계값, 확인시간, 비용 게이트와 Decimal 원장은 바꾸지 않았다.
- 앱 내 브라우저에서 native Fullscreen 요청이 끝나지 않으면 전체화면 버튼이 반응하지 않았고 모바일 가격 통계가 클릭영역을 가로막았다. 클릭 즉시 CSS 전체화면을 켜고, 통계는 포인터를 받지 않으며 `지표`·`전체화면` 버튼을 한 줄에 고정했다.

### 이번 실행의 실제 결과

| 검증 | 상태 | 실제 결과 |
|---|---|---|
| 포화 과거창 microbenchmark | PASS | 20종목·각 1,200표본·1,000회 전략평가는 0.877117625초에서 0.369194708초로 2.38배, 1,000단계 top 삭제·재삽입 10,000회는 0.111795292초에서 0.011025916초로 10.14배 개선됐다. |
| 실제 시작·일시정지·재개 | PASS | 실제 브라우저에서 `시작 전 → 작동 중 → 사용자가 일시정지 → 작동 중`을 직접 확인했고 한 번 시작 뒤 자동 관찰이 유지됐다. |
| 실제 차트·화면 | PASS | SOLUSDT 선택, 3분→15분, MA5·RSI 선택, 종목검색, 전체화면 열기·닫기, 5개 주 화면과 전략 모드·방향 변경 후 복원을 직접 확인했다. 브라우저 console error는 0이다. |
| 자연 PAPER 진입·보호 | PASS | `run-b700234d1c03`의 PENGUUSDT SHORT LSA main 진입에 실제 entry 0.0095510, TP1 0.009456305600, TP2 0.009306981600, SL 0.00962791200, 수량 5,031, 최대계획손실 0.4649556985632 USDT가 진입과 동시에 생성됐다. |
| 초단기 종료 재발 | PASS | 같은 main은 37.070초 뒤 EDGE_DECAY로 종료했고 해당 Run의 LIVE shadow 최소 보유는 14.060초였다. 관찰된 거래에서 1~2초 종료는 0건이다. |
| 1분 실제 LIVE 표본 | PASS | 5초 간격 13개 표본에서 event 125,887→136,392, 실행경로 P50 29~34ms·P95 141~382ms, queue 최대 1, gap/resync/drop/persistence fault 0이었다. 기존 reconnect 누계 1은 표본 동안 증가하지 않았다. |
| 넓은 감시 지연 분리 | PASS | 1초 wide scanner age P95는 1,615~1,656ms였지만 실행가능 depth·trade 지연과 분리된 정보값이다. PAPER 안전판이 사용하는 실행경로 P95는 위 범위였고 `RUNNING`을 유지했다. |
| 저장 공개시장 replay | PASS | 고정 `run-f14214b3b1dd` 15,045건을 두 번 replay해 checksum `7a44e652f962f6fe46cdcc0c279fc34294fbbbee6845178912e4a2f409e239eb`, 평가 41,628·적격 8·후보 7·main 0·shadow 7이 모두 일치했다. |
| 원장 무결성 | PASS | active SQLite `PRAGMA quick_check`는 `ok`였고 기존 불변 거래는 수정·삭제하지 않았다. |
| 전체 자동검증 | PASS | backend 213, frontend 12 files·36 tests, Playwright desktop·tablet·mobile 3, Ruff, mypy 75 files, ESLint, TypeScript, Vite 48 modules, PAPER build safety, security 108 source, repository hygiene가 모두 PASS했다. |
| 실제 주문·인증 | PASS | 화면·API·replay에서 실제 주문 false/0, auth false/0을 유지했다. |
| 전략 수익성 | NOT_PROVEN | 이번 자연 main 거래도 순손익 -0.0249386670 USDT다. 작동 검증과 수익성은 다른 주장이고 현재 표본으로 수익을 보장하지 않는다. |
| 변경 후 6시간 / 24시간 soak | NOT_RUN | 실제 관찰과 포화 과거창 benchmark는 통과했지만 변경 후 멀티시간 전체 실행은 하지 않았다. |
| Release ZIP | NOT_RUN | 이번 범위는 현재 소스와 GitHub main 동기화이며 새 Release를 만들지 않았다. |

기계판독 증거는 `evidence/PHASE07_FULL_RUNTIME_AUDIT.json`이고 실제 모바일 최종 화면은 `evidence/screenshots/phase07-live-runtime-audit-mobile.png`다. screenshot SHA-256은 `ee7cdb1d019a7003ee0e2a5a1c5f72890faa77c78f55fe2b62986a04841456c8`다. 구현 commit은 `41e9063`이고 최초 증거 commit은 `d8a2db7`이다. [GitHub Actions 32744518964](https://github.com/robom-labs/flowscalper/actions/runs/32744518964)에서 validate 51초, browser 1분 8초, 실제 Chromium desktop·tablet·mobile E2E와 browser evidence 업로드가 모두 PASS했다.

## 23. 8전략 Strategy League와 LIVE replay 격리 증거

2026-08-25 기존 A~F의 진입 기준을 낮추지 않고 공식 주문장 연구를 별도 가설로 분리해 G/H를 EXPERIMENTAL·SHADOW 전용으로 구현했다. 실제 주문, private API, 인증, API Key와 wallet 경로는 계속 0이다.

### 연구 근거와 안전한 적용

- Cont·Kukanov·Stoikov의 주문장 이벤트 가격영향 연구는 짧은 구간 OFI의 가격영향이 깊이에 반비례할 수 있음을 보고한다.
- Stoikov의 micro-price 연구와 다단계 호가 간격 연구는 단순 mid보다 주문장 상태를 반영한 공정가 가설을 뒷받침한다.
- 이 연구들은 거래소·기간·시장구조가 현재 Binance USD-M PAPER 환경과 동일하지 않다. 수익성이나 현재 임계값을 증명하는 자료로 해석하지 않고 SHADOW 비교 가설에만 사용했다.
- 결정과 출처, 임계값, 적용 한계는 `docs/adr/ADR-016-depth-normalized-flow-and-multilevel-fair-price-shadow-strategies.md`에 기록했다.

### 발견한 결함과 수정

- 저장 공개시장 15,045 events를 실제 LIVE 서비스의 worker thread에서 replay하면 결과는 결정적이지만 Python CPU 경쟁 중 공개시장 supervisor가 한 번 재연결되고 임계 지연 누계가 증가했다.
- LIVE 모드의 replay를 독립 SQLite·Parquet 연결을 가진 별도 저우선순위 프로세스로 옮기고 앱별 동시 replay를 하나로 제한했다. fixture와 단위테스트 런타임은 기존 thread 경로를 유지한다.
- 신규 G는 top10 bid·ask VWAP과 반대편 수량으로 계산한 다중호가 공정가가 top microprice, 250ms·3s OFI, 1s 공격체결, 가격반응과 750ms 이상 같은 방향일 때만 적격이 된다.
- 신규 H는 3s OFI를 top10 평균 깊이 notional로 보정한 값의 과거-prefix robust z가 2.0 이상이고 OFI·체결·microprice·가격반응이 500ms 이상 정렬될 때만 적격이 된다.
- G/H는 기본 SHADOW·LONG·SHORT, 각 BASE·STRESS 독립계좌만 사용한다. A/B만 공동계좌 ACTIVE이고 C~H는 모두 SHADOW다.

### 이번 실행의 실제 결과

| 검증 | 상태 | 실제 결과 |
|---|---|---|
| G/H 피처·진입·보호 | PASS | 독립 참조식, LONG·SHORT 대칭, 핵심 거부코드, 과거-prefix robust z, 조건 reset과 event-time 지속성을 검증했다. A~H 양방향은 기존 비용후 계획과 TP1·TP2·초기 SL 종단 시나리오를 통과했다. |
| 저장 공개시장 replay 결정성 | PASS | `run-f14214b3b1dd`의 15,045 events를 세 번 replay해 checksum `34e157e3b62ba19895f10bc8deac24be57021376150bc01d4bc7ffaf25a2b233`, 평가 55,504·적격 9·후보 8·main 0·shadow 9가 모두 일치했다. |
| H replay 종단경로 | PASS | H SHORT BASE·STRESS 후보 준비, 진입 체결, 관리청산 준비와 종료 체결이 같은 ReplayEngine 경로에서 발생했다. |
| LIVE replay 격리 | PASS | 실제 14.058초 replay 동안 0.5초 간격 24표본에서 LIVE event가 2,597건 증가했다. 실행경로·wide P95 최대 0ms, queue 최대 2, 임계지연·reconnect·gap·drop 증가 0, 진입잠금 표본 0, 최종 RUNNING이었다. |
| 실제 H 자연 공개시장 표본 | PASS | `run-f79a63312b92`에서 H LIVE_PUBLIC BASE 2건·STRESS 1건이 자연 진입·종료했다. 보유시간 19.664~230.384초, BASE 1승 1패 순손익 -0.37396632 USDT, STRESS 0승 1패 -0.68828496 USDT다. 작동 증거일 뿐 수익성 증거는 아니다. |
| 1~2초 종료 재발 | PASS | 같은 Run의 main 2건은 20.898초와 68.484초, H는 최소 19.664초였다. 관찰된 거래에서 1~2초 종료는 0건이다. |
| 183초 실제 LIVE 표본 | PASS | 36표본 모두 RUNNING, event +32,974, queue 최대 2, reconnect·gap·drop·persistence fault·buffer drop 증가 0, 8전략·16계좌, 실제주문·인증 false를 확인했다. 메모리는 356.484~377.719MB였으며 이 짧은 창을 멀티시간 안정성 증거로 쓰지 않는다. |
| 실제 browser 화면 | PASS | 실제 8870을 새로고침해 작동 중·시장 관찰 계속 작동·PAPER 진입 작동·자동복구 켜짐·P95 0ms를 확인했다. 8개 전략, 16개 계좌, 8개 모드와 16개 방향 제어, H LONG 변경·복원, 5개 화면과 차트 전체화면을 직접 확인했다. |
| 원장 무결성 | PASS | active SQLite `PRAGMA quick_check`는 `ok`였고 기존 불변 기록을 수정·삭제하지 않았다. |
| 전체 자동검증 | PASS | backend 248, frontend 12 files·36 tests, Playwright desktop·tablet·mobile 3, Ruff, mypy 78 files, ESLint, TypeScript, Vite 48 modules, PAPER build safety, security 111 source와 repository hygiene가 모두 PASS했다. |
| G 자연 공개시장 적격·체결 | NOT_OBSERVED | 저장 replay와 이번 짧은 LIVE 관찰에서 G 자연 적격은 없었다. 신호를 만들기 위해 기준을 낮추지 않았다. |
| 전략 수익성 | NOT_PROVEN | H는 3건뿐이고 기존 전략도 충분하고 독립적인 유효 표본이 부족하다. 이번 자연 main·H 집계 역시 순손실이며 승률을 수익 보장으로 표현하지 않는다. |
| 6시간 / 24시간 soak | NOT_RUN | 183초 LIVE와 14초 replay 동시 실행을 통과했지만 변경 후 멀티시간 전체 실행은 하지 않았다. |
| Release ZIP | NOT_RUN | 이번 범위는 현재 source와 GitHub main 동기화이며 새 Release를 만들지 않았다. |

기계판독 증거는 `evidence/PHASE08_EIGHT_STRATEGY_AND_REPLAY_ISOLATION.json`이다. 실제 성과 화면은 `evidence/screenshots/phase08-eight-strategy-live-performance.jpg`이고 SHA-256은 `ec76d288d49519eae997c1b869ed3446ea36cc1bafb6c915e1f3796b26ad8887`다. 구현 commit은 `80fe973089aacdf72ae3182792b178d000566220`, 실행증거 commit은 `e5cfcfedd4e8dc95995fb192a1c42ddc1d2cdd48`이다. [GitHub Actions 32749612580](https://github.com/robom-labs/flowscalper/actions/runs/32749612580)에서 validate 53초, browser 1분 20초, 실제 Chromium desktop·tablet·mobile E2E와 browser evidence 업로드가 모두 PASS했다.

## 24. 현재 전략버전 성과 분리와 모드별 표본 무결성

2026-08-25 실제 불변 원장과 화면을 대조한 결과, `LIVE_PUBLIC`이라는 표본 유형만 같으면 서로 다른 진입·종료 로직으로 생성된 과거 전략 거래와 현재 전략 거래가 하나의 승률·기대값·Profit Factor에 합쳐지고 있었다. 이전 1~2초 종료 정책의 거래까지 현재 로직의 보유시간 통계에 섞이므로 현재 소프트웨어의 전략 결과라고 볼 수 없는 결함이었다.

### 발견한 결함과 수정

- A~H 전략 식별자에 구현 revision을 결합한 전략 버전을 Run과 신규 shadow 거래에 기록한다. 기본 성과는 `sample_type=LIVE_PUBLIC`이면서 현재 전략 버전과 정확히 같은 독립 BASE·STRESS 거래만 집계한다.
- 과거 거래 154건은 삭제·수정하지 않았다. checksum을 검증한 조회 결과에만 소속 Run의 `strategy_version`과 `config_hash`를 보강하고, 전략·프로필·종목 화면에 제외 건수를 공개한다.
- DEMO와 REPLAY의 공통 shadow 변환기가 LIVE로 표시될 수 있던 경로를 각각 `DEMO_FIXTURE`와 `REPLAY`로 분리했다. 오프라인 표본은 LIVE 성과에 들어갈 수 없다.
- 성과표 한 행에서 현재 Run 가상계좌의 비용·낙폭과 저장된 전체 통계를 섞던 표시를 현재버전 report의 수수료·슬리피지·낙폭으로 통일했다. 현재 Run 자산은 별도 열과 요약으로 유지한다.
- LIVE 대시보드는 매 snapshot마다 SQLite를 읽지 않고 Run 시작 때 현재·과거 버전 cache를 분리한다. 결정과 불변 원장 호환 방식은 `docs/adr/ADR-017-current-strategy-version-performance-scope.md`에 기록했다.

### 이번 실행의 실제 결과

| 검증 | 상태 | 실제 결과 |
|---|---|---|
| 현재·과거·오프라인 표본 격리 | PASS | 현재버전 LIVE_PUBLIC만 집계하고 과거버전 LIVE_PUBLIC은 제외 건수로, OFFLINE fixture는 비LIVE로 분리하는 원장·cache·API 회귀검사를 통과했다. |
| 과거 불변 원장 호환 | PASS | 과거 shadow payload와 checksum을 다시 쓰지 않고 연결된 Run의 검증된 config만 조회 결과에 보강했다. active SQLite `PRAGMA quick_check`는 `ok`였다. |
| 신규 모드별 표본 | PASS | 신규 LIVE shadow에는 현재 전략버전·Run config hash·`LIVE_PUBLIC`, REPLAY에는 `REPLAY`가 저장됨을 종단 원장 테스트로 확인했다. DEMO Run은 별도 `DEMO_FIXTURE` 계약을 유지한다. |
| 현재버전 자연 공개시장 표본 | PASS | 스냅샷 시점 15건만 현재 통계에 포함됐고 과거버전 154건은 제외됐다. 최단 보유 13.416초, 최장 120.378초, 10초 미만·13초 미만 0건으로 1~2초 종료 재발은 관찰되지 않았다. |
| 전략별 승률·수익성 | NOT_PROVEN | 16계좌 중 거래가 있는 계좌도 1~6건뿐이며 모두 `표본 부족`이다. 일부 1건 승률 100%나 손실 표본을 전략 수익성으로 해석하지 않았다. 기준을 낮춰 거래를 만들지 않았다. |
| 저장 공개시장 replay | PASS | `run-e2411f324b33`의 85,838 events를 replay해 전략평가 304,496·적격 86·고유후보 8·shadow 종료 10·main 종료 0, checksum `700f2cd183c0bffbff16a74add18ddc9b7628c05574eefc4131a10946b1f21e0`을 기록했다. 실제주문·인증은 false였다. |
| 대형 replay의 LIVE 무지연 | NOT_PROVEN | replay는 별도 저우선순위 프로세스에서 완료되고 LIVE API는 응답했지만, 같은 컴퓨터에서 replay·전체 회귀·브라우저 검증을 함께 실행하는 동안 임계지연 누계와 reconnect 1회가 증가했다. 최종 부하 해제 뒤 P95 188ms·queue 0·drop/gap/resync/fault 0·active lock false로 회복했으나 무영향이라고 과장하지 않는다. |
| 실제 8870 상태 | PASS | 최종 스냅샷은 RUNNING·LIVE_SHADOW_PAPER·LIVE·PAPER, wide 50·deep 20, event 224,477, P50 26ms·P95 188ms, queue 0, entry lock false, last error 없음, 실제주문·인증 false였다. |
| 실제 browser 화면 | PASS | 시작을 직접 눌러 READY→RUNNING을 확인하고 시장·전략·전략상세·분석·전략별 종목을 열었다. 현재버전 설명과 과거 제외 건수를 확인했고 browser console error·warning은 0이었다. |
| 반응형 화면 | PASS | 실제 Chromium desktop·tablet·mobile에서 성과와 전략×종목 화면, 현재버전 안내와 과거 제외 문구를 확인했다. |
| 공개시장 네트워크 | PASS | Binance eligible 527, catalog 696, Upbit KRW 관찰 286, 세 시장 candle 각 200, WebSocket 16 events, 수신 P95 23.631ms, credential·Authorization·실제주문 false였다. |
| 전체 자동검증 | PASS | backend 248, frontend 12 files·38 tests, Playwright desktop·tablet·mobile 3, Ruff, mypy 78 files, ESLint, TypeScript, Vite 48 modules, PAPER build safety, security 111 source와 repository hygiene가 모두 PASS했다. |
| 6시간 / 24시간 soak | NOT_RUN | 약 21분 현재 Run과 개별 재현·회귀 결과다. 30분 반복 모니터는 활성 상태지만 멀티시간 수용결과로 기록하지 않는다. |
| Release ZIP | NOT_RUN | 이번 범위는 현재 소스와 GitHub main 동기화이며 새 Release를 만들지 않았다. |

기계판독 증거는 `evidence/PHASE09_CURRENT_STRATEGY_VERSION_SCOPE.json`, 공개시장 smoke는 `evidence/PHASE09_PUBLIC_MARKET_SMOKE.json`이다. 실제 Chrome 증거는 `evidence/screenshots/phase09-current-version-strategy-detail-actual-chrome.jpg`, `phase09-current-version-performance-actual-chrome.jpg`, `phase09-current-version-strategy-symbol-actual-chrome.jpg`다. desktop·tablet·mobile 반응형 증거 6개도 같은 `evidence/screenshots/phase09-*` 이름으로 보존했다.

구현 commit은 `e471216d2d8413e7b03d4acdce639f290ee14e51`이다. [GitHub Actions 32754123908](https://github.com/robom-labs/flowscalper/actions/runs/32754123908)에서 validate 55초, browser 1분 28초, 실제 Chromium desktop·tablet·mobile E2E와 browser evidence 업로드가 모두 PASS했다. PASS는 이번 실행에서 실제 확인한 범위만 뜻하며 수익성·6시간·24시간·Release ZIP은 각각 `NOT_PROVEN` 또는 `NOT_RUN`이다.

## 25. 대형 replay·LIVE 경합, 시간 동기화와 거래 재생 응답성

2026-08-25 수십만 건 저장 Run replay를 실제 공개시장 PAPER 서비스와 동시에 실행해 결과 무결성과 LIVE 처리 성능을 분리해 점검했다. 실제 주문, private API, 인증, API Key와 wallet 경로는 계속 0이다.

### 발견한 결함과 수정

- 기존 별도 process replay도 저장 이벤트 읽기, 전략 재처리, 정렬과 checksum에서 CPU를 오래 사용했다. 실행 시작 이후 누적 평균으로 CPU를 제한한 첫 구현은 앞선 고부하를 뒤늦게 갚는 긴 sleep 빚을 만들었다.
- 기존 checksum은 정규화된 이벤트 전체와 결정경로 전체를 다시 canonical JSON material로 만들어 peak RSS가 약 2.1GB까지 증가했다. schema 3은 각 이벤트와 결정경로 항목을 길이 prefix streaming SHA-256으로 묶고 최종 material에는 digest와 count만 남긴다.
- replay worker는 `nice(19)`, 한 코어 기준 구간별 10% CPU 예산과 최대 0.5초 sleep을 사용한다. 전체 replay·timeline·거래 집중 replay가 하나의 lock을 공유하며, 이미 replay 중이면 다른 UI 요청은 HTTP 409 `REPLAY_BUSY`와 한국어 재시도 안내를 즉시 받는다.
- 거래 집중 replay는 실제 거래 전 20분·종료 후 5분 시간창만 읽고, 해당 거래를 포함하는 안전한 replay 결과와 schema v7 zlib·SHA-256 cache를 재사용한다.
- 신규 Parquet 필터 조회도 배치 전체 row checksum과 `batch_checksum`을 먼저 대조한다. 일부 row가 잘린 batch가 남은 종목·시간 필터만 정상이라는 이유로 통과하지 못한다.
- 로컬 시각이 거래소보다 느릴 때 지연이 0ms로 숨던 계산을 Binance·Bybit 공개 시각의 최소 RTT 중간점 오프셋으로 보정했다. 운영체제 시각은 변경하지 않고 보정값·RTT·동기화 상태를 시스템 화면에 표시한다.
- 정상 연결 교체와 비정상 재연결을 분리하고, 현재 거래기록 기본값은 현재 전략 revision의 `LIVE_PUBLIC` main 거래만 사용한다. 이전 기록은 삭제하지 않고 불변 원장에 보존한다.

### 이번 실행의 실제 결과

| 검증 | 상태 | 실제 결과 |
|---|---|---|
| 기존 대형 replay 결과 무결성 | PASS | schema 2 누적예산 구현으로 `run-7525441a7665` 332,553 events를 7,055초에 완료했다. 평가 1,360,224·적격 342·후보 21·main 5·shadow 22·결정경로 1,377, checksum `ca9ecea905d56df61ab33986d2b97e32d377cb0fbfc956af710a843d050f9398`이며 실제주문·인증은 false다. |
| 기존 대형 replay LIVE 성능 | FAIL | 234개 LIVE 표본의 실행경로 p95 최대 3,352ms, 병렬 회귀 부하 종료 뒤에도 최대 1,804.5ms, queue 최대 22, critical lag 누계 최대 2,163, 진입잠금 표본 5개였다. 계획 회전 7회와 별개로 비정상 재연결·gap·drop·저장 fault는 0이지만 LIVE 무영향 수용기준은 충족하지 못했다. |
| schema 3 중간 replay 결정성 | PASS | 같은 `run-d2d9e34a0242` 85,714 events를 두 번 replay해 472초·473초에 완료했다. 두 실행 모두 평가 154,208·적격 24·후보 6·main 0·shadow 9·결정경로 396, checksum `e88e18b62d3c0b40efcfb6529aae3e7eea118dfacf40c49758452a86ebcd1fc7`로 일치했다. |
| schema 3 중간 replay LIVE 격리 | PASS | 두 실행의 LIVE p95 최대는 171.5ms와 659.5ms, queue 최대는 2와 17, replay peak RSS는 약 529MB와 536MB였다. 두 표본 모두 비정상 재연결·gap·drop·critical lag·진입잠금·저장 fault 0이었다. |
| replay 동시요청·저장 무결성 회귀 | PASS | 긴 replay 중 두 번째 UI 요청은 `REPLAY_BUSY` 409를 받고, 필터형 Parquet의 잘린 배치·잘못된 batch checksum·row checksum을 모두 거부하는 테스트를 통과했다. |
| 자연 PAPER 진입·보유 | PASS | 진단 중 `run-e301d70b9ba8`에서 자연 main 4건이 진입·TP·SL 확정 뒤 종료됐다. 보유시간은 13.940초, 60.216초, 22.876초, 14.598초이고 1~2초 종료는 0건이다. 네 건 모두 순손실이므로 수익성은 `NOT_PROVEN`이다. |
| Fresh LIVE PAPER | PASS | 최종 `run-b987d1d386c6`은 1,000 USDT, 실현·미실현손익·수수료·슬리피지·거래 0에서 `RUNNING`이다. wide 50·deep 20, p50 21ms·p95 131ms, queue 0, 비정상 재연결·gap·drop·critical lag 0, 진입잠금 false, 거래소 시각 +2,034ms·RTT 46ms·`SYNCED`였다. |
| 실제 browser 조작 | PASS | 실제 8870에서 시작 상태, 사용자 일시정지→재시작, 시장·전략·기록·분석·설정 화면을 직접 열었다. 차트를 1분으로 바꾸고 MA5를 켜짐→꺼짐→켜짐으로 전환했으며 최종 `작동 중`, 8/8 전략 켜짐, 실제 주문 경로 0, browser console error 0을 확인했다. |
| 원장 무결성 | PASS | active SQLite schema v7 `PRAGMA quick_check`는 `ok`였다. 1.7GB 원장에 Run 49·main 37·shadow 288건이 있으며 기존 불변 기록 삭제 0이다. |
| 전체 자동검증 | PASS | backend 260, frontend 40, fixture backend 11, Playwright desktop·tablet·mobile 3, Ruff, mypy 80 source, ESLint, TypeScript, Vite build, PAPER build safety, security 113 source와 repository hygiene가 모두 PASS했다. |
| 새 schema 3의 332,553건 전체 replay | NOT_RUN | 수정 뒤 85,714건 두 번으로 결정성과 LIVE 격리를 확인했다. 332,553건 전체를 새 schema로 다시 실행한 결과는 아직 없다. |
| 전략 수익성 | NOT_PROVEN | 자연 main 4건은 작동·보호 경로 증거일 뿐 전부 순손실이며, 독립 표본도 수익성을 판단하기에 부족하다. 진입 기준을 낮추지 않았다. |
| 변경 후 6시간 / 24시간 soak | NOT_RUN | 반복 모니터는 계속 활성 상태지만 이번 증거 시점에 6시간·24시간 전체 경과를 완료하지 않았다. |
| Release ZIP | NOT_RUN | 이번 범위는 현재 소스와 GitHub main 동기화이며 새 Release ZIP은 만들지 않았다. |

기계판독 증거는 `evidence/PHASE10_REPLAY_LIVE_ISOLATION.json`이다. 구현 commit은 `924e8b39e421bd4a1b50c5f868b8f7747e87fc35`다. [GitHub Actions 32780373377](https://github.com/robom-labs/flowscalper/actions/runs/32780373377)에서 validate 57초, browser 1분 12초, 실제 Chromium desktop·tablet·mobile E2E와 browser evidence 업로드가 모두 PASS했다. 새 schema 332,553건 전체 replay·전략 수익성·6시간·24시간·Release ZIP은 각각 `NOT_RUN` 또는 `NOT_PROVEN`이다.

## 26. OFI·단기수익률 동행 I 전략과 replay 우선순위 보강

2026-08-25 공식 시장미시구조 연구와 현재 소스를 대조해, 깊이보정 OFI만 보는 H와 다른 가설로 `OFI_RETURN_CONFLUENCE_V1`을 추가했다. 이 전략은 실제 prefix 3초 가격수익률까지 OFI와 같은 방향으로 움직여야 하며 EXPERIMENTAL·SHADOW 전용이다. 연구는 가설의 근거일 뿐 수익성 증거가 아니므로 기존 ACTIVE 전략에 섞지 않았고 자연신호가 없을 때도 임계값을 낮추지 않았다. 상세 결정은 ADR-019에 있다.

### 구현과 전략 안전경계

- Registry를 A~I 9개 전략과 BASE·STRESS 18개 독립 PAPER 계좌로 확장했다. A/B만 ACTIVE이고 C~I는 SHADOW이며 모든 LONG·SHORT는 기본으로 켜져 있다.
- I는 정상 RANGE·TREND 레짐, spread 8bp 이하, 방향성 깊이보정 OFI robust z 1.5 이상, 250ms·3s OFI 정렬, 실제 prefix 3초 수익률 2bp 이상, microprice 0.20bp 이상, 가격반응 효율 0.30 이상과 event-time 1,000ms 지속성을 모두 요구한다.
- 진입 전 비용 게이트와 기존 TREND TP1·TP2·SL·수량·최대손실·BASE/STRESS 체결 경로를 그대로 사용한다. 실제 주문·private API·인증·API Key·secret·wallet 경로는 추가하지 않았다.
- 3초 수익률 기준가격은 목표 시각 이전의 가장 가까운 동일종목 표본이며 최대 1.5초까지만 오래될 수 있다. 미래 timestamp와 기준가격 없는 표본은 거부한다.

### 저장 공개시장 replay와 LIVE 경합 수정

15,045개 실제 공개시장 저장 이벤트인 `run-f14214b3b1dd`를 세 번 replay했다. `replay-077ee42417924c1b`, `replay-67f6e51924c64a13`, `replay-bc66d3a1a2ca4d8e`는 모두 checksum `f7b59481f5c79184697fc92d59696171d9f61f2efc62259c12c7263a3d437cee`, 전략평가 62,442회, 적격 9, 고유 후보 8, main 0, shadow 종료 9와 최종 `OBSERVING_NO_MAIN_TRADE`가 일치했다. I의 적격 경로는 0이었고 기준은 그대로 유지했다.

기존 10% 협력 CPU 예산 replay 중 LIVE 누적 critical lag가 증가한 실제 관찰을 근거로 replay 예산을 5%로 낮추고, 시장 입력 16건과 checksum 128건마다 협력 양보하도록 바꿨다. 수정 후 동일 replay와 `run-94899287d623` LIVE를 225초 병행한 45표본 결과는 다음과 같다.

| 항목 | 수정 후 실제 결과 |
|---|---:|
| LIVE events | 8,057 → 39,596, `+31,539` |
| 실행경로 p50 / p95 최대 | 22.5ms / 369.5ms |
| wide age p95 최대 | 1,944.5ms |
| queue 최대 | 2 |
| critical lag 증가 / active / entry lock | 0 / false / false |
| 비정상 reconnect / gap / drop | 0 / 0 / 0 |
| persistence fault / buffer drop | 0 / 0 |
| process CPU / memory 최대 | 95.253% / 366.094MB |

전체 회귀·빌드 자체를 LIVE와 동시에 실행했을 때는 누적 critical count가 0에서 26으로 증가했다. 이를 replay 수정 성공으로 숨기지 않고 별도 부하로 분리했다. 테스트 부하가 끝난 뒤 117.737초·24표본에서는 events `+15,020`, p95 152.5~536.5ms, queue 최대 2, critical count 추가 증가 0, active critical·entry lock·reconnect·gap·drop·persistence fault 0으로 회복했다. 이 결과는 replay 우선순위 수정과 부하 후 자동회복의 단기 증거이며 모든 개발 부하와 6시간·24시간 안정성을 증명하지 않는다.

### 실제 LIVE·브라우저·거래 표본

- 실제 앱 내 브라우저에서 시작 한 번으로 `시작 전 → 연결 중 → 작동 중`, 일시정지 한 번으로 `사용자가 일시정지`, 재개 한 번으로 `작동 중` 복귀를 확인했다.
- 1280×720 시장 화면은 root 폭·높이 1280×720, chart 880×542였고 overflow, console error와 warning은 0이었다.
- 전략 화면은 9개 행, 선택된 mode 9개, 선택된 방향 18개와 `9/9 전략 켜짐 · 실제 주문 0`을 표시했다. 신규 I 상세의 SHADOW, BASE/STRESS와 현재버전 표본 0을 직접 확인했다.
- 최종 snapshot은 `run-94899287d623`, LIVE·PAPER·RUNNING, wide 50·deep 20, events 108,554, p50 9.0ms·p95 152.5ms, queue 0, critical active·entry lock·reconnect·gap·drop·fault 0, 거래소 시각 `SYNCED`, 실제 주문·인증 false였다.
- 이 Run의 자연 main PAPER 거래 1건은 SUIUSDT LSA LONG으로 23.524초 보유 후 `EDGE_DECAY` 종료됐다. 진입 0.804900, 초기 SL 0.798411200, TP 0.8254541600, 순손익 -0.161123610 USDT였으며 1~2초 종료는 아니었다.
- 현재 전략버전 독립계좌 표본은 전략별 0~7건이고 모두 `표본 부족`이다. I의 BASE·STRESS 자연 표본은 0이며 전체 전략 수익성은 `NOT_PROVEN`이다.

화면 증거는 `evidence/screenshots/wave21-live-market-1280x720.png`, `evidence/screenshots/wave21-live-strategies-1280x720.png`, `evidence/screenshots/wave21-live-strategies-full.png`이다. 기계판독 수치와 전략별 현재 표본은 `evidence/WAVE21_OFI_RETURN_AND_REPLAY_QA.json`에 있다.

### 최종 검증 상태

| 검증 | 상태 | 이번 실행의 실제 결과 |
|---|---|---|
| backend pytest | PASS | 279 passed, 38.81초 |
| frontend Vitest | PASS | 12 files, 40 passed |
| Ruff / mypy | PASS | 오류 0 / 81 source files 오류 0 |
| ESLint / TypeScript | PASS | 오류 0 / 오류 0 |
| production build | PASS | 48 modules, JS 485.73kB·gzip 150.64kB, PAPER build safety PASS |
| Playwright | PASS | capture를 끈 최종 desktop·tablet·mobile 3 passed, 과거 Wave 스크린샷 덮어쓰기 없음 |
| security / repository hygiene | PASS | 114 source, violation·secret-like·real-order path 0 / 위반 0 |
| SQLite | PASS | `PRAGMA quick_check=ok` |
| 실제 브라우저 | PASS | 시작·일시정지·재개·시장·전략·상세, 9/9·18/18, console error·warning 0 |
| 결정적 저장 replay | PASS | 15,045 events 세 checksum·평가·후보·shadow 결과 일치 |
| replay 병행 225초 | PASS | p95 최대 369.5ms, critical/reconnect/gap/drop/lock/fault 0 |
| 실제 주문·private API·인증 | PASS | 모두 0 또는 false |
| 신규 I 자연 LIVE 적격·거래 | NOT_OBSERVED | 현재 LIVE와 저장 replay에서 0, 기준 완화 없음 |
| 전략 수익성 | NOT_PROVEN | 전략별 0~7건, 모두 표본 부족 |
| 6시간 / 24시간 soak | NOT_RUN | 단기 표본을 장시간 완료로 표현하지 않음 |
| Release ZIP | NOT_RUN | 이번 Wave에서 만들지 않음 |
| GitHub main / Actions | PASS | 구현 commit `24b8463891c3b8bc8199aac220f5624299b0537d`을 main에 push했고 [Actions 32785122708](https://github.com/robom-labs/flowscalper/actions/runs/32785122708)의 validate 51초·browser 1분4초·browser evidence upload가 모두 PASS |
| FAIL / BLOCKED | 0 / 0 | 현재 해결하지 못한 필수 로컬 검증 실패와 blocker 없음 |

구현·실행증거 commit은 `24b8463891c3b8bc8199aac220f5624299b0537d`이다. 같은 SHA의 GitHub Actions에서 로컬과 독립된 설치·저장소 위생·lint·typecheck·backend/frontend test·production build와 실제 Chromium desktop·tablet·mobile E2E가 모두 통과했다.

## 27. wall-clock 보정과 계획 WebSocket 교체 복구

2026-08-25 장시간 실제 공개시장 점검에서 `run-94899287d623`가 계획 교체 뒤 `SAFETY_WAITING`으로 바뀌고 실행경로 지연 p50 약 2,120ms·p95 약 2,321ms를 계속 표시했다. 안전잠금은 설계대로 신규 PAPER 진입을 막았지만 실제 Binance 공개 time과 로컬 시각 차이는 당시 약 63~65ms였고 telemetry는 이전 +2,158ms를 계속 적용하고 있었다. 저장 Parquet의 정상 depth 이벤트도 2,114~2,167ms 지연으로 기록돼 고정 wall-clock 오프셋이 원인임을 확인했다.

### 발견한 결함과 수정

- 공개 거래소 시각을 시작·교체 시점의 고정 wall-clock 오프셋으로만 보관해, 실행 중 macOS 시각이 보정되면 이후 정상 이벤트를 거짓 임계지연으로 계산했다.
- 최소 monotonic RTT 공개 time 응답의 서버 timestamp를 monotonic 기준점에 고정하고, 이후 지연을 monotonic 경과시간으로 계산하도록 바꿨다. 화면의 거래소·로컬 오프셋은 현재 wall clock과의 차이로 계속 갱신한다.
- Binance의 wide·depth·trade 세 WebSocket 기본 close wait가 순차 적용돼 30초 단축 표본에서도 첫 계획 교체가 끝나지 않았다. 각 공개 연결 close timeout을 1초로 제한했다.
- 기존 스트림이 끝난 뒤 새 metadata·snapshot을 준비하는 동안 `RECONNECTING`과 진입잠금 적용이 늦었다. 계획 교체 집계 즉시 두 상태를 설정하고 fresh sequence-valid depth 뒤에만 해제한다.
- 서비스 재시작 뒤 READY로 돌아와도 이전 Run의 `새 PAPER 진입 N건` 알림이 브라우저 상태에 남았다. `run_id`가 바뀌면 알림·집중 포지션·종료 검토를 함께 초기화한다.
- 세부 결정은 `docs/adr/ADR-020-monotonic-venue-clock-and-rotation-recovery.md`, 수치는 `evidence/WAVE22_CLOCK_ROTATION_QA.json`에 기록했다.

### 실제 공개시장·서비스 검증

| 검증 | 상태 | 이번 실행의 실제 결과 |
|---|---|---|
| wall-clock +2초 회귀 | PASS | host UTC만 +2초 이동해도 monotonic 거래소 지연은 0ms를 유지하고 표시 오프셋만 +2,000ms에서 +10ms로 갱신됨을 검증했다. |
| 교체 준비 전 안전잠금 | PASS | 두 번째 provider `prepare()`를 의도적으로 막은 동안 계획 교체 1·`RECONNECTING`·entry lock true를 확인했다. |
| 비계획 재연결 재검증 | PASS | 첫 WebSocket 오류와 첫 재준비 실패를 연속 주입한 뒤 backoff·세 번째 공개 준비·새 depth에서 LIVE로 복구되고 이전 오류가 지워지는 경로를 검증했다. |
| 실제 공개 스트림 단축 교체 | PASS | Binance public wide 12·deep 10, 6초 주기 28초 실행에서 계획 교체 3회와 복구 3회를 완료했다. 최대 복구 0.919초, 최종 event 1,121·p50 20.535ms·p95 22.851ms·wide p95 829.916ms, 비계획 reconnect·critical·drop·gap·lock 0이었다. |
| 실제 서비스 기본 15분 교체 | PASS | `run-19533130477b`에서 생산 기본 900초 교체 1회를 관찰했다. `RECONNECTING`·entry lock true가 먼저 보였고 1.749초 뒤 fresh depth·LIVE·entry lock false로 복구됐다. 기준점 이후 event는 99,856→127,612로 27,756건 증가했고 교체 직후에도 62건이 추가됐다. 복구 p50 58.665ms·p95 259.870ms, 비계획 reconnect·drop·gap·error 0이었다. |
| Fresh READY와 브라우저 시작 | PASS | 서비스 재시작 뒤 1,000 USDT·손익·비용·슬리피지·거래 0의 READY를 확인하고 실제 앱 내 브라우저에서 시작 한 번으로 0.5초 `연결 중`, 1초 `작동 중`을 확인했다. |
| 실제 서비스 부하 후 상태 | PASS | 전체 pytest 뒤 8개 2초 표본에서 event 5,775→7,627, deep p95 56.561~79.454ms, queue 최대 1, critical count·active·entry lock·비계획 reconnect·drop·gap·error 0이었다. |
| 실제 브라우저 최종 상태 | PASS | `작동 중`, 시장 관찰 계속 작동, 새 PAPER 진입 작동, 자동 복구 켜짐, P95 94ms와 console error·warning 0을 확인했다. |
| 재시작 뒤 낡은 알림 제거 | PASS | 재시작 직후 실제 화면에서 이전 Run의 `새 PAPER 진입 2건` 알림을 재현했다. Run 변경 초기화와 Vitest를 추가한 뒤 새 빌드에서 알림이 사라졌고, 실제 시작 한 번으로 2.5초 안에 `작동 중`이 됐다. 최종 `run-88eeb45b4568`은 1,000 USDT·성과·비용·거래 0, event 1,610·p50 20.282ms·p95 38.330ms, critical·lock·reconnect·drop·gap·error 0, console error·warning 0이었다. |
| 자연 main PAPER 거래 | PASS | DOGEUSDT LSA SHORT가 불변 계획·비용 경로로 진입해 21.068초 뒤 `EDGE_DECAY` 종료됐다. 순손익 -0.117817546 USDT, 수수료 0.123552546, 슬리피지 0.005735이며 1~2초 종료는 아니고 수익성 증거도 아니다. |
| SQLite 무결성 | PASS | active ledger `PRAGMA quick_check=ok`, 기존 불변 Run·거래 삭제·수정 0이다. |
| 실제 주문·private API·인증 | PASS | 실제 서비스와 security scan에서 모두 0 또는 false다. |

기본 교체 전 로컬 검증 구간에는 누적 임계지연 406건이 별도로 있었다. 저장된 154,000행을 직접 대조한 결과 2026-08-25 08:04:05~08:04:49 KST의 43.418초 구간에 TRADE 385건·DEPTH 21건이 몰렸고 lag는 1,503.901~5,319.006ms였다. 이것은 교체보다 먼저 발생했으므로 교체 정지나 wall-clock 보정 재발은 아니다. 해당 순간에는 1,500ms fail-closed 안전잠금이 작동했고 이후 자동 해제됐다. 교체 뒤 40초·20표본에서 event 129,830→135,404, critical count 406→406, active critical·entry lock·비계획 reconnect·drop·gap·error 0이었고, 최종 backend 283개 회귀검사 뒤 event 187,574까지도 누계가 증가하지 않았다. Parquet 시각만으로 당시 경쟁 프로세스까지 특정할 수는 없으므로 이를 장시간 무지연 증거로 과장하지 않는다.

### 자동검증과 한계

| 검증 | 상태 | 실제 결과 |
|---|---|---|
| backend pytest | PASS | 283 passed, 12.67초 |
| supervisor·시각 targeted | PASS | 19 passed |
| frontend Vitest | PASS | 12 files, 41 passed |
| Playwright | PASS | desktop·tablet·mobile 3 passed |
| Ruff / mypy | PASS | 오류 0 / 81 source files 오류 0 |
| ESLint / TypeScript | PASS | 오류 0 / 오류 0 |
| production build | PASS | 48 modules, PAPER build safety PASS |
| security / repository hygiene | PASS | 114 source, violation·secret-like·real-order path 0 / 위반 0 |
| 전략 수익성 | NOT_PROVEN | 이번 자연 main 1건은 순손실이며 전략별 충분한 독립 표본이 없다. |
| 6시간 / 24시간 soak | NOT_RUN | 단축 실제 교체와 현재 서비스 표본을 멀티시간 수용결과로 표현하지 않는다. |
| Release ZIP | NOT_RUN | 이번 Wave에서 만들지 않았다. |
| GitHub main / Actions | PASS | 구현 commit `42536795aa718edb2922fde9478a50a08a1da3d0`을 main에 push했고 [Actions 32789067527](https://github.com/robom-labs/flowscalper/actions/runs/32789067527)의 validate 51초·browser 1분26초·browser evidence upload가 모두 PASS했다. |

FAIL과 BLOCKED인 필수 로컬 검증은 현재 0이다. 실제 인터넷 품질과 장시간 성능은 계속 모니터링하며 자연신호를 만들기 위해 전략 기준을 낮추지 않는다.

Wave 22 구현·실행증거 commit은 `42536795aa718edb2922fde9478a50a08a1da3d0`이다. 같은 SHA의 GitHub Actions에서 로컬과 독립된 설치·저장소 위생·lint·typecheck·backend/frontend test·production build와 실제 Chromium desktop·tablet·mobile E2E가 모두 통과했다.

## 28. top10 호가 기울기 비대칭 J와 10전략 PAPER 리그

2026-08-25 기존 A~I의 실제 소스·테스트·현재버전 성과를 대조하고 공식 시장미시구조 연구를 조사했다. 기존 전략이 쓸기·압축·OFI·queue·aggressor flow·top10 microprice·단기수익률을 이미 사용하므로 같은 변수의 숫자만 바꾼 복제 전략은 추가하지 않았다. 대신 top10 bid·ask에서 중간가격까지의 거리 1bp당 누적 명목깊이를 계산해 한쪽의 얇음과 반대쪽 지지 깊이가 동시에 지속되는지 보는 `BOOK_SLOPE_ASYMMETRY_V1`을 J로 추가했다.

Næs·Skjeltorp와 Cenesizoglu·Dionne·Zhou의 연구는 호가장 기울기와 가격동학의 관계를 다루지만 현재 Binance USD-M PAPER 전략의 수익성을 증명하지 않는다. Binance 공식 공개 WebSocket 문서는 top10을 포함하는 공개 depth 입력을 제공하는 기술 근거로만 사용했다. J는 이 가설을 장기간 반증 가능하게 비교하기 위한 EXPERIMENTAL·SHADOW 전략이며 자동 승격·임계 완화는 없다. 세부 결정과 출처는 `docs/adr/ADR-021-book-slope-asymmetry-shadow-strategy.md`에 기록했다.

### 구현과 안전경계

- top10 bid·ask 각각에서 거리 1bp당 누적 명목깊이의 10단계 평균을 계산한다. 동일종목 최대 1,200개 과거창을 증분 정렬하며 현재 snapshot은 모든 평가가 끝난 뒤에만 넣어 look-ahead를 막는다.
- 최소 과거표본 32개, 반대쪽 기울기 percentile 0.15 이하, 지지쪽 percentile 0.50 이상, 지지/반대 기울기비 1.50 이상, spread 8bp 이하를 요구한다.
- 250ms·3초 OFI, 1초 공격체결 불균형 0.10, microprice 0.15bp, 가격반응효율 0.25와 event-time 1,000ms 지속이 같은 방향이어야 한다.
- 진입 전에 entry·TP1·TP2·SL·수량·최대손실·예상비용을 확정하는 기존 비용후 추세 계획과 실제 bid·ask 기반 BASE·STRESS PAPER 체결을 그대로 사용한다.
- Registry는 A~J 10전략, BASE·STRESS 20개 독립계좌다. A/B만 ACTIVE이고 C~J는 SHADOW이며 10개 모두 LONG·SHORT 기본 켜짐이다.
- 실제 주문, private API, 인증, API Key, secret과 wallet 경로는 계속 0 또는 false다.

### 저장 공개시장·실제 서비스 검증

| 검증 | 상태 | 이번 실행의 실제 결과 |
|---|---|---|
| J 피처·신호·보호 | PASS | top10 기울기의 유한성·결정성과 bid·ask 차이, LONG·SHORT 대칭, 과거표본·percentile·기울기비·지속·비용 거절을 검증했다. A~J 양방향의 비용후 계획과 TP1·TP2·초기 SL 종단 시나리오도 통과했다. |
| 저장 공개시장 replay 결정성 | PASS | `run-f14214b3b1dd`의 실제 공개시장 15,045 events를 두 번 replay했다. 두 실행 모두 checksum `5880f66a673ad64d01dec42853d59e3208497fc6ab6ba6520737b7553bccc94b`, 평가 69,380·적격 9·고유후보 8·main 0·shadow 9로 일치했고 실제주문·인증은 false였다. |
| 실제 시작·정지·재개 | PASS | 실제 앱 내 브라우저에서 `자동 관찰 시작`을 한 번 눌러 RUNNING을 확인했다. 일시정지 뒤 `관찰 중 · 내가 일시정지`, 시장관찰 계속·새 진입 중단을 확인하고 `새 진입 다시 시작`으로 RUNNING 복귀를 확인했다. |
| 실제 전략 화면 | PASS | 새로고침한 실제 8870에서 전략 10행·모드 10개·방향 20개·`10/10 전략 켜짐 · 실제 주문 0`을 확인했다. J 상세를 열어 SHADOW, BASE·STRESS와 현재버전 표본 0을 확인했다. |
| 181.9초 실제 RUNNING | PASS | event 24,608→49,651로 25,043건 증가했다. 실행경로 P95는 119.124~349.242ms, 최종 158.313ms였다. 전 표본에서 RUNNING·entry lock false였고 critical 누계 증가·비계획 reconnect·drop·gap·persistence fault·last error는 0이었다. |
| 후속 순간 지연 감사 | PASS_WITH_OBSERVED_TRANSIENT | 이후 원장 조회·화면 점검 구간에서 1,500.355~1,506.127ms인 TRADE 4건이 22ms 동안 관찰됐다. rolling P95는 483.705ms로 임계값 아래였고 critical active·entry lock은 자동 해제, queue 0·reconnect/drop/gap/fault/error 0이었다. 이를 장시간 무지연 증거로 쓰지 않는다. |
| 전체검증 부하 뒤 복구 | PASS_WITH_OBSERVED_TRANSIENT | backend·frontend·production build를 동시에 다시 실행하는 동안 1,506.606~1,544.847ms TRADE 10건이 592ms에 추가됐다. 부하 뒤 2초 간격 8표본은 모두 RUNNING·critical active false·entry lock false였고 event +2,555, P95 1,005.884→205.497ms, queue 최대 2, reconnect/drop/gap/fault/error 0이었다. 짧은 경계초과는 숨기지 않되 자동복구 결과와 분리한다. |
| 현재버전 자연 PAPER 거래 | PASS | main 2건은 17.776~28.766초, 독립 shadow 14건은 13.762~45.904초 보유했다. 1~2초 종료는 0건이다. main 순손익 -0.219965 USDT, shadow 합계 -13.018762 USDT이며 모두 `LIVE_PUBLIC`이다. 작동 증거일 뿐 수익성 증거가 아니다. |
| 전략별 후보 독립성 | PASS | 같은 SUIUSDT LONG 가격·시간에 잡힌 LSA와 VWAP 표본은 `candidate-6ba7eaa9ec884ad6`, `candidate-1df054200161458c`라는 서로 다른 불변 후보와 전략 ID를 가졌다. 동일 시장체결을 공유통계 중복으로 잘못 기록한 것이 아니다. |
| J 자연신호 | NOT_OBSERVED | 최신 snapshot에서 J 40경로를 평가했지만 적격 0, BASE·STRESS 완료표본 0이었다. 신호를 만들기 위해 기준을 낮추지 않았다. |
| 프로세스·원장 | PASS | LaunchAgent는 running이고 최신 프로세스 이후 traceback·error·exception·critical 로그 일치 0, SQLite `PRAGMA quick_check=ok`다. 기존 불변 Run·거래 삭제·수정 0이다. |

### 자동검증과 한계

| 검증 | 상태 | 실제 결과 |
|---|---|---|
| targeted backend | PASS | 88 passed |
| 전체 backend pytest | PASS | 294 passed, 8.77초 |
| frontend Vitest | PASS | 12 files, 41 passed |
| Playwright | PASS | 실제 Chromium desktop·tablet·mobile 3 passed, 17.4초 |
| Ruff / mypy | PASS | 오류 0 / 82 source files 오류 0 |
| ESLint / TypeScript | PASS | 오류 0 / 오류 0 |
| production build | PASS | Vite 48 modules, PAPER build safety PASS |
| security / repository hygiene | PASS | 115 source, violation·secret-like·real-order path 0 / 위반 0 |
| 전략 수익성 | NOT_PROVEN | 현재버전 계좌별 표본은 0~3건이고 모두 `표본 부족`이다. 관찰된 main·shadow 합계도 순손실이므로 승률이나 수익을 보장하지 않는다. |
| J 자연 공개시장 적격·체결 | NOT_OBSERVED | 저장 replay와 실제 짧은 LIVE 관찰에서 자연 적격·완료거래가 없었다. 기준은 유지했다. |
| 6시간 / 24시간 soak | NOT_RUN | 181.9초 실제 서비스 표본을 멀티시간 수용결과로 표현하지 않는다. |
| Release ZIP | NOT_RUN | 이번 Wave에서 만들지 않았다. |
| GitHub main / Actions | PASS | 구현 commit `a8a04b8c4aedfd092a13ce199d9925f2cce5505a`을 main에 push했다. [Actions 32791918431](https://github.com/robom-labs/flowscalper/actions/runs/32791918431)의 validate 48초, browser 1분4초, 실제 Chromium desktop·tablet·mobile E2E와 browser evidence upload가 모두 PASS했다. |

기계판독 증거는 `evidence/WAVE23_BOOK_SLOPE_STRATEGY_QA.json`이다. 필수 로컬 회귀·화면·서비스 검증의 FAIL과 BLOCKED는 현재 0이다. 전략 수익성, J 자연신호, 6시간·24시간과 Release ZIP은 각각 `NOT_PROVEN`, `NOT_OBSERVED` 또는 `NOT_RUN`으로 분리했다.

Wave 23 구현 commit은 `a8a04b8c4aedfd092a13ce199d9925f2cce5505a`이다. 같은 SHA의 GitHub Actions에서 로컬과 독립된 설치·저장소 위생·lint·typecheck·backend/frontend test·production build와 실제 Chromium desktop·tablet·mobile E2E가 모두 통과했다.

## 29. 장시간 런타임 정지 제거와 성과 범위 명확화

2026-08-25 실제 공개시장 PAPER를 장시간 실행하며 간헐적 1.5초 이상 지연을 저장 flush, 메모리 일괄 폐기, 외부 공개 스트림 지연으로 분해했다. 첫 `run-e2cd64bac738`에서는 1,579ms Parquet flush와 20개 TRADE의 1,597.6~1,629.5ms 지연이 겹쳤다. 저장만 process로 옮긴 `run-64d8e843f38f`에서는 52,501번째 이벤트에서 과거 2,500개 객체를 한꺼번에 삭제하던 경계와 17개 TRADE의 1,502.1~1,577.2ms 지연이 정확히 겹쳤다. 두 진단 런은 해결 증거가 아니라 `FAIL_DIAGNOSTIC_FIXED`로 보존했다.

### 구현한 수정

- 같은 snapshot에서 A~J가 동일 방향·청산형식의 entry·TP·SL·비용 입력을 최대 32번 만들던 계산을 `(Side, ExitStyle)`별 최대 4번으로 재사용한다. cache는 한 snapshot 안에서만 살아 오래된 호가를 공유하지 않는다.
- 장시간 worker의 JSON·row checksum·Arrow·zstd·fsync를 AnyIO 별도 process로 옮기고 SQLite 불변 manifest만 thread에서 반영한다. process 실패 시 뽑은 batch를 먼저 복원하고 신규 PAPER 진입을 fail-closed한다.
- 이미 계산한 batch checksum을 Parquet 파일 digest로 재사용해 같은 row의 두 번째 전체 JSON 직렬화를 없앴다.
- 최근 이벤트 10,000개와 계획거부 2,000개를 각각 고정길이 `deque`로 바꿔 2,500개·500개 prefix 일괄 삭제를 없앴다.
- 성과 화면은 요약·현재자산이 `이번 Run`, 거래·승률·기대값·PF·비용·낙폭이 `현재 전략 버전` 범위임을 문장과 열 제목에 직접 표시한다.
- 실제 주문, private API, 인증, API Key, secret과 wallet 경로는 계속 0 또는 false다.

### 실제 공개시장·브라우저 검증

| 검증 | 상태 | 이번 실행의 실제 결과 |
|---|---|---|
| 최종 16분 PAPER 런 | PASS_WITH_OBSERVED_TRANSIENT | `run-b85a51c5daed` 966초·49표본에서 event 2,150→160,850으로 158,700건 증가했고 메모리 이벤트는 warmup 뒤 10,000에 고정됐다. process flush 최대 5,591ms, 계획회전 1·전체 reconnect 1·비계획 reconnect 0, drop·gap·persistence fault·last error 0, 최종 실행경로 P95 343.373ms였다. |
| 52,501 일괄 폐기 경계 | PASS | 최종 런은 52,691, 56,433, 59,826 events 표본을 critical 증가 없이 통과했다. 별도 process flush가 5.59초 걸린 구간에도 실행경로 표본 P95는 930.982~959.178ms였고 임계치 아래였다. |
| 외부 순간지연 안전장치 | PASS_WITH_OBSERVED_TRANSIENT | 최종 런 누적 TRADE 임계지연 275건을 숨기지 않는다. critical active와 entry lock이 각각 2개 표본에서 함께 true였고 회복 뒤 둘 다 false였다. 비계획 reconnect·drop·gap·fault는 0이므로 지연 0을 주장하지 않고 늦은 데이터로 진입하지 않는 fail-closed와 자동회복을 PASS로 판정했다. |
| 기본 15분 회전 | PASS | event 149,567→151,920 사이 계획회전 1·전체 reconnect 1이 됐고 비계획 reconnect 0, 이후 event 160,850까지 계속 증가했다. 회전 뒤 P95는 706.665→126.813ms로 회복됐다. |
| 실제 시작·일시정지·재개 | PASS | 실제 앱 내 브라우저에서 시작 한 번으로 `작동 중`, 일시정지로 `관찰 중 · 내가 일시정지`, 재개로 다시 `작동 중`을 확인했다. 시장관찰과 신규 PAPER 진입 상태가 구분됐다. |
| 실제 전략·기록·성과 | PASS | 전략 10/10, 모드 10개, LONG·SHORT 20개, 실제 주문 0을 확인했다. 이번 Run 기록 0과 과거 보존을 구분했고, 수정 빌드의 성과 화면에서 `이번 Run 현재자산`과 `현재버전 거래·승률` 열을 실제로 확인했다. |
| 자연 main PAPER 경로 | PASS | `run-6c57522494e8` BNBUSDT SHORT는 사전 확정 entry 711.520·SL 717.2172·TP 693.30996·수량 0.14로 진입해 22.608초 뒤 EDGE_DECAY 종료됐다. 최종 빌드 `run-e6fe0a69a138` ENAUSDT SHORT는 entry 0.1547600·SL 0.1560031200·TP 0.15080301600·수량 680으로 진입해 17.670초 뒤 종료됐다. 1~2초 종료는 0이며 두 손실 표본은 작동 증거일 뿐 수익성 증거가 아니다. |
| 저장 공개시장 replay | PASS | `run-f14214b3b1dd` 15,045 events를 `replay-436ffc42d86846bd`, `replay-b1eb7ad227964a8c`로 재생했다. 두 실행은 checksum `5880f66a673ad64d01dec42853d59e3208497fc6ab6ba6520737b7553bccc94b`, 평가 69,380·적격 9·후보 8·main 0·shadow 9가 같고 실제주문·인증은 false였다. |
| SQLite·저장 manifest | PASS | 활성 원장 `PRAGMA quick_check=ok`, 외래키 위반 0, Run 59·main 50·shadow 434·replay 47·archive manifest 24,711을 읽었다. replay가 source batch·row checksum을 검증했다. |
| 최종 서비스 상태 | PASS | 빌드 재시작 뒤 `run-e6fe0a69a138`은 약 15분 45초에 event 139,208·메모리 10,000·P95 218.127ms, 계획회전/전체 reconnect 1/1, 비계획 reconnect·drop·gap·fault 0, critical active·entry lock false, 실제주문·인증 false였다. |

### 자동검증과 한계

| 검증 | 상태 | 실제 결과 |
|---|---|---|
| backend pytest | PASS | 299 passed, 17.72초 |
| frontend Vitest | PASS | 12 files, 41 passed |
| Playwright | PASS | 실제 Chromium desktop·tablet·mobile 3 passed, 12.5초. 차트 크기·지표·전체화면·전략·기록·replay·반응형 overflow와 console/page error 0을 검사했다. |
| Ruff / mypy | PASS | 오류 0 / 82 source files 오류 0 |
| ESLint / TypeScript | PASS | 오류 0 / 오류 0 |
| production build | PASS | Vite 48 modules, PAPER build safety PASS |
| security / repository hygiene | PASS | 115 source, violation·secret-like·real-order path 0 / 위반 0 |
| 전략 수익성 | NOT_PROVEN | 관찰된 자연 main 표본은 순손실이고 전략별 표본이 부족하다. 독립계좌 합계를 하나의 공동 1,000 USDT 수익으로 해석하지 않는다. |
| 6시간 / 24시간 soak | NOT_RUN | 966초와 후속 서비스 표본을 멀티시간 수용결과로 표현하지 않는다. |
| Release ZIP | NOT_RUN | 이번 Wave에서 만들지 않았다. |
| GitHub main / Actions | PASS | 계산 재사용 commit `3284fc1`과 구현 commit `887b0ec1aed6a9930a5d1cf8bfa2562af22f6bee`를 main에 push했다. [Actions 32798366401](https://github.com/robom-labs/flowscalper/actions/runs/32798366401)의 validate 56초, browser 1분15초, 실제 Chromium E2E와 browser evidence upload가 모두 PASS했다. |

기계판독 증거는 `evidence/WAVE24_RUNTIME_STALL_QA.json`이다. 필수 로컬 회귀·화면·서비스 검증의 미해결 FAIL과 BLOCKED는 0이다. 외부 공개시장 지연은 관찰 사실로 남기고, 내부 정지 제거와 fail-closed 자동회복만 완료로 판정했다.

## 30. 활성 원장 제한·실행지연 분리·현재 PAPER와 전략 감시 가시성

2026-08-25 활성 SQLite가 약 2.0GiB까지 증가한 원인을 캔들 파생구간 중복, 상태 비변경 감사의 전체 복구 snapshot 복제와 모든 전략계정 이력 반복으로 분해했다. 동시에 10개 전략·20계좌 성과를 0.5초 화면 frame마다 재계산하고 deep 20개를 처리하던 부하와, 실행 bid·ask 주문장·공개 체결·50종목 wide scanner 지연을 한 숫자로 섞던 관측 문제를 수정했다.

### 구현과 안전경계

- 활성 SQLite와 공개시장 archive 볼륨을 독립 검사하고 어느 하나라도 임계 미달이면 새 PAPER 진입을 fail-closed한다.
- 모든 실행 감사는 append-only로 유지하되 상태 비변경 거절은 전체 복구 snapshot을 다시 쓰지 않는다. 상태가 바뀔 때만 전체 checksum snapshot과 영향받은 전략계정 이력을 기록한다.
- 모든 차트 시간구간은 메모리에서 유지하지만 SQLite에는 원본 1초봉과 거래 중심 replay의 180초봉만 저장한다.
- wide 50종목을 유지하면서 deep 정밀분석은 12종목으로 제한하고, 대시보드는 최근 512건만 투영한다. 현재버전 성과는 완료 독립 PAPER 거래가 바뀔 때만 재계산한다.
- Parquet worker process를 서비스 시작 때 미리 기동하고 SQLite 실행원장 반영은 event loop 밖 thread에서 수행한다.
- 실제 bid·ask 실행호가 p95, 공개체결 p95와 wide scanner p95를 분리한다. 500ms보다 늦은 aggregate trade는 archive에 보존하되 candle·FeatureEngine·전략평가에 넣지 않고 신선한 체결까지 해당 종목을 `data_healthy=false`로 둔다.
- 차트에는 열린 PAPER의 방향·전략·BASE/STRESS·entry·TP1·SL과 같은 종목의 추가 진행 건수를 표시한다. 시장 화면에는 전체 진행 목록을 제공한다.
- 전략 화면은 A~J 각각의 정상 감시·준비·진입·안전대기·확인필요·꺼짐, 최근 대기 이유와 평가경로 수를 표시한다.
- 전략 신호·비용·TP·SL·위험 임계값은 낮추지 않았다. 실제 주문, private API, 인증, API Key, secret과 wallet 경로는 계속 0 또는 false다.

### 실제 공개시장·브라우저·원장 검증

| 검증 | 상태 | 이번 실행의 실제 결과 |
|---|---|---|
| 독립 공개 depth 표본 | PASS | BTCUSDT·SOLUSDT·BNBUSDT의 8초 공개 WebSocket 표본에서 p50 20.3ms, p95 21.63ms, 최대 24.83ms, 1,500ms 초과 0건이었다. 짧은 네트워크 진단이며 soak가 아니다. |
| 실제 시작 한 번 | PASS | 실제 앱 내 브라우저에서 `자동 관찰 시작`을 한 번 눌렀다. 약 250ms 뒤 `연결 중·요청받음`, 약 5.5초 뒤 `작동 중`을 확인했다. |
| 실제 chart와 진행 목록 | PASS | `run-b39e9a83991b`에서 ENAUSDT·XRPUSDT·DOGEUSDT의 BASE·STRESS 6개 열린 PAPER가 목록에 표시됐고, 선택한 ENAUSDT 차트에서 하락·전략·BASE·entry·TP1·SL banner를 확인했다. 자연 종료 뒤 `진행 중 0건`과 오래된 banner 제거를 확인했다. |
| 다른 전략 작동상태 | PASS | A~J 10개 모두 LONG·SHORT 켜짐, 각각 12종목×양방향 24개 경로를 평가했다. 계정 fault는 0이었다. 진입하지 않은 전략은 시장 방향·체결 흐름·호가·가격 구조 등 최근 엄격조건 대기 이유를 표시했다. Queue Microprice와 Depth-adjusted OFI가 관찰 구간에 자연 적격·PAPER 진입했다. |
| 181초 실제 RUNNING | PASS | 동일 Run 13표본에서 event 44,383→63,439로 19,056건 증가했다. 실행호가 p95 32.924~278.431ms, 체결 p95 33.992~190.332ms, wide scanner p95 1,540.585~1,829.765ms, queue 최대 1이었다. critical active·entry lock·비계획 reconnect·gap·drop·persistence fault 표본은 모두 0이었다. stale trade 종목은 순간 최대 2였고 다음 신선한 체결 뒤 0으로 회복했다. |
| 현재 Run 자연 shadow PAPER | PASS_WITH_LOSS | Queue Microprice BASE 9건·STRESS 5건과 Depth-adjusted OFI BASE 1건·STRESS 1건, 총 16건이 완료됐다. 보유 11.652~65.464초, 3초 미만 0건이었다. BASE 독립계좌별 합산 순손익 -2.978295·수수료 2.681336·슬리피지 0.281199 USDT, STRESS 독립계좌별 합산 순손익 -1.242385·수수료 2.071645·슬리피지 0.293730 USDT다. 서로 다른 독립계좌를 한 1,000 USDT 계좌로 해석하지 않으며 작동 증거이지 수익성 증거가 아니다. |
| 실제 시스템 화면 | PASS | `50 / 12종목`, 실제 호가·체결 지연, KST·서버·거래소 보정, 비정상 재연결·누락 0, 저장소 정상, 실제 주문 경로 0을 확인했다. wide scanner는 고급진단에서 진입판정이 아님을 구분한다. |
| 활성 원장과 보존 | PASS | 서비스는 `/Volumes/ROBOM_FLOWSCALPER/05_RUNTIME/ROBOM_FlowScalper/active-ledger/run-ledger.sqlite3`를 사용한다. 이전 닫힌 원장은 `/Volumes/ROBOM_FLOWSCALPER/04_MIGRATION_ARCHIVE/internal-active-ledger-before-wave25-20260825T1110KST/`에 보존했다. SQLite `quick_check=ok`, 외래키 위반 0이며 archive·ledger 여유공간 약 22,150MB, storage entry allowed true였다. |
| 최종 사이트 재시작 | PASS | 원장검사 뒤 LaunchAgent를 다시 올려 READY를 확인하고 실제 브라우저에서 시작을 한 번 눌렀다. 최종 `run-8cd493f93260`은 LIVE, event 5,556, 실행호가/체결 p95 28.882/31.560ms, queue·critical·lock·비계획 reconnect·gap·drop·fault 0이었다. 실제 화면과 API에서 Queue Microprice·Depth-adjusted OFI의 SOLUSDT·BTCUSDT BASE/STRESS 4개 열린 PAPER와 진입 즉시 TP1·TP2·SL 보호를 확인했고 browser console 항목은 0이었다. |

### 자동검증과 한계

| 검증 | 상태 | 실제 결과 |
|---|---|---|
| backend pytest | PASS | 312 passed, 20.86초 |
| frontend Vitest | PASS | 12 files, 44 passed |
| Playwright | PASS | 실제 Chromium desktop·tablet·mobile 3 passed, 20.4초. 차트 현재 PAPER, 전체 진행 목록, BASE·STRESS 계좌를 모두 반영한 전략 감시상태·평가경로와 반응형 overflow를 검사했다. |
| Ruff / mypy | PASS | 오류 0 / 82 source files 오류 0 |
| ESLint / TypeScript | PASS | 오류 0 / 오류 0 |
| production build | PASS | Vite 48 modules, PAPER build safety PASS |
| security / repository hygiene | PASS | 115 source, violation·secret-like·real-order path 0 / 위반 0 |
| 실제 브라우저 console | PASS | 실제 화면 warning·error 0 |
| 전략 수익성 | NOT_PROVEN | 현재 Run 자연 Queue 표본은 순손실이고 다른 전략도 현재버전 표본이 충분하지 않은 행이 있다. 조용한 정상 대기를 성과나 오류로 해석하지 않는다. |
| 6시간 / 24시간 soak | NOT_RUN | 181초와 후속 실제 서비스 표본을 멀티시간 수용결과로 표현하지 않는다. |
| Release ZIP | NOT_RUN | 이번 Wave에서 만들지 않았다. |
| GitHub main / Actions | PASS | 구현 commit `7d4175d53256bbc9735b2e0bc875ef2d7b5ee87e`을 main에 push했다. [Actions 32809307309](https://github.com/robom-labs/flowscalper/actions/runs/32809307309)의 validate 1분0초, browser 1분5초, 실제 Chromium desktop·tablet·mobile E2E와 browser evidence upload가 모두 PASS했다. |

기계판독 증거는 `evidence/WAVE25_STORAGE_RUNTIME_UI_QA.json`, 상세 결정은 ADR-024·ADR-025·ADR-026이다. 필수 로컬 회귀·실제 화면·짧은 실제 서비스 검증의 미해결 FAIL과 BLOCKED는 현재 0이다. 전략 수익성, 6시간·24시간과 Release ZIP은 `NOT_PROVEN` 또는 `NOT_RUN`으로 분리했다.

Wave 25 구현 commit은 `7d4175d53256bbc9735b2e0bc875ef2d7b5ee87e`이다. 같은 SHA의 GitHub Actions에서 로컬과 독립된 설치·저장소 위생·lint·typecheck·backend/frontend test·production build와 실제 Chromium desktop·tablet·mobile E2E가 모두 통과했다.

## 31. 런타임 지연 사건 진단·종료 차트 정리·A~J 실동작 재검증

2026-08-25 실제 LIVE PAPER에서 순간 지연이 회복된 뒤 정확한 발생시각과 지속시간을 남길 수 없던 관측 공백을 수정했다. 같은 Run에서 포지션이 자연 종료된 뒤 기존 `새 PAPER 진입` 알림이 현재 거래처럼 남을 수 있는 화면 모순도 종료 안내와 자동 정리로 교체했다. 전략 임계값·비용·TP·SL·위험과 실제 주문 0 경계는 바꾸지 않았다.

### 실제 차트와 전략 감시

| 검증 | 상태 | 이번 실행의 실제 결과 |
|---|---|---|
| 실제 시작과 Run | PASS | 새 빌드 재시작 뒤 앱 내 브라우저에서 `자동 관찰 시작`을 한 번 눌렀다. `run-4c905f26da0d`가 LIVE·RUNNING으로 진행됐고 공동계좌는 1,000 USDT, 거래·손익·비용 0을 유지했다. |
| 현재 PAPER 차트 | PASS | 자연 발생한 BTCUSDT 깊이보정 OFI LONG BASE/STRESS와 ENAUSDT Queue SHORT BASE/STRESS를 진행 목록에서 확인했다. 선택한 BTCUSDT 차트에는 상승·전략·BASE와 entry 80,265, TP1 80,638.31, TP2 81,011.61, SL 80,024.16 보호선이 즉시 표시됐다. |
| 종료 표시 정리 | PASS | 자연 종료 뒤 `focus_positions`가 0이 되자 현재 PAPER banner·선택 포지션은 사라졌다. 화면의 `새 PAPER 진입`은 거래 알림이 아니라 새 진입 기능의 작동 상태였고, 특정 종목의 과거 진입 알림은 남지 않았다. 같은 Run 종료 전이를 `PAPER 거래 종료 … 기록에서 확인`으로 바꾸고 15초 뒤 정리하는 회귀 테스트를 추가했다. |
| A~J 전체 상태 | PASS | A/B ACTIVE, C~J SHADOW, A~J LONG·SHORT 켜짐, 10개 각각 12종목×양방향 24경로를 평가했고 계정 fault는 0이었다. 진입하지 않은 전략은 `REJECTED`와 시장방향·체결흐름·호가·가격구조·지속성의 최근 대기 이유를 표시했다. 조용한 상태는 고장이 아니라 엄격조건 정상 대기였다. |
| 자연 shadow 원장 | PASS_WITH_LOSS | Queue BASE/STRESS 각 3건, Depth-adjusted OFI BASE/STRESS 각 1건으로 총 8건이 완료됐다. 보유 6.122~37.832초, 3초 미만 0건, 10초 미만 EDGE_DECAY 0건이다. 6.122초 2건은 초기 SL 도달 STOP이고 EDGE_DECAY 6건은 13.682~37.832초였다. 작동 증거이지 수익성 증거가 아니다. |

### 런타임 진단과 저장 리플레이

| 검증 | 상태 | 이번 실행의 실제 결과 |
|---|---|---|
| 사건 관측값 | PASS | 임계 실행호가 지연의 사건 수·시작·복구·최근/최장 지속시간, 최근/최대 이벤트 수신 공백·500ms 초과 횟수, 저장 flush 최근완료·최대·2초 이상 발생시각을 시스템 고급진단에 추가했다. 기존 1,500ms fail-closed 기준은 그대로다. |
| 90초 LIVE 표본 | PASS | event 4,791→13,850, 실행호가 p95 31.6~37.6ms, 공개체결 p95 25~33ms, queue 0, 진입잠금 false, critical 사건·fault 0이었다. 포지션은 0→2→4→2로 자연 변화했다. |
| replay 병행 LIVE | PASS | 저장 `run-f14214b3b1dd`의 15,045 이벤트를 별도 저우선순위 process로 replay하는 동안 대시보드 API는 16ms, 완료 뒤 실행호가/체결 p95 43.143/46.453ms, queue 0, critical·진입잠금·비계획 reconnect·sequence gap·drop·persistence fault 0이었다. 수신 공백 최대 610.408ms 14회와 저장 flush 최대 3,502ms 2회는 진단값으로 남겼으며 실행경로 임계사건과 동반되지 않았다. |
| replay 결정성 | PASS | checksum `5880f66a673ad64d01dec42853d59e3208497fc6ab6ba6520737b7553bccc94b`, 69,380 전략평가, 9 적격, 8 후보계획, main 0, shadow 9가 같은 A~J 구현 revision의 이전 반복 결과들과 일치했다. 서로 다른 과거 전략 revision의 checksum과는 비교하지 않았다. |

### 신규 전략 후보 결정

공식 지정가호가장 복원력과 깊이정규화 OFI 연구에서 유동성 재충전 실패 추세 후보 K를 도출했다. 12개 저장 `LIVE_PUBLIC` Run에서 현재 snapshot 이전 정보만 사용하고 750ms 지속, 실제 ask·bid 진입/종료와 15초 horizon을 적용했다. train 88개는 총수익 평균 -0.196bp, BASE 비용후 -13.196bp였고 최신 holdout 25개도 총수익 평균 1.46bp, BASE 비용후 -11.54bp였다. 양쪽에서 13bp 비용을 넘은 후보는 각각 2개뿐이었다. 기준을 낮추지 않고 `REJECTED_NOT_ADDED`로 기록했다.

### 자동검증과 한계

| 검증 | 상태 | 실제 결과 |
|---|---|---|
| backend pytest | PASS | 313 passed |
| frontend Vitest | PASS | 12 files, 45 passed |
| Playwright | PASS | 실제 Chromium desktop·tablet·mobile 3 passed |
| Ruff / mypy | PASS | 오류 0 / 82 source files 오류 0 |
| ESLint / TypeScript | PASS | 오류 0 / 오류 0 |
| production build | PASS | Vite 48 modules, PAPER build safety PASS |
| security / repository hygiene | PASS | 115 source, violation·secret-like·real-order path 0 / 위반 0 |
| 전략 수익성 | NOT_PROVEN | 현재버전 표본은 전략별로 0~76건이며 비용후 손실 전략이 있다. 30건 미만은 순위를 매기지 않고, 표본이 있는 전략도 수익성이 입증됐다고 표현하지 않는다. |
| 6시간 / 24시간 soak | NOT_RUN | 90초와 replay 병행 표본을 멀티시간 수용결과로 표현하지 않는다. |
| Release ZIP | NOT_RUN | 이번 Wave에서 만들지 않았다. |
| GitHub main / Actions | PASS | 구현 commit `9842c330d54e0b545735776e332e413c26a0e192`을 main에 push했다. [Actions 32811910384](https://github.com/robom-labs/flowscalper/actions/runs/32811910384)의 validate 1분8초, browser 1분11초와 Chromium desktop·tablet·mobile E2E·브라우저 증거 업로드가 모두 PASS했다. |

기계판독 증거는 `evidence/WAVE26_INCIDENT_STRATEGY_UI_QA.json`, 상세 결정은 ADR-027이다. 필수 로컬 회귀·실제 화면·짧은 실제 서비스·저장 replay 검증의 미해결 FAIL과 BLOCKED는 현재 0이다. 전략 수익성, 6시간·24시간과 Release ZIP은 `NOT_PROVEN` 또는 `NOT_RUN`으로 분리했다.

Wave 26 구현 commit은 `9842c330d54e0b545735776e332e413c26a0e192`이다. 같은 SHA의 GitHub Actions에서 로컬과 독립된 설치·저장소 위생·lint·typecheck·backend/frontend test·production build와 실제 Chromium desktop·tablet·mobile E2E·증거 업로드가 모두 통과했다.

## 32. READY 즉시 응답·저장 단계 진단·공동/독립 PAPER 구분

2026-08-25 약 2GiB 활성 SQLite를 사용하는 실제 macOS 서비스의 시작 지연을 단계별로 측정했다. 안전 복구가 아니라 READY 생성 중 동기 실행하던 과거 main·shadow 거래통계 조회가 차가운 파일 캐시에서 약 9.4~14.6초를 차지했다. PAPER 복구 checksum 검증은 그대로 두고 화면용 과거 통계만 백그라운드 query-only 연결로 분리했다.

### 구현과 안전경계

- 저장소 준비, SQLite open, 복구조회, 런타임·PAPER 계좌, 과거 거래통계와 전체 부팅시간을 각각 진단한다.
- READY의 과거 거래통계는 lifespan background thread에서 준비하고 준비완료·로딩·최근 소요·완료시각을 표시한다.
- main·shadow 과거 조회는 SQLite WAL의 query-only 연결을 사용해 새 Run과 PAPER 저장의 writer lock을 막지 않는다.
- 최장 저장 flush의 Parquet·manifest·candle 소요와 이벤트·candle·batch 수, 최대 이벤트 수신 공백의 발생시각을 기록한다.
- 같은 종목·전략·BASE에 공동 PAPER와 전략 독립 PAPER가 동시에 있으면 목록·선택기·제목·차트 banner·계획 rail에 `공동계좌` 또는 `전략 독립계좌`를 표시한다.
- 전략 임계값·비용·TP·SL·위험예산은 바꾸지 않았다. 실제 주문, private API, 인증, API Key, secret과 wallet 경로는 계속 0 또는 false다.

### 실제 재시작·브라우저·LIVE 검증

| 검증 | 상태 | 이번 실행의 실제 결과 |
|---|---|---|
| 변경 전 병목 분해 | PASS_DIAGNOSTIC | 첫 차가운 표본은 외부 응답 23.159초, 내부 ledger 533ms·복구조회 2,427ms·런타임 14,630ms·전체 17,590ms였다. 더 깊은 표본은 외부 12.506초, 런타임 9,382ms 중 과거 거래통계가 9,381ms였다. 즉시 warm 재시작은 외부 1.905초·내부 43.9ms였다. |
| 변경 후 READY | PASS | 첫 배포 재시작은 내부 전체 0.212초, 런타임 0.648ms, 동기 통계 0ms였고 첫 HTTP 응답까지 3.86초였다. 화면을 막지 않은 과거 통계는 1.624초에 완료됐다. 다른 저장상태의 재시작은 내부 안전복구 포함 1.909초, 외부 첫 응답 9.21초였으며 통계는 계속 background였다. LaunchAgent 자체 기동시간과 저장장치 변동을 내부 런타임 수치로 오해하지 않는다. |
| 재시작 직후 실제 시작 | PASS | 앱 내 브라우저를 새로 열고 150ms 뒤 `자동 관찰 시작`을 한 번 눌렀다. 250ms 뒤 `연결 중·요청을 받았습니다`, 8초 뒤 `작동 중·계속 작동`, 지연 22ms를 확인했다. 최신 `run-d1cbbe3d2458`은 이후에도 LIVE로 유지됐다. |
| 현재 PAPER 차트 | PASS | 자연 XRPUSDT LSA LONG의 공동 BASE와 독립 BASE/STRESS, BTCUSDT Depth-adjusted OFI STRESS, DOGEUSDT Queue STRESS가 동시에 표시됐다. 선택된 공동 BASE 차트는 `PAPER 진입 중·상승`, entry 1.5207, TP1 1.5356, SL 1.5085를 보였다. 공동 BASE와 독립 BASE의 기존 동일 문구를 발견해 `공동계좌`·`전략 독립계좌`로 수정하고 단위·브라우저 회귀로 고정했다. |
| 전략 A~J 정상대기 | PASS | A/B ACTIVE, C~J SHADOW, 10개 모두 LONG·SHORT 켜짐, 각각 12종목×양방향 24경로를 평가했다. 화면은 10개 정상 감시·문제 0·실제 주문 0과 시장방향·체결흐름·호가·가격구조·지속성 대기 이유를 표시했다. 진입하지 않은 전략의 조건을 낮추지 않았다. |
| 첫 실제 LIVE 저장 표본 | PASS_WITH_SLOW_IO | `run-8805db58dce8`의 50초 연속 표본에서 event 5,136→10,000, 지연 P95 약 30~38ms, 비계획 reconnect·sequence gap·critical active·persistence fault 0이었다. 후속 최장 flush 6,682ms는 Parquet 1,665ms·manifest 1,642ms·candle 3,365ms였고 최대 수신 공백 1,046.676ms는 다른 시각에 발생했다. 저장 I/O는 느렸지만 실행 이벤트 루프가 6.7초 정지했다는 증거는 아니다. |
| 최종 LIVE 상태 | PASS | 최종 빌드의 `run-d1cbbe3d2458`은 event 10,000 메모리 상한을 유지하고 지연 P95 약 35~43ms, 최대 수신 공백 586.764ms, 비계획 reconnect·sequence gap·drop·persistence fault 0이었다. 최신 Run main XRPUSDT 거래 1건은 38.846초 EDGE_DECAY, 순손익 -0.157406472 USDT였다. shadow 10건은 13.554~95.308초이고 1~2초 종료는 0이었다. 손실·짧은 표본은 작동 증거이지 수익성 증거가 아니다. |

### 자동검증과 한계

| 검증 | 상태 | 실제 결과 |
|---|---|---|
| backend pytest | PASS | 315 passed, 13.43초 |
| frontend Vitest | PASS | 12 files, 47 passed |
| Playwright | PASS | 실제 Chromium desktop·tablet·mobile 3 passed, 17.2초. 재생 계좌범위, 차트·전략·기록·분석·반응형과 console/page error 0을 검사했다. |
| Ruff / mypy | PASS | 오류 0 / backend/app 82 source files 오류 0 |
| ESLint / TypeScript | PASS | 오류 0 / 오류 0 |
| production build | PASS | Vite 48 modules, PAPER build safety PASS |
| security / repository hygiene | PASS | 115 source, violation·secret-like·real-order path 0 / 위반 0 |
| 추가 광범위 mypy backend/tests | FAIL_NON_GATE | 프로젝트 mypy 계약 밖의 테스트 보조코드까지 임의로 확대한 검사에서 기존 타입주석·Protocol 문제 184개를 확인했다. backend/app 정식 mypy는 PASS이고 pytest 315개도 PASS다. 이 추가 실패를 통과한 것으로 쓰지 않는다. |
| 전략 수익성 | NOT_PROVEN | LSA 현재버전 BASE는 11건, STRESS 10건이며 CBR과 일부 신규 전략은 0건이다. 30건 미만은 순위를 매기지 않고, Queue처럼 더 큰 표본도 전체 비용후 검증 없이는 수익성이 입증됐다고 표현하지 않는다. |
| 6시간 / 24시간 soak | NOT_RUN | 이번 분 단위 실제 서비스 표본을 멀티시간 수용결과로 표현하지 않는다. |
| Release ZIP | NOT_RUN | 이번 Wave에서 만들지 않았다. |
| GitHub main / Actions | PASS | 구현 commit `354053df30128f2a7ae7bfbc7200e538a516b82e`을 main에 push했다. [Actions 32814598091](https://github.com/robom-labs/flowscalper/actions/runs/32814598091)의 validate 1분13초, browser 1분13초, 실제 Chromium desktop·tablet·mobile E2E와 browser evidence upload가 모두 PASS했다. |

기계판독 증거는 `evidence/WAVE27_STARTUP_STORAGE_ACCOUNT_QA.json`, 상세 결정은 ADR-028이다. 로컬 필수 회귀·실제 화면·짧은 실제 서비스와 구현 commit의 GitHub Actions 검증에서 미해결 FAIL과 BLOCKED는 현재 0이다. 추가 비게이트 타입검사 실패, 전략 수익성, 6시간·24시간과 Release ZIP은 각각 별도 상태로 유지했다.

Wave 27 구현 commit은 `354053df30128f2a7ae7bfbc7200e538a516b82e`이다. 같은 SHA의 GitHub Actions에서 로컬과 독립된 설치·저장소 위생·lint·typecheck·backend/frontend test·production build와 실제 Chromium desktop·tablet·mobile E2E·증거 업로드가 모두 통과했다.

## 33. 외장 원장 통합 커밋·실제 A~J 감시 재검증

2026-08-25 실제 활성 원장의 저장 flush가 간헐적으로 5~24초까지 늘어난 경로를 단계별 진단값으로 다시 조사했다. Parquet 뒤 archive manifest·종목별 통계와 candle을 `synchronous=FULL` 트랜잭션 두 번으로 확정하던 구조를 한 원자 커밋으로 합쳤다. FULL 내구성, checksum, WAL, 불변 원장, 저장공간 안전잠금과 모든 전략 기준은 유지했다.

### 구현과 실패 안전성

- 한 영속화 배치의 모든 checksum-addressed Parquet 파일을 기존 별도 process에서 먼저 fsync한다.
- 준비된 archive manifest·종목별 이벤트 통계·캔들을 한 `BEGIN IMMEDIATE`·`COMMIT`으로 확정한다.
- 중복 candle payload나 manifest checksum이 다르면 전체 SQLite 배치를 롤백해 부분 manifest·부분 통계를 남기지 않는다.
- Parquet 또는 원장 단계가 실패하면 시장·캔들 메모리 배치를 모두 복원하고 신규 PAPER 진입을 fail-closed한다.
- 기존 `manifest ms`와 `candle 원장 ms` 진단 키·화면 문구를 제거하고 `원장 통합 커밋 ms`로 교체했다.
- 실제 주문, private API, API Key, 인증과 wallet 경로는 계속 0 또는 false다.

### 변경 전·후 실제 LIVE 비교

| 검증 | 상태 | 이번 실행의 실제 결과 |
|---|---|---|
| 변경 전 진단 | PASS_DIAGNOSTIC | `run-d1cbbe3d2458`의 78번째까지 최장 flush는 24.564초였다. Parquet 0.237초·manifest 0.621초·candle 23.689초였고 최대 수신 공백 21.236초와 최장 47.999초 임계 지연 사건이 거의 같은 시각에 기록됐다. 강한 시간 연관은 확인했지만 저장장치·운영체제 내부까지 포함한 인과 증명으로 확대하지 않는다. |
| 원자성 | PASS | 테스트 trace에서 archive manifest·종목통계·candle이 `BEGIN IMMEDIATE` 1회·`COMMIT` 1회를 사용했다. 충돌 candle을 주입하면 manifest·통계가 함께 rollback됐고 기존 candle만 보존됐다. |
| 작업자 실패복구 | PASS | 통합 원장 오류를 주입하자 시장·candle 배치가 모두 복원되고 drop 0, PAPER pause·risk fault·`PERSISTENCE_FAULT_ENTRY_LOCK`이 적용됐다. |
| 실제 시작 | PASS | 열린 포지션 0에서 LaunchAgent를 새 빌드로 재시작하고 앱 내 브라우저에서 `자동 관찰 시작`을 한 번 눌렀다. 250ms 뒤 `연결 중`, 8초 뒤 `작동 중·계속 작동·새 PAPER 진입 작동·자동 복구 켜짐`, 표시지연 44ms였다. |
| 변경 후 28 flush | PASS | 새 `run-2b0119b86432`의 56,260 events·28 flush에서 최장 1.506초, 해당 Parquet 0.728초·통합 원장 0.770초, 2초 이상 flush 0이었다. 마지막 실행호가 p95는 75.969ms, 최대 수신 공백 1.231초, 임계 지연 사건·비계획 reconnect·sequence gap·drop·저장 fault·buffer drop은 모두 0이었다. |
| 저장 원장 대조 | PASS | 중간 검증시 archive 23배치·46,000 events와 종목통계 46,000건이 일치했고 candle 4,779건, shadow trade 8건이었다. archive 파일 누락·orphan manifest·외래키 위반은 0이었다. |

후속 정정. 같은 Run을 159,663 events·79 flush까지 계속 관찰하자 최장 flush가 다시 15.783초로 늘었다. Parquet은 0.252초였지만 통합 원장 커밋이 15.520초였고 최대 수신 공백 11.823초, 임계 지연 사건 6회·최장 90.400초가 기록됐다. 그러므로 위 28 flush 결과는 정확한 초기 표본이지만 지속 성능 개선 완료 증거가 아니다. 비계획 reconnect·sequence gap·drop·저장 fault는 계속 0이었으며, 후속 진단과 수정은 Wave 29와 ADR-030에 기록한다.

### 차트·진행거래·다른 전략 상태

| 검증 | 상태 | 이번 실행의 실제 결과 |
|---|---|---|
| A~J 감시 | PASS | 실제 전략 화면은 `10개 정상 감시 · 문제 0개 · 실제 주문 0`이었다. A/B ACTIVE, C~J SHADOW, 10개 모두 LONG·SHORT 켜짐이며 전략마다 12종목×양방향 24경로를 평가했다. |
| 정상 대기 이유 | PASS | 최신 거절은 시장방향·가격구조·체결흐름·호가·microprice·지속성 조건으로 설명됐다. `REJECTED + 24경로 + fault 0`은 조용한 고장이 아니라 엄격조건 정상 대기이며 자연신호를 만들기 위해 기준을 낮추지 않았다. |
| 자연 PAPER 진입 | PASS_WITH_LOSS | 실제 화면 관찰 중 Depth-adjusted OFI가 BTCUSDT LONG BASE·STRESS 두 독립계좌에 진입했다. 진행 화면은 진입 80,747, SL 80,504.71, TP1 81,122.55, TP2 81,498.1과 TP·SL·근거감쇠 자동관리를 표시했다. 13.612초 후 EDGE_DECAY로 종료됐다. |
| 차트 현재 진입 표시 | PASS | 현재 PAPER가 열리면 선택 종목·전략·BASE/STRESS·계좌범위·방향·entry·TP1·SL을 차트 banner와 가격선으로 표시하는 회귀 47개와 Chromium 3개가 통과했다. Wave 27 실제 XRP 공동·독립 동시 진입에서도 확인했다. 이번 BTC 자연 포지션은 전략·진행 화면 확인 뒤 시장화면 전환 사이 종료돼 열린 banner의 새 스크린샷은 만들지 않았고, 종료 뒤 열린 것처럼 남지 않는 상태를 확인했다. |
| 자연 shadow 원장 | PASS_WITH_LOSS | 현재 Run의 Queue BASE/STRESS 각 3건과 Depth-adjusted OFI BASE/STRESS 각 1건, 총 8건이 완료됐다. 보유 13.612~33.890초, 3초 미만 0건, 종료사유는 EDGE_DECAY였다. 비용후 손실 표본이며 작동 증거이지 수익성 증거가 아니다. |
| 실제 브라우저 console | PASS | 앱 내 브라우저 dev log 항목은 0이었다. 전략 화면 캡처는 `evidence/WAVE28_STRATEGY_MONITORING.jpg`에 보존했다. |

### 자동검증과 한계

| 검증 | 상태 | 실제 결과 |
|---|---|---|
| backend pytest | PASS | 318 passed, 22.28초 |
| 저장·런타임 표적 pytest | PASS | 44 passed, 8.19초 |
| frontend Vitest | PASS | 12 files, 47 passed |
| Playwright | PASS | 로컬 실제 Chromium desktop·tablet·mobile 3 passed, 13.6초 |
| Ruff / mypy | PASS | 오류 0 / backend/app 82 source files 오류 0 |
| ESLint / TypeScript | PASS | 오류 0 / 오류 0 |
| production build | PASS | Vite 48 modules, PAPER build safety PASS |
| security / repository hygiene | PASS | 115 source, violation·secret-like·real-order path 0 / 위반 0 |
| 활성 원장 full quick check | NOT_RUN | Wave 25에서 완료한 다중 GiB 전체검사를 짧은 지연 관찰 중 다시 실행하지 않았다. 이번 Wave는 현재 Run 건수 일치·파일 존재·orphan·외래키를 읽기 전용으로 대조했다. |
| 전략 수익성 | NOT_PROVEN | 현재 Run 자연 shadow 8건은 비용후 손실이고 여러 전략의 현재버전 표본은 30건 미만 또는 0건이다. 순위나 수익성을 주장하지 않는다. |
| 6시간 / 24시간 soak | NOT_RUN | 56,260-event 분 단위 실제 서비스 표본을 멀티시간 수용결과로 표현하지 않는다. |
| Release ZIP | NOT_RUN | 이번 Wave에서 만들지 않았다. |
| GitHub main / Actions | PASS | 구현 commit `ef1292804ea814c7deb0757f8527055ba3b83974`을 main에 push했다. [Actions 32815768312](https://github.com/robom-labs/flowscalper/actions/runs/32815768312)의 validate 1분2초, browser 1분21초와 Chromium desktop·tablet·mobile E2E·브라우저 증거 업로드가 모두 PASS했다. |

기계판독 증거는 `evidence/WAVE28_ATOMIC_PERSISTENCE_STRATEGY_QA.json`, 실제 화면은 `evidence/WAVE28_STRATEGY_MONITORING.jpg`, 상세 결정은 ADR-029다. 필수 로컬 회귀·실제 화면·짧은 실제 서비스와 구현 commit의 GitHub Actions 검증에서 미해결 FAIL과 BLOCKED는 현재 0이다. 전략 수익성, 활성 원장 전체 quick check 재실행, 6시간·24시간과 Release ZIP은 `NOT_PROVEN` 또는 `NOT_RUN`으로 분리했다.

Wave 28 구현 commit은 `ef1292804ea814c7deb0757f8527055ba3b83974`이다. 같은 SHA의 GitHub Actions에서 로컬과 독립된 설치·저장소 위생·lint·typecheck·backend/frontend test·production build와 실제 Chromium desktop·tablet·mobile E2E·증거 업로드가 모두 통과했다.

## 34. WAL checkpoint 분리와 장기 표본 정정

2026-08-25 Wave 28의 같은 실제 Run을 계속 관찰해 초기 28 flush 성능 결과가 지속되지 않음을 확인했다. SQLite 공식 WAL 문서와 현재 PRAGMA를 대조한 뒤, 기본 1,000-page 자동 checkpoint를 COMMIT 경로에서 끄고 8회 저장마다 별도 process의 PASSIVE checkpoint를 수행하도록 변경했다. WAL·`synchronous=FULL`·checksum·원자성·버퍼복구와 전략 기준은 유지했다.

### 구현과 실제 결과

| 검증 | 상태 | 이번 실행의 실제 결과 |
|---|---|---|
| Wave 28 장기 정정 | PASS_DIAGNOSTIC | `run-2b0119b86432` 159,663 events·79 flush에서 최장 flush 15.783초, Parquet 0.252초·통합 원장 15.520초, 최대 수신 공백 11.823초, 임계 지연 6회·최장 90.400초였다. 초기 56,260-event 결과는 사실이지만 지속 성능 완료 증거가 아니므로 정정했다. |
| checkpoint 분리 | PASS | `wal_autocheckpoint=0`, 8 flush 간격 PASSIVE checkpoint, 부분 checkpoint 재시도와 WAL 64MiB fail-closed를 구현했다. 화면에는 자동 checkpoint 설정·시도·부분완료·소요·frame·오류를 표시한다. |
| 변경 후 실제 장기 표본 | PASS_WITH_FOLLOWUP | 새 `run-517b78c88366` 194,449 events·97 flush에서 최장 flush 8.359초, Parquet 0.605초·통합 원장 7.741초, checkpoint 최대 17.496초였다. 최대 수신 공백 5.867초, 임계 지연 4회·최장 45.896초였지만 비계획 reconnect·sequence gap·drop·저장 fault는 0이었다. checkpoint는 분리됐으나 같은 Python process의 FULL 커밋 지연이 남아 Wave 30으로 이관했다. |
| 실제 A~J 화면 | PASS | 실제 브라우저에서 10개 정상 감시·문제 0·실제 주문 0, A/B ACTIVE·C~J SHADOW, 10개 모두 LONG·SHORT 켜짐과 전략별 24경로를 확인했다. 조용한 전략은 오류가 아니라 엄격조건 정상 대기였고 기준을 낮추지 않았다. |

### 자동검증과 한계

| 검증 | 상태 | 실제 결과 |
|---|---|---|
| backend pytest | PASS | 320 passed |
| 저장·런타임 표적 pytest | PASS | 46 passed |
| frontend Vitest | PASS | 12 files, 47 passed |
| Playwright | PASS | 로컬 실제 Chromium desktop·tablet·mobile 3 passed |
| 정적·build·안전·security | PASS | Ruff, mypy backend/app 82 source, ESLint, TypeScript, Vite 48 modules, PAPER build safety, security 115 source와 repository hygiene가 모두 통과했다. |
| 전략 수익성 | NOT_PROVEN | 자연 거래 발생과 정상 대기는 작동 증거일 뿐 수익성 증거가 아니다. |
| 6시간 / 24시간 soak | NOT_RUN | 이번 실제 표본을 멀티시간 수용결과로 표현하지 않는다. |
| Release ZIP | NOT_RUN | 이번 Wave에서 만들지 않았다. |
| GitHub main / Actions | PASS | 구현 commit `48823ee7bd4358f42371448b1e856efa34e19fb7`을 main에 push했다. [Actions 32817722186](https://github.com/robom-labs/flowscalper/actions/runs/32817722186)의 validate·browser·증거업로드가 PASS했다. |

기계판독 증거는 `evidence/WAVE29_SEPARATED_WAL_CHECKPOINT_QA.json`, 실제 전략 화면은 `evidence/WAVE29_STRATEGY_MONITORING.jpg`, 상세 결정은 ADR-030이다. Wave 29는 checkpoint 분리 구현과 회귀검증은 완료했지만 잔여 FULL 커밋 지연 때문에 `COMPLETE_WITH_FOLLOWUP`이다.

## 35. 내구성 저장 전체 프로세스 격리·차트 진입·전략 전수 점검

2026-08-25 Parquet 작성만이 아니라 archive manifest·종목통계·캔들의 `synchronous=FULL` 원자 커밋까지 하나의 background I/O process로 옮겼다. worker는 독립 SQLite 연결에 WAL, foreign key, FULL 동기화, 자동 checkpoint 0과 60초 writer wait를 적용한다. process·Parquet·SQLite 오류는 시장·캔들 버퍼를 모두 복원하고 신규 PAPER 진입을 fail-closed한다.

### 실제 시작과 저장 경로 비교

| 검증 | 상태 | 이번 실행의 실제 결과 |
|---|---|---|
| 새 Run 시작 | PASS | 열린 포지션 0에서 LaunchAgent를 재시작하고 실제 앱 내 브라우저에서 `자동 관찰 시작`을 한 번 눌렀다. 250ms 뒤 `연결 중`, 8초 뒤 `작동 중`, 표시지연 39ms였다. 새 `run-622167a01f3c`은 1,000 USDT·손익 0·수수료 0·거래 0에서 시작했고 실제 주문과 인증은 false였다. |
| process 저장 원자성 | PASS | 별도 연결이 checksum Parquet·manifest·통계·candle을 한 `BEGIN IMMEDIATE`·`COMMIT`으로 확정하고 주 연결에서 즉시 읽혔다. 오류 주입 시 두 버퍼 복원·drop 0·신규진입 안전잠금을 확인했다. |
| 실제 160k gate | PASS_WITH_RESIDUAL | 160,141 events gate와 165,405 events 후속까지 관찰했다. 82 flush 중 최장 13.065초, 원장 FULL 커밋 최대 12.530초, checkpoint 최대 17.743초가 worker에서 발생했다. 그 동안 실행호가 p95 37.717ms·체결 p95 278.101ms였고 임계 지연은 2회·최장 1.816초, 최종 active false·entry lock false였다. |
| 전후 비교 | PASS_WITH_LIMIT | 같은 장비의 분리 전 `run-517b78c88366`은 임계 지연 4회·최장 45.896초였고 분리 후에는 2회·최장 1.816초였다. 미래의 공개 네트워크·저장장치 지연 0을 보장하지 않으며 남은 두 사건도 숨기지 않는다. |
| 연장 관찰 | PASS_WITH_LIMIT | 207,283 events·103 flush와 계획 회전 2회까지 checkpoint 최대값은 22.984초로 늘었지만 처리 p95 39.903ms·체결 p95 45.371ms였고 임계 지연 사건은 2회에서 증가하지 않았다. 최종 active·entry lock false, 비계획 reconnect·gap·drop·저장 fault·buffer drop 0이었다. 긴 worker checkpoint 자체를 미래 지연 0의 증거로 해석하지 않는다. |
| 연결·원장 불변조건 | PASS | 비계획 reconnect·sequence gap·drop·저장 fault·buffer drop은 모두 0이었다. 읽기 전용 대조에서 archive 83배치·166,000 events와 종목통계 166,000건이 일치했고 candle 17,544건, shadow trade 14건, main trade 1건, open persisted position 0건, fill 2건, 누락 archive 파일 0이었다. |

### 차트 현재 진입과 계획 표시

| 검증 | 상태 | 이번 실행의 실제 결과 |
|---|---|---|
| 차트 banner·가격선 | PASS | 실제 SOLUSDT Queue Microprice SHORT BASE/STRESS에서 전략·하락방향·계좌범위·진입 101.49·TP1 100.996·SL 101.799를 차트에서 확인했다. |
| 전체 진입계획 | PASS | 실제 ENAUSDT Queue BASE 계획에서 진입 0.154223·TP1 0.153498·TP2 0.152767·SL 0.154698·수량 81·최대 계획손실 0.0561 USDT를 확인했다. |
| 다른 전략 자연 진입 | PASS | 이전에 조용하던 F Aggressor Flow가 실제 BTCUSDT SHORT BASE/STRESS에 자연 진입했고 차트는 진입 80,593.9·TP1 80,219.06·SL 80,835.73을 표시했다. 미래 거래처럼 고정한 fixture가 아니라 현재 공개시장 PAPER 관찰 결과다. |
| 화면 증거 | PASS | `evidence/WAVE30_LIVE_CHART_POSITION.jpg`, `evidence/WAVE30_LIVE_CHART_POSITION_2.jpg`, `evidence/WAVE30_STRATEGY_MONITORING.jpg`에 실제 화면을 보존했다. 브라우저 console dev log 항목은 0이었다. |

### 전략 A~J 전수 점검

| 검증 | 상태 | 이번 실행의 실제 결과 |
|---|---|---|
| Registry·방향·계좌 | PASS | 화면은 `10개 정상 감시 · 문제 0개 · 실제 주문 0`이었다. A/B는 ACTIVE, C~J는 SHADOW이며 10개 모두 LONG·SHORT가 켜져 있고 전략별 12종목×양방향 24경로, 계좌 fault 0이었다. |
| 자연 완료 전략 | PASS_WITH_LOSS | 이번 Run에서 A LSA, C VWAP exhaustion, E Queue Microprice, F Aggressor Flow, H Depth-adjusted OFI가 자연 shadow 완료 표본을 만들었다. 총 14건, 보유 14.624~41.144초, 3초 미만 0건, 종료사유는 EDGE_DECAY였다. 비용후 손실 표본이므로 수익성을 주장하지 않는다. |
| 조용한 전략 | PASS_WAITING | B CBR, D OFI pullback, G multilevel microprice, I OFI-return confluence, J book-slope는 각각 24경로를 계속 평가했고 account fault 0과 시장방향·체결흐름·호가·가격구조·지속성 거절 이유를 표시했다. 이번 표본의 무진입은 정상 대기이며 신호를 만들기 위해 임계값을 낮추지 않았다. |
| main 거래 | PASS_WITH_LOSS | A DOGEUSDT LONG 1건이 36.508초 뒤 EDGE_DECAY로 종료됐고 순손익은 -0.16291445 USDT였다. 거래 발생은 종단 간 작동 증거이지 수익성 증거가 아니다. |

### 자동검증과 한계

| 검증 | 상태 | 실제 결과 |
|---|---|---|
| backend pytest | PASS | 321 passed, 46.66초 |
| 저장·런타임 표적 pytest | PASS | 47 passed, 30.19초 |
| frontend Vitest | PASS | 12 files, 47 passed, 4.51초 |
| Playwright | PASS | 로컬 실제 Chromium desktop·tablet·mobile 3 passed, 13.4초 |
| Ruff / mypy | PASS | 오류 0 / backend/app 82 source files 오류 0 |
| ESLint / TypeScript | PASS | 오류 0 / 오류 0 |
| production build | PASS | Vite 48 modules, PAPER build safety PASS |
| security / repository hygiene | PASS | 115 source, violation·secret-like·real-order path 0 / 위반 0 |
| 활성 원장 foreign-key / quick check | NOT_RUN | Wave 25의 다중 GiB 전체검사를 이번 저장 관찰 중 반복하지 않았다. foreign-key 전수검사는 read snapshot이 checkpoint를 붙잡아 중단했고, 현재 Run 건수와 83개 archive 파일은 읽기 전용으로 대조했다. |
| 전략 수익성 | NOT_PROVEN | 14건의 자연 shadow와 1건의 main 표본은 모두 작고 비용후 손실이다. 전략 순위나 수익성을 주장하지 않는다. |
| 6시간 / 24시간 soak | NOT_RUN | 165,405-event 실제 표본을 멀티시간 수용결과로 표현하지 않는다. |
| Release ZIP | NOT_RUN | 이번 Wave에서 만들지 않았다. |
| GitHub main / Actions | PASS | 구현 commit `663e3857d4574aef9af9e16af3e54699c5f34984`을 main에 push했다. [Actions 32820190558](https://github.com/robom-labs/flowscalper/actions/runs/32820190558)의 validate·browser·실제 Chromium E2E·증거업로드가 PASS했다. |

기계판독 증거는 `evidence/WAVE30_OUT_OF_PROCESS_PERSISTENCE_STRATEGY_QA.json`, 상세 결정은 ADR-031이다. 구현·로컬 회귀·실제 화면·160,000-event 실제 서비스·구현 commit GitHub Actions의 미해결 FAIL과 BLOCKED는 현재 0이다. 미래 지연 0과 전략 수익성은 `NOT_PROVEN`, 활성 원장 전체검사 재실행·6시간·24시간·Release ZIP은 `NOT_RUN`이다.

Wave 30 구현 commit은 `663e3857d4574aef9af9e16af3e54699c5f34984`이다. 같은 SHA의 GitHub Actions에서 로컬과 독립된 설치·저장소 위생·lint·typecheck·backend/frontend test·production build와 실제 Chromium desktop·tablet·mobile E2E·증거 업로드가 모두 통과했다.

## 36. 비용후 손실전략 중지와 공개시장 처리 여유

2026-08-25 현재 strategy revision `2026-08-25-wave23`의 독립 `LIVE_PUBLIC` BASE 거래를 먼저 확인했다. A는 18건 중 1승·기대값 -16.273bp·PF 0.012·순손익 -12.4191 USDT, E는 96건 중 12승·기대값 -12.406bp·PF 0.084·순손익 -45.9369 USDT, H는 20건 중 승리 0·기대값 -15.736bp·PF 0·순손익 -32.5472 USDT였다. 승률을 높게 보이게 하려고 과거 거래를 지우거나 비용을 낮추지 않았다.

### 시간순 저장 공개시장 연구와 결정

`scripts/research_strategy_revision.py`로 시간순 train 8개 Run과 더 늦은 holdout 5개 Run을 분리했다. 같은 종목의 현재시각 이전 데이터만 사용하고, 500ms 평가·실제 ask/bid·30초 horizon·BASE 13bp·STRESS 25bp로 계산했다.

| 전략·후보 | train | holdout | 결정 |
|---|---|---|---|
| E baseline | 958건, 비용후 승률 12.735%, 기대값 -13.222bp, PF 0.124 | 188건, 승률 9.043%, 기대값 -14.067bp, PF 0.160 | 기본 OFF |
| H baseline | 102건, 비용후 0승, 기대값 -12.556bp, PF 0 | 47건, 비용후 0승, 기대값 -12.555bp, PF 0 | 기본 OFF |
| strict·cost-aware E/H 수정후보 | 자연신호 0 | 자연신호 0 | 배포 거절, 결과 확인 뒤 기준완화 없음 |

H의 비용전 방향 승률은 train 54.902%, holdout 57.447%였지만 평균 가격변화가 약 0.444bp에 불과해 실제 bid·ask와 왕복비용을 넘지 못했다. 따라서 높은 겉보기 승률도 배포 근거로 사용하지 않았다. A는 공동 main PAPER의 ACTIVE에서 SHADOW로 내렸고, B만 ACTIVE로 유지했다. C/D/F/G/I/J는 SHADOW, E/H는 OFF다. LONG·SHORT 제어, 20개 독립계좌와 모든 과거 거래는 보존했다. 현재 revision은 `2026-08-25-wave31`이며 이전 revision 거래는 현재 기본 성과에서 분리한다. 상세 결정은 ADR-032다.

### queue 포화 원인과 수정

수정 전 `run-622167a01f3c`은 provider queue 4,096/4,096, drop 270,796, 현재 실행호가 p95 약 33ms인데 표시 p95는 12,127.627ms였다. 현재 시각이나 SQLite가 직접 원인이 아니라 소비할 수 있는 속도보다 depth snapshot과 trade를 많이 전달해 오래된 queue가 남은 것이 원인이었다.

모든 raw depth delta는 먼저 로컬 Binance 호가장에 적용하고, 종목별 첫 sequence 시작과 마지막 sequence 끝을 보존한 마지막 완성 snapshot만 500ms마다 전달하도록 바꿨다. aggregate trade도 500ms로 합쳤다. stale·sequence·1,500ms fail-closed 검사는 유지했다.

### 새 Run과 10분 연속 관찰

열린 main·League PAPER 포지션이 모두 0인 것을 확인한 뒤 LaunchAgent를 재시작하고 시작을 한 번 호출했다. 새 `run-0ca162282d14`은 1,000 USDT, main 손익·수수료·슬리피지·거래 0에서 시작했다.

| 검증 | 상태 | 실제 결과 |
|---|---|---|
| 10분 처리 여유 | PASS | 10초 간격 61표본, event 2,404→47,648으로 45,244건 증가했다. queue 최대 12/4,096, drop 0이었다. |
| 지연·안전 | PASS | 실행호가 p95 관찰범위 24.016~35.249ms, trade p95 34.821→77.953ms였다. entry lock·critical incident·비계획 reconnect·sequence gap은 전 표본 0이었다. |
| 저장 격리 | PASS_WITH_LIMIT | persistence fault·buffer drop 0이었다. 후속 dashboard에서 flush 최대 3.304초, checkpoint 최대 7.790초였지만 시장 경로 queue 포화·critical lag는 발생하지 않았다. 10분은 멀티시간 soak가 아니다. |
| 자원 | PASS_WITH_LIMIT | CPU 표본 최대 99.157%, 메모리 159.0→267.672MB였다. 짧은 표본에서 queue headroom은 유지됐지만 장시간 메모리 안정성은 계속 관찰한다. |
| PAPER 안전 | PASS | main 포지션·거래는 전 표본 0, 실제 주문 false, 인증 불필요였다. A 독립 BASE/STRESS 자연 포지션은 최대 2건이었다. |

### 실제 브라우저와 자연 PAPER 진입

실제 앱 내 브라우저를 다시 불러와 `작동 중`, 공개시장 계속 관찰, 새 PAPER 진입 작동, 자동복구 켜짐과 표시 지연 27ms를 확인했다. 전략 화면은 `8개 감시 · 검증 중지 2개 · 문제 0개 · 실제 주문 0`이었다. A SHADOW, B ACTIVE, E/H OFF와 나머지 SHADOW, 각 활성 전략의 24개 평가경로를 직접 확인했다.

같은 실제 화면에서 자연 발생한 A ENAUSDT LONG BASE PAPER 포지션의 진입 0.15095, 현재 0.15088, SL 0.149718, TP1 0.152409, TP2 0.154789, 수량 3,489, 명목 526.66 USDT, 수수료·슬리피지와 17초 보유를 확인했다. 해당 거래는 18.972초, 순손익 -1.115878256 USDT로 종료됐다. 최종 재확인 시 현재 revision A BASE는 2건·0승·순손익 -2.101888592 USDT·중앙 보유 16.489초였고 main 거래는 0이었다. 이 작은 손실 표본은 현재 revision 진입·자동관리·비용회계가 연결됐고 A를 main에서 내린 결정이 안전했음을 뒷받침할 뿐 수익성이나 최종 전략순위 증거가 아니다. 화면은 `evidence/WAVE31_STRATEGY_RETIREMENT_MONITORING.jpg`에 보존했다.

### 자동검증과 한계

| 검증 | 상태 | 실제 결과 |
|---|---|---|
| backend pytest | PASS | 322 passed, 10.16초 |
| supervisor 표적 pytest | PASS | 18 passed |
| frontend Vitest | PASS | 12 files, 47 passed |
| Playwright | PASS | 로컬 Chromium desktop·tablet·mobile 3 passed |
| 정적·build·안전·security | PASS | Ruff, mypy backend/app 82 source, ESLint, TypeScript, Vite 48 modules, PAPER build safety, security 115 source와 repository hygiene가 통과했다. |
| 높은 승률·전략 수익성 | NOT_PROVEN | 비용후 손실 전략은 신규진입에서 제외했지만 현재 revision의 승리 전략은 증명되지 않았다. E/H 대체후보도 자연신호 0이라 배포하지 않았다. |
| 6시간 / 24시간 soak | NOT_RUN | 10분 queue-headroom gate를 장시간 수용결과로 표현하지 않는다. |
| Release ZIP | NOT_RUN | 이번 Wave에서 만들지 않았다. |
| GitHub main / Actions | PASS | 구현 commit `60cecafadfa7a97e70e5b15de47b9d8e2a648c8f`을 main에 push했다. [Actions 32829795266](https://github.com/robom-labs/flowscalper/actions/runs/32829795266)의 validate 54초, browser 1분9초와 Chromium desktop·tablet·mobile E2E·브라우저 증거 업로드가 모두 PASS했다. |

기계판독 증거는 `evidence/WAVE31_STRATEGY_RETIREMENT_RUNTIME_HEADROOM_QA.json`, 실제 화면은 `evidence/WAVE31_STRATEGY_RETIREMENT_MONITORING.jpg`, 상세 결정은 ADR-032다. 이번 결론은 나쁜 승률을 숨기는 것이 아니라 비용후 실패 전략을 기본 진입에서 제외하고, 남은 전략은 현재 revision 자연표본이 쌓일 때까지 순위를 매기지 않는 것이다.

Wave 31 구현 commit은 `60cecafadfa7a97e70e5b15de47b9d8e2a648c8f`이다. 같은 SHA의 GitHub Actions에서 로컬과 독립된 설치·저장소 위생·lint·typecheck·backend/frontend test·production build와 실제 Chromium desktop·tablet·mobile E2E·증거 업로드가 모두 통과했다.

## 37. 실행 감사 시간축 수정과 A~J 전체 비용후 선별

### 감사 시각 결함과 실제 거래 재검증

`PaperPortfolioEngine`의 감사 기록은 후보의 불변 `signal_time_ms`를 진입체결·보호·관리청산·실제 청산 이벤트에도 반복해서 사용하고 있었다. 거래 원장의 실제 진입·종료와 보유시간은 정상이었지만, 감사표만 모든 동작이 동시에 발생한 것처럼 보이는 관측 결함이었다.

후보 관련 이벤트만 signal time을 유지하고, 진입·청산 요청은 해당 book 시각, 실제 fill은 fill book 시각, 관리결정은 현재 decision 시각을 기록하도록 수정했다. 결정적 회귀검사는 후보 1,000ms, 진입 1,250ms, TP1 요청·체결 2,000·2,250ms, TP2 요청·체결 3,000·3,250ms, 관리청산 결정·체결 126,000·126,250ms를 각각 검증한다.

실제 공개시장 A DOGEUSDT LONG PAPER 거래에서도 후보 1,787,650,399,828ms, 진입 1,787,650,400,348ms, 관리결정 1,787,650,428,778ms, 청산 1,787,650,429,316ms가 기록됐다. 후보→진입 520ms, 진입→관리결정 28.430초, 관리결정→청산 538ms이며 원장 보유시간 28.968초와 일치했다. BASE 순손익은 -0.492706494 USDT, STRESS는 -0.982675596 USDT였다. 이 결과는 감사 시간축과 비용 회계를 검증한 것이며 전략 수익성 증거가 아니다. 상세 결정은 ADR-033이다.

### 실제 A~J evaluator의 시간순 저장시장 선별

`scripts/research_strategy_revision.py`가 실제 A~J `StrategyRegistry`와 `StrategySignalEvaluator`를 직접 호출하도록 확장했다. 저장된 `LIVE_PUBLIC` Run 13개를 시간순 train 8개와 더 늦은 holdout 5개로 분리하고, 현재시각 이전의 피처만 사용했다. 500ms 평가, 실제 ask·bid 진입과 반대호가 청산, 30초 고정 horizon, BASE 13bp·STRESS 25bp를 적용했다.

| 전략 | train BASE | holdout BASE | 결정 |
|---|---|---|---|
| A LSA 반전 | 25건·2승·승률 8%·기대값 -21.139bp·PF 0.072 | 10건·0승·기대값 -13.767bp·PF 0 | 기본 OFF |
| B CBR 돌파 | 1건·0승·기대값 -10.519bp | 0건 | ACTIVE 유지, 수익성 NOT_PROVEN |
| C VWAP 소진 | 4건·1승·기대값 -2.739bp·PF 0.535 | 1건·0승·-18.670bp | SHADOW 유지, 표본 부족 |
| D OFI 눌림 | 4건·0승·기대값 -14.289bp | 0건 | SHADOW 유지, 표본 부족 |
| F 체결흐름 | 3건·0승·기대값 -10.766bp | 0건 | SHADOW 유지, 표본 부족 |
| G/I/J | 0건 | 0건 | SHADOW 유지, 기준완화 없음 |
| E/H | ADR-032 실패 재현 | ADR-032 실패 재현 | OFF 유지 |

A는 비용전에도 train 기대값 -8.139bp, holdout -0.767bp라 단순 수수료 문제만이 아니었다. 높은 승률을 만들기 위해 threshold를 낮추거나 holdout을 보고 parameter grid를 탐색하지 않았다. 전략 revision을 `2026-08-25-wave32`로 올리고 B만 ACTIVE, C/D/F/G/I/J는 SHADOW, A/E/H는 OFF로 설정했다. 상세 결정은 ADR-034다.

### Fresh Run과 실제 브라우저

열린 main·League 포지션 0과 실제 주문·인증 false를 확인한 뒤 서비스를 재시작했다. 새 `run-04a41901147e`은 구현 commit `293a3db5ccfcc270c4a8382d51bffc3d4792974f`, 1,000 USDT, main 손익·수수료·슬리피지·거래 0과 revision `2026-08-25-wave32`로 원장에 기록됐다. 시작 작업 `control-d805083db8f94848a0a9fe192eac6c7c`은 `COMPLETED`였다.

10초 간격 12표본에서 event 7,502→16,418, queue 0/4,096, drop 0, 실행호가 p95 25.406~33.535ms, 공개체결 p95 37.175~62.719ms였다. entry lock·critical incident·비계획 reconnect·sequence gap·persistence fault·buffer drop은 모두 0이었다. wide scanner p95는 1,529.573~1,671.764ms였지만 실행용 정밀 호가와 분리돼 있었고 진입잠금은 발생하지 않았다.

Run을 계속 유지한 뒤 15분 계획 WebSocket 교체 1회에서 신규 진입 잠금이 한 표본에 관찰됐다. 이는 계획 교체 준비 중 fail-closed한 뒤 새 공개 호가를 검증하는 기존 안전동작이었다. 다음 표본에서 자동 해제됐고 전체 reconnect 1은 계획 교체 1과 일치했으며 비계획 reconnect·critical incident·sequence gap·resync·drop·persistence fault는 0이었다. 최대 이벤트 수신 공백은 4,132.979ms였고 회복 뒤 실행호가 p95 약 36ms, queue 0이었다.

Run 생성 원장에는 A/E/H OFF가 정확히 기록됐다. 이후 열려 있던 브라우저에서 세 전략을 SHADOW로 바꾸는 명시적 `POST /api/strategies/...` 요청이 들어왔으며 자동 런타임 변경으로 오인하지 않았다. E가 잠깐 활성화된 동안 자연 BASE 5건은 1승 4패, 승률 20%, 기대값 -1.285985 USDT, PF 0.000031, 비용후 순손익 -6.429924 USDT였다. 기존 포지션은 지정된 TP1·TP2·SL과 관리청산 경로로 종료됐고 A/E/H를 다시 OFF로 설정해 추가 평가를 중지했다. 최종 main·League 열린 포지션은 0, queue/drop/critical/reconnect/gap/persistence fault는 0이다.

실제 앱 내 브라우저는 `7개 감시 · 검증 중지 3개 · 문제 0개 · 실제 주문 0`, A/E/H 꺼짐, B 공동·독립 모의 중, 나머지 정상 감시를 표시했다. 차트는 자연 PAPER 포지션의 전략·방향·계좌범위·entry·TP1·SL을 표시했고 브라우저 console 로그는 0건이었다. 화면은 `evidence/WAVE32_STRATEGY_RETIREMENT_BROWSER.png`에 보존했다.

### 자동검증과 남은 한계

| 검증 | 상태 | 실제 결과 |
|---|---|---|
| backend pytest | PASS | 323 passed |
| backend 정적검사 | PASS | Ruff와 mypy 82 source |
| frontend | PASS | Vitest 12 files·47 tests, ESLint, TypeScript, Vite 48 modules |
| Playwright | PASS | Chromium desktop·tablet·mobile 3 passed |
| PAPER 안전·security·위생 | PASS | build safety, security 115 source·위반 0, repository hygiene PASS |
| 실제 브라우저 | PASS | 7개 감시·3개 OFF·문제 0·실제 주문 0, console 오류 0 |
| 높은 승률·수익성 | NOT_PROVEN | 실패 전략을 중지했지만 B와 나머지 전략의 충분한 비용후 자연표본이 없다. |
| 활성 원장 full integrity check | NOT_RUN | 작동 중 writer와 경쟁하는 전체 검사는 실행하지 않았다. 기존 checksum·불변 저장 회귀는 PASS다. |
| 6시간 / 24시간 soak | NOT_RUN | 짧은 연속 표본을 멀티시간 결과로 표현하지 않는다. |
| Release ZIP | NOT_RUN | 이번 Wave에서 만들지 않았다. |

기계판독 증거는 `evidence/WAVE32_AUDIT_TIMELINE_STRATEGY_RETIREMENT_QA.json`, 실제 화면은 `evidence/WAVE32_STRATEGY_RETIREMENT_BROWSER.png`, 결정 근거는 ADR-033·ADR-034다. 구현 commit `293a3db5ccfcc270c4a8382d51bffc3d4792974f`을 GitHub main에 push했고 [Actions 32835366808](https://github.com/robom-labs/flowscalper/actions/runs/32835366808)의 validate 1분7초와 browser 1분5초, desktop·tablet·mobile Chromium 흐름 및 증거업로드가 모두 PASS했다.

## 38. D 비용후 폐기, LIVE 분석 즉시화와 Fresh Run 보존 종료

### 더 늦게 발생한 D 자연 PAPER 거래와 기본 OFF 결정

Wave 32의 시간순 저장시장 train에서 D `OFI_CONTINUATION_PULLBACK_V1`는 BASE 4건·0승·기대값 -14.289bp였고 후기 holdout은 0건이었다. 기준을 낮추지 않고 계속 실행한 `run-04a41901147e`에서 더 늦은 자연 `LIVE_PUBLIC` BASE 2건이 발생했다.

| 종목·방향 | 진입→청산 | 보유 | 총손익 | 수수료·슬리피지 | 순손익 | 종료 |
|---|---:|---:|---:|---:|---:|---|
| BNBUSDT LONG | 700.090→700.460 | 20.146초 | +0.07770 | 0.17646930·0.00105 | -0.09981930 USDT | EDGE_DECAY |
| ENAUSDT LONG | 0.1508500→0.1508100 | 29.580초 | -0.0170800 | 0.077285292·0.0021350 | -0.096500292 USDT | EDGE_DECAY |

합산 BASE는 0승 2패·순손익 -0.196319592 USDT·기대값 -10.8855302173bp·PF 0이고, STRESS는 0승 2패·순손익 -0.453259184 USDT·기대값 -23.0877574739bp·PF 0이다. 두 거래는 1~2초 종료 결함이 아니며 후보→진입→관리결정→청산 시간순도 일치했다. train 4건에 이은 더 늦은 자연 BASE 2건이 모두 비용후 손실이므로 D를 기본 `SHADOW`에서 `OFF`로 내렸다. 전략 코드·과거 불변 거래·BASE/STRESS 독립계좌·LONG/SHORT·수동 재활성화는 보존했다. revision은 `2026-08-25-wave33`이다. 현재 승률이 높아졌다거나 수익성이 입증됐다는 결론은 `NOT_PROVEN`이다.

### LIVE 전략 분석 API 지연 제거

`/api/analytics/strategies`가 15.940653초·16.010164초·13.722936초 대기하는 것을 재현했다. LIVE는 부팅 때 checksum 검증한 현재·이전 버전 cache와 현재 process 완료거래를 ID로 병합하는데도 매 API요청이 활성 2.3GiB SQLite `shadow_trades`를 다시 읽고 writer lock 경쟁을 대기한 것이 원인이었다.

LIVE 전략·전략별 종목·분석 범위는 검증 cache와 현재 process 거래만 사용하고, non-LIVE·replay는 불변 원장 읽기를 유지했다. 수정 후 전략 API 5회는 2.697~3.565ms, 전략별 종목 API 5회는 2.723~4.190ms였다. 실제 541ms·833ms persistence flush 중 6회도 2.288~4.136ms였다. 저장거래가 cache에 포함되면서 테스트가 금지한 원장 재주사는 호출되지 않는 회귀검사를 추가했다. 상세 결정은 ADR-036이다.

### READY→Fresh Run의 과거 Run 보존 종료

macOS LaunchAgent는 항상 `READY`로 부팅하고, 기존 archive 함수는 READY에서 즉시 반환했다. 그 결과 새 LIVE Run을 만들 때 과거 Run을 종료하지 않아 `finalized_ts_ms IS NULL`인 행이 76개 누적되었다. 거래는 Run ID로 분리됐지만 수명주기는 잘못됐다.

새 LIVE·DEMO·Run·venue failover 직전에 평평한 과거 Run을 한 transaction에서 `preserved=true`·`recovered_as_superseded=true`로 종료하도록 수정했다. 거래·주문·체결·snapshot·archive는 삭제하거나 다시 쓰지 않는다. 실제 이관 후 미종료는 현재 `run-f7118bed2264` 1개만 남았고 76개 과거 행이 보존 종료됐다. 최근 checksum 검증 복구 snapshot에 pending entry나 position이 있으면 `RECOVERY_OPEN_PAPER_EXPOSURE`로 새 Run을 차단하는 회귀검사도 통과했다. 상세 결정은 ADR-037이다.

### Fresh LIVE PAPER 실행·저장·화면

시작 작업 `control-2f2c6afaa49548efb77850d36143a268`은 `COMPLETED`였고 `run-f7118bed2264`는 구현 commit `248cfefba7e9a684a68614e90760107d7a77a25b`, 1,000 USDT·main 손익/수수료/슬리피지/거래 0·실제주문 false·인증 false·50 wide·12 deep로 시작했다.

32,571 events·491.849초 지점에서 실행호가 p95 26.189697ms·공개체결 p95 54.088867ms·queue 0/4,096·drop 0이었다. critical incident·비계획 reconnect·sequence gap·persistence fault·entry lock·열린 League position은 0이었다. 첫 worker `FULL` 커밋이 일시적으로 10.894초, 전체 flush가 11.142초, 첫 별도 checkpoint가 13.473초 걸렸다. 후속 flush는 357~520ms로 회복했고 두 번째 checkpoint는 1.278초였으며 그 동안 시장 처리 이상은 없었다. 이 일시 저장지연은 숨기지 않고 `PASS_WITH_LIMIT`로 남긴다.

연장 관찰에서 913.309초에 15분 계획 교체가 시작돼 `RECONNECTING`·신규진입 잠금으로 바뀌었고, 914.361초 `CONNECTING`, 915.425초·62,216 events에서 `LIVE`·잠금 해제로 복귀했다. 관찰된 잠금→복귀는 2.116초였다. 계획 교체 1회와 전체 reconnect 1회가 일치했고 비계획 reconnect·sequence gap·resync·drop·critical incident·persistence fault는 0이었다.

실제 앱 내 브라우저는 `6개 감시 · 검증 중지 4개 · 문제 0개 · 실제 주문 0`, B ACTIVE 24개 경로, C/F/G/I/J SHADOW 각 24개 경로, A/D/E/H OFF 0개 경로를 표시했다. 모든 LONG·SHORT 제어는 켜져 있고 새 revision 완료표본은 아직 0건이므로 성과화면은 `표본 부족`을 표시했다. 브라우저 console 오류는 0건이고 화면은 `evidence/WAVE33_STRATEGY_RETIREMENT_ANALYTICS_BROWSER.png`에 보존했다.

### 자동검증·외부 경쟁 한계

| 검증 | 상태 | 실제 결과 |
|---|---|---|
| backend pytest | PASS | 327 passed, 37.72초 |
| frontend Vitest | PASS | 12 files·47 tests |
| Ruff / mypy | PASS | 오류 0 / backend/app 82 source files 오류 0 |
| ESLint / TypeScript | PASS | 오류 0 / 오류 0 |
| production build / PAPER safety | PASS | Vite 48 modules / PAPER 불변조건 PASS |
| security / repository hygiene | PASS | 115 source·violation/secret-like/real-order path 0 / PASS |
| Playwright | PASS | desktop·tablet·mobile 3 passed, 12.5초 |
| 실제 브라우저 | PASS | 6개 감시·4개 OFF·문제 0·실제 주문 0·console 오류 0 |
| 전략 연구 재실행 | NOT_COMPLETED | 13개 Run 전수 재계산이 10분 한도를 넘어 중단했고 0-byte 부분 출력은 증거로 사용하지 않았다. 기존 Wave32의 완료된 시간순 결과와 더 늦은 자연 거래만 사용했다. |
| 고CPU 연구 병행 | PASS_WITH_LIMIT | 이전 Wave32 Run에서 479.597ms 임계지연 incident 1회·43 events가 발생했다. 종료 후 p95·queue·reconnect·gap·drop·fault는 회복했다. 고CPU offline 연구를 LIVE와 같은 host에서 전속 실행하지 않는 근거다. |
| 활성 원장 full integrity | NOT_RUN | 작동 중인 multi-GiB writer와 경쟁하는 전수검사는 반복하지 않았다. checksum·복구·보존종료 회귀검사는 PASS다. |
| 높은 승률·수익성 | NOT_PROVEN | 실패 D를 기본 OFF했지만 남은 전략의 현재 revision 비용후 표본이 아직 없다. 30건 전에 순위를 매기지 않는다. |
| 6시간 / 24시간 soak | NOT_RUN | 491.849초 표본을 멀티시간 결과로 표현하지 않는다. |
| Release ZIP | NOT_RUN | 이번 Wave에서 만들지 않았다. |
| GitHub main / Actions | PASS | 구현·테스트 commit `0c256ab03be26f5169a9e31887701398d5f8f190`을 main에 push했다. [Actions 32840334068](https://github.com/robom-labs/flowscalper/actions/runs/32840334068)의 validate 1분2초, browser 1분11초와 Chromium desktop·tablet·mobile·증거업로드가 모두 PASS했다. |

기계판독 증거는 `evidence/WAVE33_COST_RETIREMENT_ANALYTICS_RUN_LIFECYCLE_QA.json`, 실제 화면은 `evidence/WAVE33_STRATEGY_RETIREMENT_ANALYTICS_BROWSER.png`, 결정 근거는 ADR-035·ADR-036·ADR-037이다. 이번 결론은 나쁜 승률을 숨기기가 아니라, 실패 전략의 새 진입을 중지하고 현재 revision의 자연 비용후 표본을 새로 모으는 것이다.

## 39. 전면 점검, 사전등록 장중 연구, Strategy Governor와 서비스 복구

2026-08-26 첨부 실행지시 867줄을 SHA-256 `f713a2c8e95c364641035138678739922685c7539daef8fb90465495e85017b3`으로 고정해 전체를 읽고, 기존 프로젝트의 분리 작업트리에서 구현·연구·검증했다. 새 프로젝트나 실제 주문 경로는 만들지 않았다.

### 상태·제어·기록·전략 운용 계약

| 검증 | 상태 | 이번 실행의 실제 결과 |
|---|---|---|
| 시작·새 Run·pause·resume | PASS | idempotency key와 control revision을 추가해 같은 요청은 같은 operation을 반환하고, 새 Run은 명시적인 요청에서만 생성한다. 사용자 pause와 자동 안전잠금은 별도 상태다. |
| 전략 설정 | PASS | CAS revision, actor, reason, manual lock, rollback과 불변 감사 이력을 API·SQLite·한국어 UI에 연결했다. |
| 기록 범위 | PASS | main/전략리그, 현재/전체 Run, BASE/STRESS, 현재/과거 전략버전, LIVE_PUBLIC/OFFLINE_FIXTURE 범위를 분리했다. |
| 시간구간 | PASS | 1m·3m·5m·15m·30m·1h·4h를 단일 registry와 canonical completed candle 경로로 통합했다. |
| 전략 운용 정보 | PASS | 10개 런타임 전략에 horizon, 예상 보유, 신호 반감기, 입력 시간구간, exit model, 최대 안전보유와 비용모델 버전을 선언하고 전략 상세에 표시했다. |
| Strategy Governor | PASS | RESEARCH·SHADOW·CHALLENGER·ACTIVE·QUARANTINED·RETIRED, 평가주기, minimum sample, 비용후 OOS, manual lock, 원자 champion 교체와 rollback을 구현했다. 자동 변경은 source·임계값을 생성하거나 수정하지 않는다. |

### 사전등록 저장 공개시장 연구

13개 저장 Run의 manifest 2,690,582 events를 입력으로 사용했고, 실제 처리된 2,232,327 events에서 19,020개 신호를 만들었다. 5개 전략계열 × ORIGINAL·MIRROR·REVERSE × horizon·parameter 조합으로 180개 key를 사전등록했다. train 12,647, validation 2,423, OOS 3,456 outcome을 시간순으로 나누고 horizon별 purge·embargo, walk-forward, PBO, DSR, deterministic bootstrap을 적용했다.

| 연구 항목 | 상태 | 결과 |
|---|---|---|
| 선택 후보 | NOT_PROVEN | `MICRO_SCALP:30:ABSORPTION_REFILL_REVERSE:ORIGINAL`의 OOS BASE는 2건·기대값 -4.893bp·PF 0.554, STRESS는 -16.893bp·PF 0.004였다. |
| 과적합·강건성 | NOT_PROVEN | PBO 0.6286, DSR `INSUFFICIENT_SAMPLE`, bootstrap 기대값 95% 구간 -21.936~12.150bp였다. |
| mirror parity | PASS | 비교 가능한 190쌍의 mismatch는 0이었다. |
| 승격 | NOT_PROVEN | 모든 promotion gate를 통과한 후보가 없어 Registry 변경은 0이다. 자연신호를 만들기 위해 기준을 낮추지 않았다. |
| 결정성 | PASS_WITH_LIMIT | 장중 연구 전체 반복은 dataset·parameter·result hash가 일치했다. 기존 A~J 연구는 1회 완료했으며 두 번째 전체 반복은 `NOT_RUN`; 표적 결정성 회귀검사는 PASS다. |

입력·파라미터·결과 hash는 각각 `5107f072ce2584f8a25c4cc0968a1681af63165ede27e85e047bc0cce17496dc`, `7b52995af94fdb811637abcde8b74a2bbe9b510ae38309008e045955d1f04a80`, `ba921ad8396a59e8298ca38a32cc3da1959b2e4941369025c33bec678b1d141e`이다. 상세 결과는 `evidence/WAVE34_INTRADAY_RESEARCH.json`, 기존 전략 결과는 `evidence/WAVE34_EXISTING_STRATEGY_RESEARCH.json`에 보존했다.

### 30분 실제 공개시장과 활성 원장

| 검증 | 상태 | 이번 실행의 실제 결과 |
|---|---|---|
| 격리 30분 soak | PASS | `soak-9d9cc1e8cbcf`, 실제 1,800초, 130,248 events, 계획 회전 1·전체 reconnect 1·비계획 reconnect 0, gap/resync/drop 0, queue 최대 12, 실행경로 p95 최대 62.467ms, critical incident 0이다. |
| 자원 | PASS | 메모리 111.359→255.500MB, 증가 143.407MB로 256MB 기준 안이다. 최종 event memory는 10,000 제한을 지켰다. |
| 활성 원장 전체검사 | PASS | 서비스를 평평한 상태로 내린 뒤 2.2GB 활성 SQLite의 `PRAGMA quick_check`는 `ok`, `foreign_key_check` 위반은 0이었다. 실제 545.70초를 기다려 완료했으며 부분검사를 전체검사로 쓰지 않았다. |
| 동시 host 고부하 | PASS_WITH_LIMIT | 기존 서비스와 전체 연구·soak가 동시에 경쟁한 계획 회전 1회에서 86.467초 critical lag가 발생했으나 자동 복구됐다. 격리 30분에서는 재현되지 않았다. |
| 6시간·24시간 | NOT_RUN | 실제 시간을 채우지 않았으므로 PASS로 기록하지 않는다. |

### 배포·재시작·실제 8870 화면

열린 main·League 포지션과 pending 0, 실제 주문·인증 false를 확인한 뒤 구현을 기존 서비스에 fast-forward 배포했다. 종료 중 persistence worker는 cancellation과 분리해 완료 결과를 회수했고 종료 로그에는 새 `CancelledError`, traceback 또는 ERROR가 없었다. 활성 원장의 마지막 미종료 Run을 읽어 `LIVE_SHADOW_PAPER`로 부팅하는 서비스 선택은 모든 읽기·schema 오류에서 READY로 fail-closed한다.

명시적 idempotency key로 `run-2b7135a972dd`를 새로 만들었다. 생성 직후 시작자산 1,000 USDT, main 손익·수수료·슬리피지·거래는 모두 0이었고, 같은 key의 재호출은 같은 operation과 Run을 반환했다.

수동 pause 뒤 서비스를 재시작해 같은 Run의 `MANUALLY_PAUSED`, `manual_pause_requested=true`가 유지되는 것을 확인했다. resume 뒤 다시 재시작했을 때 같은 Run이 `RUNNING`, `manual_pause_requested=false`로 복구됐다. supervisor 연결 성공과 fresh-book 재검증 어느 쪽도 사용자의 수동 pause를 자동 해제하지 못하도록 회귀검사와 실제 서비스를 함께 고쳤다.

실제 `http://127.0.0.1:8870/`에서 다음을 직접 눌렀다.

- 신규진입 pause·resume와 상태 제목·설명·버튼 전환.
- 1분·3분·5분·15분·30분·1시간·4시간, MA·RSI와 앱 전체화면 왕복.
- 전략 리그 10개, ACTIVE 1·SHADOW 5·OFF 4와 20개 BASE/STRESS 독립계좌.
- 전략 상세의 시간축·예상보유·반감기·TP1·TP2·SL·EDGE_DECAY·비용모델·Governor 근거.
- 거래기록 범위, 저장 Run 자동발견, 종목 timeline과 backend replay.
- 분석의 기대값·PF·Omega·Sortino·Calmar·비용·낙폭·turnover·표본상태, 설정의 공개시장·시간·저장·PAPER 안전.

실제 공개시장 자연 표본으로 `AGGRESSOR_FLOW_CONTINUATION_V1` BTCUSDT SHORT의 BASE·STRESS 진입, entry·TP1·TP2·SL, 수수료·슬리피지와 자동 종료를 화면에서 확인했다. BASE 보유시간은 27.488초, 순손익은 -0.63811580 USDT였고 STRESS도 별도로 기록됐다. 이는 1~2초 종료 재발이 없다는 한 표본과 종단 간 작동 증거일 뿐 수익성 증거가 아니다.

저장 Run replay는 checksum `b3fb6fa517866264fe72a74117ce3f340996dd0cfc9cc3e1e2410323a8bbc2c2`, 3,939 events, 전략평가 14,328회, 후보·main·shadow 거래 0, 실제 주문·인증 0으로 완료됐다. 저우선순위 replay 중에도 LIVE events는 18,746→32,087로 증가했고 최종 p95 28.255ms, queue·gap·drop·비계획 reconnect·저장오류 0이었다. replay의 기존 process isolation과 5% CPU budget은 유지했다.

최종 후속 관찰은 45,775 events, 실행호가 p50 21.311ms·p95 26.964ms, 체결 p95 55.051ms, queue·비계획 reconnect·gap·resync·drop·persistence fault·buffer drop 0, 실제 주문·인증 false였다. wide scanner p95 1,758.951ms는 진입용 정밀호가와 분리해 표시하며 실행 잠금 기준으로 사용하지 않는다.

### 전체 회귀와 남은 한계

| 검증 | 상태 | 이번 실행의 실제 결과 |
|---|---|---|
| backend pytest | PASS | 359 passed, 26.18초 |
| frontend Vitest | PASS | 12 files·51 tests |
| Ruff / mypy | PASS | 오류 0 / 91 source files 오류 0 |
| ESLint / TypeScript | PASS | 오류 0 / 오류 0 |
| production build / PAPER safety | PASS_WITH_WARNING | Vite 48 modules와 PAPER safety PASS. 단일 JS chunk 502.44kB 경고는 남아 있다. |
| Playwright | PASS | fixture 기반 실제 Chromium desktop·tablet·mobile 3 passed, root overflow·page/console error 0 |
| security / repository hygiene | PASS | 124 source, violation·secret-like·실제 주문 path 0 / 위반 0 |
| 공개시장 smoke | PASS | Binance eligible 527·catalog 701, Upbit KRW 286, WebSocket 16 events, credential·Authorization·실제 주문 0 |
| 실제 주문·private API·secret·wallet·런타임 AI | PASS | 모두 0이며 auth_required false다. |
| 전략 수익성 | NOT_PROVEN | 연구 후보 OOS와 현재 자연표본이 작거나 비용후 음수다. 30건 전에 순위를 매기지 않는다. |
| Release ZIP | NOT_RUN | 이번 Wave에서 새 배포 ZIP을 만들지 않았다. |
| GitHub main / Actions | PASS | 실행증거 commit `f571487cb8e998695de0c62d7caeed7857edddb3`을 main에 push했다. [Actions 32880481225](https://github.com/robom-labs/flowscalper/actions/runs/32880481225)의 validate 1분4초, browser 1분58초와 실제 Chromium desktop·tablet·mobile E2E·브라우저 증거 업로드가 모두 PASS했다. |

기계판독 통합 증거는 `evidence/WAVE34_FULL_AUDIT_QA.json`, soak는 `evidence/PHASE03_SOAK_30M.json`, 결정 근거는 ADR-038·ADR-039·ADR-040이다. 구현 기준 commit은 `fb15494c50413650f06ec2fbd936534bdcc78ceb`, 실행증거 기준 commit은 `f571487cb8e998695de0c62d7caeed7857edddb3`이다.

## 40. 계획 회전 depth warmup backlog 제거

### 재현과 원인

장시간 실제 서비스의 15분 계획 회전에서 임계지연 사건이 두 번 연속 99.325초와 98.882초 지속됐다. 계획 회전과 전체 reconnect 수는 일치했고 비계획 reconnect·sequence gap·resync·drop·persistence fault는 0이어서 연결 오류가 아니라 정상 교체 내부 경로를 추적했다.

새 depth WebSocket을 먼저 연 뒤 REST snapshot을 받는 동안 delta가 queue에 쌓였고, snapshot 뒤 이 오래된 backlog를 실행 가능한 top-of-book으로 모두 내보낸 것이 원인이었다. sequence는 맞지만 event-time이 오래된 호가가 실행 지연을 임계치 위로 올리고, 각 stale delta의 상위 20단계 계산도 처리시간을 늘렸다.

연결별 warmup 상태를 추가했다. 1,500ms보다 오래된 warmup delta는 호가장의 update id 연속성을 위해 적용하지만 실행 이벤트로 내보내지 않고 상위호가 계산도 생략한다. 첫 신선한 depth를 실제 전달할 때 warmup을 끝낸다. 그 전까지 계획교체의 기존 신규진입 안전잠금은 유지되므로 안전기준이나 전략 임계값을 낮춘 변경이 아니다. 결정 근거는 ADR-041이다.

### 실제 공개시장과 생산 주기 검증

| 검증 | 상태 | 이번 실행의 실제 결과 |
|---|---|---|
| 30초 단축 계획회전 | PASS | 실제 Binance 공개시장 75초, 전달 5,066 events, 계획회전·전체 reconnect 2·2, 비계획 reconnect 0, 임계지연 event·incident 0, p50 20.006ms·p95 22.286ms, queue·gap·resync·drop 0이다. |
| 생산 15분 계획회전 | PASS | 배포 뒤 같은 `run-2b7135a972dd`에서 생산 주기 2회, 146,510 events, 계획회전·전체 reconnect 2·2, 비계획 reconnect 0, 임계지연 event·incident 0, 실행 p50 21.307ms·p95 39.409ms, 체결 p95 74.077ms, queue·gap·resync·drop·fault·buffer drop 0, entry lock false다. |
| 서비스 안전 | PASS | RUNNING·LIVE, 10전략·20계좌, 열린 main·League 포지션 0, 실제 주문 false·인증 false, 메모리 288.141MB다. |
| 실제 앱 내 브라우저 | PASS | 설정→시스템을 직접 열어 `작동 중`, 시장데이터 정상, 50/12종목, 실제 호가/체결 36/83ms, 비정상 재연결/누락 0/0, 정상 연결 교체 2회, 실제 주문 경로 0을 확인했다. console 오류·경고는 0건이다. |

wide scanner p95 1,860.858ms는 실행용 정밀호가 p95 39.409ms와 분리된 넓은 관찰 수치다. 신규 PAPER 진입 안전판정은 실제 실행호가 경로를 사용한다.

### 회귀검사와 남은 한계

| 검증 | 상태 | 실제 결과 |
|---|---|---|
| warmup 표적 회귀 | PASS | supervisor 20 passed. stale delta는 update id를 전진시키되 이벤트를 내보내지 않고 다음 fresh delta는 정상 전달된다. |
| backend pytest | PASS | 360 passed, 48.97초 |
| frontend Vitest | PASS | 12 files·51 tests |
| Ruff / mypy | PASS | 오류 0 / backend/app 91 source files 오류 0 |
| ESLint / TypeScript | PASS | 오류 0 / 오류 0 |
| production build / PAPER safety | PASS_WITH_WARNING | Vite 48 modules·PAPER 불변조건 PASS, 기존 단일 JS chunk 502.44kB 경고 유지 |
| security / repository hygiene | PASS | 124 source·violation/secret-like/실제주문 path 0 / PASS |
| Playwright | PASS | desktop·tablet·mobile 3 passed, fixture 15 passed, 실제 Chromium 화면 갱신 |
| 활성 원장 full quick_check | NOT_RERUN | 같은 2.2GB 원장의 Wave34 전수검사는 `2026-08-25T17:57:48Z`에 quick_check `ok`·FK 0·545.7초로 PASS했다. 이 회전 결함과 무관한 전수검사를 작동 중 writer에 반복하지 않았고 현재 fault·buffer drop은 0이다. |
| 수익성 | NOT_PROVEN | 전략·비용·진입 기준은 변경하지 않았고 현재 표본으로 순위나 수익성을 주장하지 않는다. |
| 6시간 / 24시간 soak | NOT_RUN | 수정 배포 뒤 실제 시간을 채우지 않았다. |
| Release ZIP | NOT_RUN | 이번 Wave에서 만들지 않았다. |
| GitHub main / Actions | PASS | 구현 commit `8bcfde29da42e4f066a225f64ff6c98f85d4c009`과 실행증거 commit `e8bbc22c4b0dfaa8051efdd448a6861c32687354`을 main에 push했다. [Actions 32906261858](https://github.com/robom-labs/flowscalper/actions/runs/32906261858)의 validate 1분7초, browser 1분20초와 실제 Chromium desktop·tablet·mobile E2E·증거업로드가 모두 PASS했다. |

기계판독 증거는 `evidence/WAVE35_ROTATION_WARMUP_QA.json`, 결정 근거는 ADR-041이다. 구현 기준 commit은 `8bcfde29da42e4f066a225f64ff6c98f85d4c009`, 실행증거 기준 commit은 `e8bbc22c4b0dfaa8051efdd448a6861c32687354`이다. 이 Wave는 재현한 정상 계획교체의 stale backlog 결함을 해결한 것이며, 모든 미래 네트워크 상태·6시간·24시간 안정성이나 전략 수익성을 입증한 것은 아니다.

## 41. 거래기록 가시성·replay 비차단 미리보기

### 재현과 원인

실제 `http://127.0.0.1:8870/`의 거래기록 기본 화면은 `이번 Run·공동 PAPER·현재 버전`을 선택해 빈 표를 표시했다. 현재 Run의 공동계좌 완료 거래는 실제로 0건이지만 독립 전략계좌에는 22건이 있었다. 화면은 확장 범위를 읽는 동안에도 빈 배열을 표시하고 로딩 문구가 없어 데이터가 없는 것처럼 보였다.

확장 거래기록 API는 활성 2.3GB 원장의 main·전략리그 거래를 매번 다시 검증해 전체 범위에 6.24초가 걸렸다. 과거 재생 화면은 Run 목록을 연 직후 선택 종목의 checksum 검증 이벤트 2,000개를 자동으로 읽어 현재 대형 Run에서 37.52초가 걸렸고, 그동안 선택기와 차트가 잠겼다.

LIVE 거래기록을 시작 때 checksum 검증한 전체 main·전략리그 cache와 현재 메모리 완료거래의 고유 ID 병합으로 바꿨다. 기본 계좌 범위는 전체로 바꾸고 로딩·실패·진짜 0건, 전체·공동·전략별 건수를 분리했다. replay는 종목통계와 최근 1초 candle 500개만 읽는 빠른 미리보기, checksum 검증 정밀 이벤트, 동일 조건 전략 재검증의 세 단계로 나눴다. Run 변경 순간 이전 종목과 timeline을 지워 교차 Run 경합도 차단했다. 결정 근거는 ADR-042다.

후속 재시작 검증에서 replay Run 요약이 저장된 전략 거래 26건과 복구 메모리 거래 28건을 더해 54건으로 표시하는 중복을 발견했다. 현재 Run 거래 수를 거래기록 cache의 거래 ID 병합 결과로 계산하도록 고쳐 거래기록 28건과 replay 요약 28건을 일치시켰다. 이 수치는 거래 자체를 추가하거나 삭제한 결과가 아니라 표시 계약을 바로잡은 결과다.

### 실제 거래와 전략 상태

현재 `run-2b7135a972dd`는 2026-08-26 02:30:06 KST에 시작했으므로 전날 밤 전체를 실행한 Run이 아니다. 최초 감사 시점 저장 이벤트는 1,419,273건, main 거래 0건, 전략계좌 거래 22건이었다. 후속 수정과 안전 재시작 뒤 실제 브라우저 감사에서는 저장 이벤트 1,492,118건, main 0건, 전략계좌 28건으로 늘었다. 28행은 자연 후보 14개를 BASE와 STRESS가 독립 체결한 결과다.

후속 28행은 LSA 6, VWAP 8, Queue 6, Aggressor 4, Depth-adjusted OFI 2, OFI pullback 2건이다. 보유시간은 14.044~85.622초이고 3초 미만 종료는 0건이다. 종료는 EDGE_DECAY 26, PROFIT_PROTECTION 1, STOP 1건이며 비용후 합계는 -25.3148 USDT다. 따라서 수익성은 `NOT_PROVEN`이며 거래를 늘리려고 전략 임계값·비용·손익비를 낮추거나 ACTIVE를 바꾸지 않았다.

공동계좌 0건은 현재 유일한 ACTIVE인 CBR이 최근 24개 경로에서 압축·돌파·눌림·유동성 회복·OFI 재가속 조건을 동시에 충족하지 못했기 때문이다. 나머지 전략도 각 24개 경로를 평가하고 명시적 구조·flow·지속성 이유로 대기했다. 공개시장 event, 50 wide·12 deep, 전략평가와 저장이 멈춘 상태는 아니었다.

### 성능과 실제 브라우저 버튼

| 검증 | 상태 | 이번 실행의 실제 결과 |
|---|---|---|
| 거래기록 API | PASS | 최초 cache 적용 뒤 22.8~34.3ms였지만 저장 checkpoint와 겹친 후속 요청이 15~20초 제한을 넘겨 Run 요약 직접 조회를 추가 제거했다. 재시작 뒤 12회는 모두 HTTP 200·9.8~30.8ms였고 현재 공동 0·전략별 28건을 반환했다. |
| replay 목록·미리보기 | PASS | 저장 Run 79개 목록은 후속 12회 2.5~19.2ms였다. 현재 Run 최근 candle 500개 미리보기 5회는 14.2~21.1ms였고 archive event 본문 0개를 읽었다. |
| 실제 거래기록 화면 | PASS | `기록`을 직접 눌러 기본 `모든 PAPER 계좌`, `표시 28건 · 공동계좌 0건 · 전략별 계좌 28건`, 거래별 수수료·슬리피지·순손익·14초 이상 보유시간을 확인했다. |
| 실제 replay 화면 | PASS | `과거 재생`을 직접 눌러 현재 대형 Run의 최근 candle 500개가 먼저 표시되고 정밀 이벤트와 전략 검증 버튼이 분리된 것을 확인했다. |
| Run 변경 경합 | PASS | 소형 `demo-7f9159e59d01`로 변경한 직후 이전 ZECUSDT가 남지 않고 ADAUSDT preview가 준비된 뒤 버튼이 열렸다. alert는 0이었다. |
| 정밀 이벤트 | PASS | 버튼을 눌러 ADAUSDT 저장 이벤트 24개를 631ms에 checksum 검증해 열었고 `전략 평가 실행 전` 문구를 확인했다. |
| 동일 조건 전략 검증 | PASS | 버튼을 눌러 1.947초에 `replay-f8a8036c38ef4fcc`, checksum `66e3adca53e2013226f0408d16f4662346f0f1fe65b540e207e98d2a573eed97`을 만들었다. 후보·main·전략별 거래 0, 실제 주문·인증 경로 0이다. |
| replay 거래 수 일치 | PASS | 최종 서비스 재시작 뒤 거래기록은 공동 0·전략별 28건, 현재 Run replay 요약은 main 0·shadow 28건이었다. 저장·복구 중복을 회귀검사로 고정했다. |
| 최종 cache 응답성 | PASS_WITH_LIMIT | 실제 서비스 12회에서 거래기록은 8.3~209.2ms, Run 목록은 3.0~5.4ms였고 모두 HTTP 200이었다. 거래기록 한 표본 209.2ms는 나머지 11개보다 높아 장기 분포를 계속 관찰한다. |
| 현재 대형 Run 전략 재검증 | IN_PROGRESS_AT_CUTOFF | 현재 ZECUSDT 정밀 이벤트 2,000개 로딩은 완료됐다. 버튼은 저장된 약 11.6만 ZECUSDT 이벤트 전체를 5% CPU 예산으로 처리해 단기 화면검증 시간 안에 끝나지 않았으며, 멈춤으로 오인하지 않도록 `전략 검증 중`을 표시했다. 소형 Run 종단간 PASS를 대형 Run 완료 증거로 대체하지 않는다. |

### 공개시장 후속 관찰과 제한

전체 테스트·build 부하와 겹친 실제 저장에서 원장 커밋 최대 14.261초, checkpoint 6.604초와 임계지연 1건이 발생해 시스템이 fail-closed `SAFETY_WAITING`으로 들어갔다. 데이터 관찰은 계속됐고 queue·gap·drop·저장 fault는 0이었다. 수동 해제 없이 86.301초 뒤 `RUNNING`으로 자동 복구됐다. 이를 오류 0으로 숨기지 않고 `PASS_WITH_LIMIT`로 기록한다.

복구 뒤 30초·7표본에서 event 37,086→39,149로 2,063건 전진, p95 46.245~162.458ms, 임계지연 추가 0, queue 최대 12 뒤 0, gap·drop·fault·buffer drop·진입잠금 0, 실제 주문·인증 false였다. 짧은 표본이므로 6시간·24시간 안정성을 뜻하지 않는다.

### 전체 회귀와 남은 한계

| 검증 | 상태 | 실제 결과 |
|---|---|---|
| backend pytest | PASS | 362 passed, 27.32초. replay 현재 Run 거래 수 중복 회귀검사를 포함한다. |
| frontend Vitest | PASS | 13 files·53 tests |
| Ruff / mypy | PASS | 오류 0 / 91 source files 오류 0 |
| ESLint / TypeScript | PASS | 오류 0 / 오류 0 |
| production build / PAPER safety | PASS_WITH_WARNING | Vite 48 modules와 PAPER 불변조건 PASS. 단일 JS chunk 505.00kB 경고는 남아 있다. |
| fixture / Playwright | PASS | fixture 15 passed, 실제 Chromium desktop·tablet·mobile 3 passed |
| security / repository hygiene | PASS | 124 source·violation/secret-like/실제 주문 path 0 / 위반 0 |
| 활성 원장 full quick_check | NOT_RERUN | Wave34의 같은 활성 원장 full quick_check `ok`·FK 0 뒤 이번 표시·조회 수정에서는 작동 중 writer를 멈추는 전수검사를 반복하지 않았다. 현재 persistence fault·buffer drop은 0이다. |
| 전략 수익성 | NOT_PROVEN | 현재 Run 14개 자연후보·28개 BASE/STRESS 행의 비용후 합계는 -25.3148 USDT이며 30건 미만이다. 기준과 Registry를 변경하지 않았다. |
| 6시간 / 24시간 soak | NOT_RUN | 수정 뒤 실제 시간을 채우지 않았다. |
| Release ZIP | NOT_RUN | 이번 Wave에서 만들지 않았다. |
| GitHub main / Actions | PASS | 1차 구현 `f06448632be74795abab9d0262bd89361cbd7630`의 [Actions 32909772325](https://github.com/robom-labs/flowscalper/actions/runs/32909772325), checkpoint 경합 후속 구현 `ba9723135a686c40bea54980f669ac054cbc8a03`의 [Actions 32910918615](https://github.com/robom-labs/flowscalper/actions/runs/32910918615), 최종 거래 수 교정 `1a088acb63e2ef41c592d7a69421e6edd4cbbb64`의 [Actions 32912271959](https://github.com/robom-labs/flowscalper/actions/runs/32912271959), 증거 commit `40ea7ec28907882ae04c6252ed6533310eaf4b7f`의 [Actions 32912523249](https://github.com/robom-labs/flowscalper/actions/runs/32912523249)이 모두 PASS다. 구현 Actions는 validate 1분5초·browser 1분21초, 증거 Actions는 validate 1분9초·browser 1분12초였고 모두 Chromium desktop·tablet·mobile E2E와 브라우저 증거 업로드를 통과했다. |

기계판독 증거는 `evidence/WAVE36_HISTORY_REPLAY_VISIBILITY_QA.json`, 결정 근거는 ADR-042다. GitHub main은 최종 구현 `1a088acb63e2ef41c592d7a69421e6edd4cbbb64`을 포함하며 구현 commit과 후속 증거 commit의 독립 Actions가 모두 PASS했다. 이번 PASS는 표시·조회·replay 분리·회귀·짧은 실제 서비스 범위이며, 전략 수익성·현재 대형 Run 전체 전략 재검증·6시간·24시간·Release ZIP은 각각 `NOT_PROVEN`, `IN_PROGRESS_AT_CUTOFF` 또는 `NOT_RUN`으로 유지한다.

## 42. 거래기록·저장 replay 관찰성과 취소

### 재현과 원인

초기 실제 API에는 현재 전략 버전 거래 33건이 있었지만 화면의 범위와 전략 버전 hash 문구 때문에 거래가 없는 것처럼 보였다. 전략 재검증은 동기 HTTP 요청이어서 진행 상태·경과시간·취소가 없었다. 현재 ZECUSDT의 약 13만건 전체 검증은 25분 이상 계속되며 실행경로 p95를 2.68초까지 올리고 신규 진입 안전잠금을 작동시켰다. 포지션·pending이 0인 것을 확인한 뒤 서비스를 안전 재시작해 해당 작업을 종료했다.

정밀 timeline은 2,000건을 요청해도 활성 SQLite 이벤트와 종목의 전체 1초 candle 23,000개 이상을 읽었다. 실측 2,000건은 27.609초·5,038,206 bytes, 250건은 35.805초·3,805,542 bytes였다. 이벤트 구간 candle만 읽도록 고친 후 250건은 11.632초·184,527 bytes로 줄었고, 최종 화면용 100건은 0.628초·75,334 bytes·candle 17개였다. 전체 전략 검증은 화면 100건 상한과 무관하게 저장 이벤트 전체를 사용한다.

### 수정과 실제 버튼 검증

- `ReplayOperationManager`는 요청·준비·처리·완료·재시도 가능 실패·차단 실패·취소 중·취소 상태와 4시간 timeout을 관리한다. 동일 범위 중복은 멱등적이고 다른 범위는 `REPLAY_BUSY`다.
- 과거 재생 화면은 Run 목록과 최근 candle를 먼저 띄우고 전략 결과 전체 목록은 백그라운드에서 읽는다. 활성 replay는 새로고침 후에도 다시 표시한다.
- 실제 데스크톱에서 `기록`을 눌렀을 때 37건·공동 1건·전략별 36건, 전부 `LIVE_PUBLIC`, 최단 14.044초·3초 미만 0건을 확인했다. 테스트를 마친 뒤 2026-08-26T00:59:49Z 최종 API 재조회에서는 자연 거래가 더 종료되어 39건·공동 1건·전략별 38건으로 전진했다. 내부 버전 hash는 `현재 전략 버전`으로 간소화됐다.
- 실제 소형 Run `run-c74c67ff5976`의 ETHUSDT 125건 전체를 2.443초에 처리했다. `replay-a0a95fa1bf62475a`, checksum `636bf7f3162147d3db559a0080660d44db9e9551d6df330378899dadd243bf1a`, 전략평가 288회, 적격·후보·main·shadow 거래 0, 실제 주문·인증 0이다.
- 실제 모바일 390×844에서 대형 현재 Run 전략검증 패널이 354ms 만에 떴고, `전략 검증 취소`를 누르면 `REQUESTED`→`PREPARING`→`PROCESSING`→`CANCELLING`→`CANCELLED`로 종료됐다. 태블릿 820×1180과 원래 데스크톱에서도 화면과 버튼을 확인했고 console error·warning은 0건이다.

### 현재 거래와 전략 판정

2026-08-26T00:59:49Z 최종 현재 Run 버전 행은 39건이고 전략리그 자연 후보는 BASE 19건과 동일 후보의 STRESS 19건, 공동계좌 1건이다. 전략별 BASE 표본은 0~5건이며 모두 `표본 부족`이다. 39건은 모두 비용후 손실이었고 총손익 1.54016000, 수수료 38.563222662, 슬리피지 1.92339004, 순손익 -38.946452702 USDT다. 종료는 EDGE_DECAY 37, STOP 1, PROFIT_PROTECTION 1건이다.

이 수치는 수익성을 입증하지 못하며 오히려 비용 부담을 명확히 보여 준다. 전략 기준·비용·손익비·TP/SL·Governor를 임의로 낮추지 않았다. 현재 B만 공동계좌 `ACTIVE`이고 나머지 9개는 독립 `SHADOW`이며, 10개 전부 LONG·SHORT 평가가 켜져 있다. 현재 수익성은 `NOT_PROVEN`이고 30건 미만 전략 순위는 매기지 않는다.

### 전체 회귀와 남은 한계

| 검증 | 상태 | 이번 실행의 결과 |
|---|---|---|
| backend pytest | PASS | 366 passed, 31.94초 |
| frontend Vitest | PASS | 13 files·54 tests |
| Ruff / mypy | PASS | 오류 0 / 92 source files 오류 0 |
| ESLint / TypeScript | PASS | 오류 0 / 오류 0 |
| production build / PAPER safety | PASS_WITH_WARNING | Vite 48 modules·PAPER 불변조건 PASS. 단일 JS chunk 507.74kB 경고는 남아 있다. |
| fixture / Playwright | PASS | fixture 15 passed, Chromium desktop·tablet·mobile 3 passed |
| security / repository hygiene | PASS | 125 source·violation/secret-like/실제주문 path 0 / 위반 0 |
| 실제 서비스 | PASS | RUNNING·LIVE·PAPER, 실행 p50/p95 19.114/29.847ms, queue·비계획 reconnect·gap·resync·drop·fault·buffer drop 0, entry lock false, 실제주문·인증 false다. |
| 활성 원장 full quick_check | NOT_RERUN | 활성 2.55GB writer와 동시 전수검사를 강행하지 않았다. Wave34의 같은 원장 전수 PASS를 이번 PASS로 쓰지 않는다. |
| 6시간 / 24시간 soak | NOT_RUN | 수정 후 실제 시간을 채우지 않았다. |
| Release ZIP | NOT_RUN | 이번 Wave에서 새 ZIP을 만들지 않았다. |
| GitHub main / Actions | PASS_WITH_REPAIR | 구현 commit `4bc02cfe2b60dd114068a28207653b244558e4f1`을 main에 올린 첫 [Actions 32917358890](https://github.com/robom-labs/flowscalper/actions/runs/32917358890)은 LIVE 분리 replay 단위검사가 CI 공개망에서 대체 거래소로 전환되며 준비한 Run ID와 달라져 FAIL했다. 실제 네트워크가 목적이 아닌 단위검사에서 supervisor 시작을 제거한 `bc113522d9c4115f5732cc1d706b4590c3de6ce9`을 main에 추가했고, [Actions 32917820261](https://github.com/robom-labs/flowscalper/actions/runs/32917820261)의 validate 1분10초·browser 1분4초·브라우저 증거 업로드가 모두 PASS했다. |

최종 GitHub 반영 뒤 실제 서비스는 3초 동안 dashboard event 104,288→104,518로 230건 전진했고, LIVE·PAPER·실행 p95 42.887ms·queue 0·entry lock false·저장 fault와 buffer drop 0·실제주문과 인증 false를 유지했다. 현재 거래기록은 39건을 유지했고 마지막 대형 replay operation은 `CANCELLED`였다.

기계판독 증거는 `evidence/WAVE37_OBSERVABLE_REPLAY_QA.json`, 결정 근거는 ADR-043이다. GitHub main의 최종 구현 기준은 `bc113522d9c4115f5732cc1d706b4590c3de6ce9`이다. 이 Wave는 기록·재생 표시와 취소·응답성을 검증한 것이며 전략 수익성·장시간 안정성을 입증한 것은 아니다.

## 43. PAPER 진입 의도 분리와 기록·재생 시작 병목 제거

### 재현과 원인

사용자가 누른 신규진입 일시정지와 자동 안전잠금이 같은 `paused` 표현에 의존해, 자동 안전대기 중 재개 버튼의 의미와 재시작 복구 결과가 모호했다. 오래된 화면의 요청과 중복 요청을 구분할 revision·idempotency 계약도 없었다.

실제 2.57GB 활성 SQLite 원장으로 서비스를 재시작했을 때 내부 시작은 165.615초였고, HTTP 포트가 열리기 전 현재 전략 거래 cache 동기 구축만 142.831초가 걸렸다. 그래서 저장 거래가 있어도 사이트가 오랫동안 열리지 않아 전체 기록이 사라진 것처럼 보일 수 있었다. `/api/replay/results`도 저장된 53개 replay 결과의 전체 결정경로를 한 번에 반환해 2,680,397 bytes가 되었고 첫 요청이 10초를 넘었다.

### 수정

- 사용자 PAPER 신규진입 의도를 `ENTRY_ENABLED`·`ENTRY_PAUSED`와 revision으로 분리했다. pause·resume은 expected revision CAS와 `Idempotency-Key`를 사용하고 actor·reason·timestamp를 `PAPER_ENTRY_INTENT_TRANSITION`으로 불변 감사한다.
- 자동 안전잠금 중에는 사용자 의도가 허용이어도 런타임을 계속 정지하고, 실제 화면은 재개 버튼을 비활성화해 자동 복구와 사용자 제어를 혼동하지 않게 했다.
- 같은 Run 재시작과 자동 venue 전환은 의도와 revision을 보존하며, Fresh Run에서만 의도를 초기화한다.
- 기존 미종료 LIVE Run의 거래 cache는 HTTP 시작 이후 background에서 준비한다. 시작 전에는 안전 복구만 수행하고 검증되지 않은 거래통계는 노출하지 않는다.
- replay 목록은 writer lock과 분리된 query-only 연결을 사용한다. source Run마다 최신 replay 하나만 기본 결과로 반환하고 API 결정경로는 최근 20개로 제한하되 SQLite 전체 결과와 원본 이벤트는 보존한다.

### 실제 제어·재시작 검증

실제 브라우저에서 `신규진입 일시정지`와 `신규진입 재개`를 차례로 눌러 revision 0→1→2와 불변 전환 2건을 확인했다. 이후 서비스를 여러 번 재시작해도 같은 `run-2b7135a972dd`, `ENTRY_ENABLED`, revision 2, actor `USER_UI`, reason `USER_RESUME`이 복구됐다.

최종 실제 재시작은 LaunchAgent kickstart부터 HTTP 응답까지 10.180초, 내부 시작 3.651초였고 동기 거래 cache 시간은 0이었다. HTTP 시작 후 background cache는 0.903초에 완료됐다. 이전의 내부 165.615초와 비교하면 사용자가 화면을 기다리는 주된 동기 병목이 제거됐다. 이 비교에는 같은 원장의 디스크 cache 온도와 새 index 영향도 포함될 수 있으므로 순수 코드 미세벤치마크로 해석하지 않는다.

### 실제 기록·재생 화면 검증

- `기록` 화면 기본 전체 PAPER 계좌에서 현재 전략 버전 43건·공동계좌 1건·전략별 계좌 42건을 확인했다. API 응답은 9.068ms였다.
- 보유시간은 최소 14.044초, 중앙 25.962초, 최대 85.622초였고 3초 미만 종료는 0건이라 1~2초 종료 재발은 없었다.
- `과거 재생` 화면에서 저장 Run 79개와 선택한 현재 Run의 2,135,559 events를 확인했다. Run 목록 API는 3.811ms였다.
- source Run별 최신 결과는 16개·33,397 bytes·결정경로 최대 20개였고 첫 응답 84.871ms, 반복 응답 2.082ms였다. 수정 전 전체 53개 결과는 2,680,397 bytes였다.
- 실제 `정밀 이벤트 불러오기`는 현재 대형 Run의 100 events를 최초 약 14.7초에 표시했고, 반복 로딩은 약 0.9초였다. `재생`을 눌러 cursor 1→12 전진 뒤 `일시정지`도 확인했다. 최초 cold read는 아직 `PASS_WITH_LIMIT`다.
- 실제 브라우저 console error·warning은 0건이고 화면은 RUNNING·LIVE·PAPER·실제주문 0·인증 0을 유지했다.

### 60초 실제 LIVE 표본

13회 표본의 60.08초 동안 event는 4,291건 전진했다. HTTP 응답 최대 127.29ms, 실행경로 p95 최대 36.001ms, 거래 지연 p95 최대 54.582ms, 관찰용 wide 지연 p95 최대 1,781.769ms, queue 최대 5였다. 비계획 reconnect·gap·resync·drop·persistence fault·buffer drop은 모두 0이고 최종 신규진입 잠금은 false였다. wide 지연은 진입 실행경로 지연과 분리해 기록한다.

### 거래량과 전략 판정

43개 행은 자연 BASE 후보 21건의 BASE·STRESS 독립계좌 42건과 공동계좌 1건이다. 전략별 BASE 표본은 LSA 5, CBR 1, VWAP 4, OFI 눌림 1, Queue imbalance 6, Aggressor impulse 2, Multilevel pressure 0, Depth-adjusted OFI 2, OFI return divergence 0, Book slope exhaustion 0건이며 모두 승리 0건이다. 전체 43행 비용후 순손익은 -45.28840655 USDT다.

현재 저장된 사용자 설정은 CBR만 `ACTIVE`, 나머지 9개는 독립 `SHADOW`이고 10전략·20계좌의 LONG·SHORT 평가는 유지된다. 거래가 적거나 승률이 낮다는 이유로 전략 임계값·비용·TP/SL을 낮추거나 모든 전략을 공동계좌 ACTIVE로 바꾸지 않았다. 전략별 표본은 최대 6건이므로 순위와 수익성은 `NOT_PROVEN`이다.

### 전체 회귀와 남은 한계

| 검증 | 상태 | 이번 실행의 결과 |
|---|---|---|
| backend pytest | PASS | 371 passed, 47.16초 |
| frontend Vitest | PASS | 13 files·54 tests |
| Ruff / mypy | PASS | 오류 0 / 92 source files 오류 0 |
| ESLint / TypeScript | PASS | 오류 0 / 오류 0 |
| production build / PAPER safety | PASS_WITH_WARNING | build와 PAPER 불변조건 PASS. 단일 JS chunk 508.72kB 경고는 남아 있다. |
| fixture / Playwright | PASS | fixture 17 passed, Chromium desktop·tablet·mobile 3 passed |
| security / repository hygiene | PASS | 125 source·violation/secret-like/실제주문 path 0 / 위반 0 |
| 실제 서비스·브라우저 | PASS_WITH_LIMIT | RUNNING·LIVE·PAPER, 기록 43건·replay Run 79개·재생 cursor 전진·console 오류 0. 대형 Run 최초 정밀 100건 14.7초는 제한으로 남긴다. |
| 활성 원장 full quick_check | NOT_RUN | 실제 writer와 동시에 시도했으나 10초 이상 진행되어 운영 경합을 피하려고 중단했다. 과거 Wave의 결과를 이번 PASS로 재사용하지 않는다. |
| 전략 수익성 | NOT_PROVEN | 자연 BASE 표본 0~6건, 전체 43행 승리 0·순손익 -45.28840655 USDT다. |
| 6시간 / 24시간 soak | NOT_RUN | 수정 후 실제 시간을 채우지 않았다. |
| Release ZIP | NOT_RUN | 이번 Wave에서 새 ZIP을 만들지 않았다. |
| GitHub main / Actions | PASS | 구현 commit `1b934140258d06ad86f551232f877488815bdb58`을 main에 반영했고 [Actions 32922393330](https://github.com/robom-labs/flowscalper/actions/runs/32922393330)의 validate 58초·browser 1분11초·브라우저 증거 upload가 모두 PASS했다. |

기계판독 증거는 `evidence/WAVE38_ENTRY_INTENT_HISTORY_REPLAY_QA.json`, 결정 근거는 ADR-044다. GitHub main의 최종 구현 기준은 `1b934140258d06ad86f551232f877488815bdb58`이고 구현 Actions도 PASS했다. 이번 PASS는 사용자 의도 감사·서비스 시작·기록·replay 기본 조회·짧은 LIVE·실제 브라우저 범위이며 전략 수익성·활성 원장 전수검사·6시간·24시간 안정성을 뜻하지 않는다.

## 44. 비용인식 전략정책·시간봉 SHADOW·거래 상세 재생 복구

### 실제 재현과 원인

현재 Run의 거래 기록은 실제로 존재했지만 거래 행의 `재생`을 누르면 불변 원장과 공개시장 archive로 세션을 완성한 뒤 선택적 `replay_focus_cache`를 기록하는 단계에서 `sqlite3.OperationalError: database is locked`가 발생해 HTTP 500을 반환했다. 활성 out-of-process durable writer와 cache 쓰기가 경합했으며 원본 거래가 없는 문제가 아니었다.

최종 현재 전략버전 화면은 63행, 공동계좌 1행, 독립계좌 62행을 표시했다. 독립 62행 중 비용후 양수는 1행, 합계 순손익은 -64.6068400286 USDT였다. 종료는 EDGE_DECAY 56, PROFIT_PROTECTION 3, STOP 3건이다. 보유는 최소 3.046초, 중앙 23.842초, p90 51.390초, 최대 85.622초이며 3초 미만은 0건이다. 즉 과거 1~2초 일반 EDGE_DECAY 재발은 없고 3.046초 최단 거래는 실제 stop 도달이다.

### 수정

- 거래 상세 세션은 불변 원장과 checksum 검증 공개시장 자료로 먼저 완성한다. 선택적 cache 쓰기의 SQLite lock·busy는 cache miss로 처리해 완성 세션을 반환하며 다른 무결성·스키마·직렬화 오류는 계속 실패시킨다.
- 거래 집중 화면은 실패를 빈 데이터처럼 보이지 않게 명시적 오류와 `거래 차트 다시 시도`를 제공한다.
- 모든 포지션을 900초에 `EMERGENCY_STALE`로 끝내던 전역 상한을 계획별 `maximum_holding_ms`와 `MAX_HOLD` 종료로 분리했다. 복구 payload와 trade schema도 최대보유를 보존한다. 데이터 공백 비상종료는 별도 fail-closed 정책으로 유지한다.
- 비용후 반복 실패한 A/D/E/H는 RETIRED·OFF로 잠그고 mode·방향 재활성화 버튼을 비활성화했다. 소스, 과거 거래, BASE·STRESS 계좌는 삭제하지 않았다. B만 ACTIVE, C/F/G/I/J는 SHADOW다.
- 완성 공개 1시간봉 전략 K `HOURLY_MOMENTUM_BREAKOUT_V1`을 SHADOW로 추가했다. 200봉 이상, EMA20/50·EMA80/200 방향과 EMA80 기울기, 24시간 모멘텀 2%, Donchian20 돌파, ADX 20, 상대거래량 1.1을 모두 요구한다. 새 봉 뒤 5초 안의 실제 bid·ask로만 계획하며 TP1 2.2R·40%, TP2 4.5R·60%, 최대 36시간을 고정한다.
- recovery schema 3은 완전히 새 strategy ID의 BASE·STRESS 두 계좌만 strict additive extension으로 허용하고 persisted order·trade ID를 복구 전에 준비한다. 기존 strategy profile 일부 누락은 계속 fail-closed한다.

### 공개시장 연구와 정직한 판정

Wave 39는 Binance USDⓈ-M 12종목의 완성 5분봉 414,720개와 사전등록 후보 6개를 평가했으나 BASE·STRESS가 모두 음수였고 PBO 0.6286이라 선택하지 않았다. Wave 41 시간봉 후보는 진단 OOS 42건에서 BASE +32.212bp·PF 1.346, STRESS +20.212bp·PF 1.202였지만 bootstrap 95% 하한 -48.537bp, DSR 0, PBO 0.3714였다. 후보 선택과 완전히 독립된 미래 OOS도 없다. 따라서 K는 미래 자연 `LIVE_PUBLIC` 표본을 모으는 SHADOW 가설이며 수익성은 `NOT_PROVEN`이다.

### 실제 브라우저와 서비스

- 실제 8870 전략 화면에서 11행, 7개 감시, 4개 퇴역, 문제 0, 실제 주문 0을 확인했다. 퇴역 mode·방향 버튼은 비활성화돼 있었다.
- K 상세에서 `INTRADAY_SWING`, 예상 1시간~36시간, 신호 5초, TP1 2.2R, TP2 4.5R, 안전 최대 36시간, 한국어 진입·종료 규칙, `아직 수익성이 입증되지 않은 독립 PAPER 검증 전략`을 확인했다.
- 실제 XRPUSDT STRESS 거래 재생은 8프레임·7캔들을 200으로 반환했다. `진입`에서 entry 1.4437, SL 1.4553, TP1 1.4295, TP2 1.4068과 차트의 PAPER 하락 진입을 확인했고 `실제 종료`에서 exit 1.4424, gross +0.4121, fee 1.098, slippage 0.0317, net -0.7175 USDT, 보유 23초, EDGE_DECAY를 확인했다.
- 최종 flat 상태에서 정확한 최종 소스로 서비스를 다시 시작해 같은 `run-2b7135a972dd`를 복구했고 HTTP ready는 25초였다. 이후 event 12,403, 실행 p50/p95 22.141/43.724ms, trade p95 56.082ms, critical incident·queue·비계획 reconnect·gap·resync·drop·persistence fault·buffer drop 0, entry lock false였다. wide 관찰 p95 1,823.049ms는 진입 실행경로와 분리한다.
- K의 공개 완성 1시간봉은 12종목 모두 499개로 준비됐다. 실제 주문과 인증은 false다.

### 전체 검증과 한계

| 검증 | 상태 | 이번 실행의 결과 |
|---|---|---|
| backend pytest | PASS | 최종 소스 재실행 393 passed, 24.00초 |
| frontend Vitest | PASS | 13 files·55 tests |
| Ruff / mypy | PASS | 오류 0 / 93 source files 오류 0 |
| ESLint / TypeScript | PASS | 오류 0 / 오류 0 |
| production build / PAPER safety | PASS_WITH_WARNING | build와 PAPER 불변조건 PASS. 단일 JS chunk 512.52kB 경고는 남아 있다. |
| fixture / Playwright | PASS | fixture 17 passed, Chromium desktop·tablet·mobile 3 passed. 첫 실행은 구버전 10행 기대값으로 3 FAIL이었고 11행·7감시·22방향·퇴역 disabled 계약으로 교정한 재실행이 3 PASS다. |
| security / repository hygiene | PASS | 126 source·violation/secret-like/실제주문 path 0 / 위반 0 |
| 실제 서비스·브라우저 | PASS | 최종 재시작 뒤 RUNNING·LIVE·PAPER, 기록 63건, K 상세와 XRP `재생`·`진입`·`실제 종료`를 직접 눌렀다. 브라우저 앱 오류·경고 0, 실제주문·인증 0이다. |
| 활성 원장 full quick_check | NOT_COMPLETED_RUNTIME_CONTENTION | 읽기 전용 검사를 547.52초 실행했지만 끝나지 않았고 자동 복구된 임계지연 사건 2회·최장 89.220초가 생겨 LIVE 우선 원칙으로 중단했다. 결과를 PASS로 쓰지 않는다. |
| 전략 수익성 | NOT_PROVEN | 독립 62행 중 양수 1, 순손익 -64.6068400286 USDT다. K 자연표본은 0이며 어떤 진입기준도 낮추지 않았다. |
| 6시간 / 24시간 soak | NOT_RUN | 수정 뒤 실제 시간을 채우지 않았다. |
| Release ZIP | NOT_RUN | 이번 Wave에서 새 ZIP을 만들지 않았다. |
| GitHub main / Actions | PENDING | 로컬 구현·검증·증거 완료 뒤 main 반영과 Actions를 확인한다. |

기계판독 증거는 `evidence/WAVE42_STRATEGY_POLICY_AND_REPLAY_QA.json`, 실제 화면은 `evidence/WAVE42_STRATEGY_POLICY_ACTUAL.png`와 `evidence/WAVE42_TRADE_REPLAY_ACTUAL_EXIT.png`, 결정 근거는 ADR-045·ADR-046이다. 이 Wave의 PASS는 구현·회귀·실제 PAPER 화면 범위이며 전략 수익성, 미래 독립 OOS, 활성 원장 전수검사, 6시간·24시간을 입증하지 않는다.
