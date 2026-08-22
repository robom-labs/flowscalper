# ROBOM FlowScalper v0.2 업그레이드 실행계획

- 상태: Wave 07 완료, Wave 08 릴리스 진행 중
- 기준 소스: `0.1.0-paper`, 기준 커밋 `c1de1165bd25d4ebba7346416f2fb6aa8f1e69d7`
- 목표 버전: `0.2.0-paper`
- 데이터 경계: 공개 REST/WebSocket만 사용
- 실행 경계: 내부 PAPER만 사용, 실제 주문·private API·자격 증명 0
- 기본 계좌: 1,000 USDT

## 확인한 입력

2026-08-22 업그레이드 ZIP의 00~07 문서, `reference/CURRENT_IMPLEMENTATION_CONTEXT.txt`, `reference/screenshots/`의 5개 화면을 모두 읽었다. 외부에 별도로 제공된 00·01 파일은 ZIP 내부 파일과 SHA-256이 일치했다. ZIP에는 절대경로, 상위경로 탈출, 심볼릭 링크 항목이 없었다.

## 0.1 기준선

| 검증 | 실제 결과 |
|---|---|
| `make setup` | PASS |
| `make test` | 백엔드 59 PASS, 프런트 2 PASS |
| `make lint` | PASS |
| `make typecheck` | PASS |
| `make build` | PASS |
| `make security-scan` | PASS, 실제 주문 경로 0 |
| `make network-smoke` | Binance PASS, eligible 527, 공개 WS 2건, credentials false, lag P95 8178.781ms |
| `make e2e` | ENVIRONMENT_CONFLICT, 사용자가 실행 중인 동일 앱이 127.0.0.1:8765를 점유해 두 번째 Playwright 서버 부팅 차단 |

기존 8765 프로세스는 사용자 실행 상태를 보존하기 위해 종료하지 않는다. v0.2 E2E는 독립 포트를 사용하고 기존 서버 재사용 여부를 명시적으로 제어한다.

## 확인된 핵심 갭

1. LIVE는 첫 공개 호가를 확인한 뒤 WebSocket을 닫는 일회성 bootstrap이다.
2. LIVE 기본 시작을 사용자가 명시적으로 누르는 READY 상태가 없다.
3. fixture 완료 거래와 SOL 포지션이 기본 화면·성과에 포함된다.
4. 기존 A/B 전략, 피처, 위험, 체결 모듈은 구현돼 있으나 LIVE 런타임 파이프라인에 연결되지 않았다.
5. Strategy Registry, C/D, 전략별 모드·방향·독립 shadow 원장이 없다.
6. 차트는 실제 캔들이 아닌 SVG 선형 표시이며 시간구간 계약이 1·5·15·60초뿐이다.
7. 현재 포지션과 PnL은 실제 엔진 상태가 아니라 fixture 화면 값이다.
8. 리플레이 화면 제어는 backend 저장 이벤트와 연결되지 않았다.
9. WebSocket UI는 0.5초마다 전체 snapshot을 재전송한다.
10. 장시간 supervisor, bounded queue, 실제 reconnect/resync/rotation 계측이 없다.

## Wave와 관찰 가능한 종료 기준

### Wave 01. 모드와 Run 진실성

- `READY`, `LIVE_SHADOW_PAPER`, `DEMO_FIXTURE`, `REPLAY`를 명시한다.
- 기본 READY 화면은 1,000 USDT, 손익·수수료·슬리피지·거래 0이다.
- `실시간 PAPER 시작`이 새 Run을 만들고 공개 데이터 검증 전 진입을 잠근다.
- fixture 기록은 DEMO 전용 저장소·성과 필터로 분리한다.
- API 통합테스트가 fresh LIVE Run의 모든 0 값을 증명한다.

### Wave 02. 지속 공개데이터와 캔들

- FastAPI 수명주기와 함께 supervisor가 시작·중지된다.
- Binance `/public`과 `/market` 연결을 분리하고 Bybit public linear를 별도 Run fallback으로 둔다.
- wide 최대 50, deep 기본 10, deep 허용 8~12를 유지한다.
- bounded queue, reconnect, resync, gap, stale, rotation, drop, lag를 계측한다.
- 실제 agg trade로 `1s, 5s, 15s, 30s, 1m, 3m, 5m, 10m, 15m` 캔들을 만든다.

### Wave 03. Strategy Registry와 shadow 연구

- A/B/C/D 메타데이터·안정성·지원 레짐을 Registry로 제공한다.
- 전략마다 `ACTIVE`, `SHADOW`, `OFF`와 LONG·SHORT 허용을 저장한다.
- A/B/C/D를 동일 공개 이벤트 파이프라인에서 평가한다.
- 전략·비용 프로필마다 독립 shadow 계좌와 거래 원장을 유지한다.

### Wave 04. 계획·체결·포지션

- 불변 `CandidatePlan`에 entry, worst entry, TP1, TP2, SL, 수량, 위험, 비용, edge-decay를 확정한다.
- main 계좌는 결정적 중재 후 최대 한 포지션만 연다.
- 롱은 ask, 숏은 bid를 실제 깊이에서 IOC로 소비하고 부분체결을 지원한다.
- TP1/TP2, SL, edge decay, stale exit와 실시간 미실현·실현 순손익을 연결한다.

### Wave 05. 원장·리플레이·분석

- market events, candles, candidates, strategy settings/accounts, shadow trades를 migration으로 추가한다.
- 거래내역은 실제 불변 원장만 표시한다.
- backend ReplayEngine이 저장 market event를 순서대로 재처리하고 checksum·결정 경로를 반환한다.
- 전략별 표본, 승률, 기대값, Profit Factor, 총·순손익, 비용, drawdown, BASE/STRESS를 계산한다.
- 표본상태를 0~29, 30~99, 100~299, 300+ 기준으로 표시한다.

### Wave 06. 한국어 사용자 화면

- 라이브, 전략, 거래내역, 리플레이, 성과분석, 위험관리, 시스템 7개 화면을 제공한다.
- Lightweight Charts v5 캔들, 시간구간, 종목 선택, bid/ask, 진입·TP1·TP2·SL, 체결 마커를 표시한다.
- 초보자용 요약을 기본으로 두고 원시 enum·queue·gap은 접이식 고급진단으로 이동한다.
- 데스크톱·태블릿·모바일, 48px 제어, 콘솔 오류 0을 Playwright로 검증한다.

### Wave 07. 복구·soak·보안

- 연결 끊김, sequence gap, 재동기화, UI 재연결, 열린 PAPER 포지션 복구를 테스트한다.
- 30분 자동 soak와 6시간·24시간 실행 스크립트를 제공한다.
- 실제 실행하지 못한 긴 soak는 `NOT_RUN`으로 기록한다.
- 보안검사에서 private endpoint, credential input, 실제 주문 호출이 0임을 재확인한다.

### Wave 08. 증거와 릴리스

- `RUNBOOK_LIVE_SHADOW_PAPER.md`, `STRATEGY_CATALOG_KO.md`, `UI_USER_GUIDE_KO.md`, `MIGRATION_NOTES_v0.2.md`, `SOAK_TEST_REPORT.md`를 완성한다.
- 실제 명령 결과와 데스크톱·태블릿·모바일 스크린샷을 `FINAL_UPGRADE_EVIDENCE.md`에 기록한다.
- `make test lint typecheck build e2e security-scan network-smoke package-release` 결과를 구분한다.
- 최종 릴리스 ZIP과 SHA-256을 만들고 Git 상태를 기록한다.

## 반복 한도와 중단 조건

각 실패는 원인 전체를 읽고 최대 3회 수정·재검증한다. 동일 원인이 2회 연속이면 해당 항목을 증거에 명시하되 다른 독립 Wave는 계속한다. 실제 주문·private API가 필요해지는 설계, 기존 사용자 프로세스 종료, 외부 배포·결제·계정 조작은 중단 조건이다. 자연스러운 신호가 없다는 이유로 전략 기준을 낮추지 않으며, 저장 공개 이벤트의 결정적 리플레이로 종단 경로를 검증한다.
