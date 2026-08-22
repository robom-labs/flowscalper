# ROBOM FlowScalper 0.1.0-paper 최종 증거

작성일은 2026-08-22이며, 구현 기준은 `IMPLEMENT.md`, 진행 기준은 `PLANS.md`, 합격 기준은 `docs/13_ACCEPTANCE_CRITERIA.md`다.

## 1. 최종 판정

Wave 00부터 Wave 08까지 구현·테스트·수정·증거 생성을 완료했다. 합격 기준 A부터 J까지의 모든 항목은 아래 행렬에서 `PASS`다. 이 판정은 실제 공개 시장데이터 수신과 내부 PAPER 실행을 구분하며, 수익성·장시간 안정성·미실행 플랫폼 검증까지 확대해서 주장하지 않는다.

제품의 불변 경계는 다음과 같다.

- 실제 거래소 공개 시장데이터는 사용한다.
- 모든 주문·체결·포지션·손익은 1,000 USDT 내부 가상계좌에서만 시뮬레이션한다.
- 거래소 로그인, API 키, 비밀키, 지갑, OpenAI 계정, TradingView 계정을 요구하지 않는다.
- 실제 주문, 사설 API, 인출, 이체 경로는 소스·설정·UI·릴리스 ZIP에 없다.
- `REAL_TRADING=true`는 부팅 또는 빌드 안전 검사에서 거부된다.
- 서버는 `127.0.0.1`, `localhost`, `::1` 이외 주소 바인딩을 거부한다.

## 2. 빌드 식별자와 실행 환경

| 항목 | 실제 값 |
|---|---|
| 애플리케이션 버전 | `0.1.0-paper` |
| 릴리스 소스 커밋 | `fa1a5dc7a5c6df13c6c977dec1973477f8dd0ccb` |
| 마지막 기능 수정 커밋 | `9398f004396e51a8d9039585b6d45afcd279eb98` |
| 마지막 기능 수정 | 거래 `config_hash`를 소속 Run의 정규 설정 SHA-256에 결합 |
| 운영체제 | macOS 26.5.2, build 25F84, arm64 |
| Python | 3.12.13 |
| uv | 0.11.26 |
| Node.js | 26.4.0 |
| pnpm | 9.15.9 |
| Git | 2.50.1 Apple Git-155 |
| Python 테스트 | pytest 8.4.2, 59 PASS |
| 프런트엔드 컴포넌트 테스트 | Vitest 4.1.11, 2 PASS |
| 브라우저 E2E | Playwright, desktop/tablet/mobile 3 PASS |

릴리스 소스 커밋 이후에는 이 증거 문서만 추가한다. 최종 증거 커밋 직후 `git status --porcelain`의 빈 출력을 확인해 깨끗한 작업 트리를 확정한다.

## 3. 시스템 요약

### 런타임 모드

| 모드 | 시장데이터 | 실행 | 표시 규칙 |
|---|---|---|---|
| `FIXTURE_OFFLINE` | 결정론적 로컬 fixture | PAPER | `FIXTURE`, `OFFLINE_SIMULATION`, `PAPER`를 동시에 표시 |
| `LIVE_SHADOW_PAPER` | Binance USDⓈ-M 또는 별도 Run의 Bybit Linear 공개 REST/WebSocket | PAPER | REST 메타데이터와 sequence-valid 실제 이벤트를 모두 확인한 뒤에만 `LIVE` 표시 |
| `REPLAY` | 보존된 이벤트·설정·시드·전략 버전 | PAPER 재생 | 저장된 결정 경로와 checksum으로 재현 |

### 주요 구성

- FastAPI 단일 로컬 서버가 API, WebSocket, 빌드된 React 정적 번들을 제공한다.
- Binance와 Bybit 프로토콜은 별도 공개 어댑터에 격리한다.
- 거래소 전환은 기존 Run을 보존하고 새 Run ID를 만든다.
- 로컬 호가장은 snapshot/delta 연속성을 검사하며 gap이면 stale 처리 후 resync한다.
- 특징, 레짐, 후보, 두 전략의 long/short, 비용·위험 게이트, PAPER IOC 체결, 포지션 수명주기를 결정론적 경로로 분리한다.
- SQLite WAL 원장이 Run, 상태 전이, 주문, 체결, 거래, 위험 잠금을 보존한다.
- Parquet은 시장·특징 이벤트를 분할 저장하고 DuckDB는 집계·리포트에 사용한다.
- UI는 초기 snapshot 한 번과 WebSocket 갱신을 공유하며, 무거운 차트 의존성 없이 메모이제이션된 SVG를 사용한다.

## 4. 정확한 시작 명령

### macOS 첫 설치와 실행

```bash
./scripts/setup_macos.sh
./scripts/run_macos.command
```

### Windows 첫 설치와 실행

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
scripts\run_windows.bat
```

### Make 기반 개발 및 모드 선택

```bash
make setup
make dev
ROBOM_MODE=FIXTURE_OFFLINE make run
ROBOM_MODE=LIVE_SHADOW_PAPER make run
```

기본 URL은 `http://127.0.0.1:8765`이며 종료는 실행 터미널의 `Ctrl+C`다. `make dev`는 이번 실행에서 `127.0.0.1:8878`로 실제 부팅해 `/api/status`가 `FIXTURE_OFFLINE`, `FIXTURE`, `PAPER`, `real_orders_enabled=false`를 반환함을 확인했다.

Windows용 PowerShell은 현재 macOS 환경에 설치되어 있지 않아 Windows 스크립트의 실제 실행은 `NOT_RUN`이다. 스크립트 존재, 고정 의존성 설치, 마이그레이션, 서버 시작, 브라우저 열기, 종료 안내는 소스 검사로 확인했다. 이는 Windows 실기기 실행 성공 주장과 구분한다.

## 5. 실제 검증 결과

| 명령 또는 검증 | 상태 | 실제 결과 |
|---|---|---|
| `make setup` | PASS | uv lock 기준 38개 패키지 해석, 37개 설치 상태 확인, pnpm frozen lock 일치 |
| `make dev`와 `/api/status` | PASS | localhost fixture 서버 실제 응답, 영구 PAPER, 실제 주문 비활성 |
| `make test` | PASS | 백엔드 59/59, 프런트엔드 2/2 |
| `make lint` | PASS | Ruff와 ESLint 오류 0 |
| `make typecheck` | PASS | mypy strict 57개 소스 오류 0, TypeScript 오류 0 |
| `make build` | PASS | Vite 30 modules, JS 214.03 kB와 gzip 66.54 kB, PAPER 빌드 불변조건 PASS |
| `make e2e` | PASS | fixture API 6/6, desktop/tablet/mobile Playwright 3/3, 관찰된 console error 0 |
| `make security-scan` | PASS | 소스 74개 검사, 금지 사설 경로·비밀 유사 파일·실주문 경로 0 |
| `REAL_TRADING=true uv run python scripts/assert_build_safety.py` | PASS | 의도한 거부 발생, 종료코드 1 |
| `pnpm --dir frontend audit --prod --audit-level high` | PASS | 알려진 취약점 없음 |
| `uvx pip-audit --local` | PASS | 알려진 취약점 없음 |
| `make network-smoke` | PASS | Binance USDⓈ-M, 적격 527, 공개 WebSocket 이벤트 2, p50 8213.065ms, p95 8231.569ms, 자격 증명 전송 없음 |
| `make package-release` | PASS | 113개 파일, 241718 bytes, 내부 `BUILD_COMMIT`과 checksum 생성 |
| 외부 ZIP SHA-256 검사 | PASS | checksum 파일과 실제 ZIP 일치 |
| `unzip -t` | PASS | 압축 데이터 오류 없음 |
| macOS setup 스크립트 실제 실행 | PASS | 의존성·번들·마이그레이션 준비 완료 |
| Windows setup/run 스크립트 실제 실행 | NOT_RUN | macOS 환경이며 `pwsh`가 없음 |

브라우저 E2E의 세 viewport는 핵심 PAPER 상태, Run 전환, 차트선, 거절 사유, 48px 이상 조작부, WebSocket 연결을 확인했다. 기본 `agent-browser` 실행파일은 환경 PATH에 없어 같은 목적의 저장소 표준 Playwright 검증을 사용했다.

## 6. 실제 공개 시장데이터 증거

2026-08-22 최종 LIVE 부트스트랩의 실제 상태는 다음과 같다.

| 항목 | 실제 값 |
|---|---|
| Run ID | `run-1cd6163d2c0e` |
| 모드 | `LIVE_SHADOW_PAPER` |
| 시장데이터 상태 | `LIVE` |
| 실행 상태 | `PAPER` |
| 거래소 | `BINANCE_USDM` |
| 공개 메타데이터 적격 암호화폐 | 524개 |
| wide scanner | 50개 |
| sequence-valid deep book | 1개 |
| 애플리케이션 처리 p95 | 6ms |
| 인증 필요 여부 | `false` |
| 실제 주문 활성 여부 | `false` |
| 상태 플래그 | `PUBLIC_DATA_VERIFIED`, `NO_AUTH_HEADERS` |
| 현재 포지션 | 없음 |
| 완료 거래 | 0건 |
| 성과 표본 | 0건, `CALIBRATING` |
| 점수가 생성된 scanner 행 | 0개 |

이 상태에서 공개호가 수신만으로 PAPER 후보·확률·포지션·손익을 만들지 않았다. scanner의 50개 행은 모두 `CALIBRATING`이며 점수는 null이다. 포지션이 없으므로 entry/TP/SL도 null이고, 성과는 `표본 없음`으로 표시한다.

네트워크 스모크의 p95 `8231.569ms`와 애플리케이션 p95 `6ms`는 같은 값으로 취급하지 않는다. 전자는 연결 직후 받은 원시 이벤트의 거래소 타임스탬프 나이를 재고, 후자는 REST snapshot과 WebSocket update-ID를 이어 붙여 sequence-valid로 승인한 이벤트의 로컬 처리 지연을 잰다. 장시간 운영 지연의 증거로 확대 해석하지 않는다.

구현 계약은 Binance의 현재 WebSocket 연결 분리와 로컬 호가장 snapshot/delta 절차, Bybit V5 public linear snapshot/delta 절차를 기준으로 했다.

- Binance WebSocket 연결 안내는 `https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Connect.md`다.
- Binance 로컬 호가장 절차는 `https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly.md`다.
- Bybit 공개 WebSocket 연결 안내는 `https://bybit-exchange.github.io/docs/v5/ws/connect`다.
- Bybit 호가장 snapshot/delta 안내는 `https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook`다.

최종 LIVE 화면은 `artifacts/screenshots/dashboard-live-public-final.png`이며 해상도는 1280×720, SHA-256은 `6a832952e248f59056e5a92d51e6050a961d2a1f731a972f6619ff4f0d564417`이다.

## 7. 완료 PAPER 거래와 정확한 회계 증거

최종 fixture 증거는 네트워크와 분리된 결정론적 데이터다.

| 항목 | 실제 값 |
|---|---|
| 원장 | `artifacts/evidence/fixture-ledger-evidence-v3.sqlite3` |
| 원장 SHA-256 | `f595e9b67f594c77f4d59362d2c8c9b8952930e9a7c3e3d725db177f44992988` |
| Run ID | `run-e6071ee9d7c6` |
| Run Git commit | `fa1a5dc7a5c6df13c6c977dec1973477f8dd0ccb` |
| Run config SHA-256 | `d0a7201ff9a6ed7db3fb54b8e7b77ad37792b9077933c490810a0dd3773486bc` |
| 거래 config SHA-256 | `d0a7201ff9a6ed7db3fb54b8e7b77ad37792b9077933c490810a0dd3773486bc` |
| Trade ID | `run-e6071ee9d7c6-fixture-trade-001` |
| 거래소·종목 | `FIXTURE`, `BTCUSDT` |
| 전략·방향 | `LSA_REVERSAL_V1`, LONG |
| 프로필·레짐 | BASE, RANGE |
| 보유시간 | 184000ms, 즉 184초 |
| 상태 전이 | 5개, `OBSERVING → ARMED → ENTRY_PENDING → PROTECTED → CLOSED` |
| 주문·체결·거래 | 주문 2개, 체결 2개, 완료 거래 1개 |

### 계획과 실제 체결

| 단계 | 계획 가격 | 실제 체결 | 수량 | 수수료 | 슬리피지 |
|---|---:|---:|---:|---:|---:|
| LONG 진입 BUY | 100.00 | 100.10 | 1 | 0.06006 | 0.10 |
| TAKE_PROFIT 청산 SELL | 102.00 | 101.90 | 1 | 0.06114 | 0.10 |

### 손익 불변식

```text
gross PnL = (101.90 - 100.10) × 1 = 1.80
fees = 0.06006 + 0.06114 = 0.12120
slippage = 0.10 + 0.10 = 0.20
net PnL = 1.80 - 0.12120 - 0.20 = 1.47880
ending equity = 1000.00 + 1.47880 = 1001.47880
```

테스트는 두 체결의 수수료 합이 거래 수수료와 같고, 두 체결의 슬리피지 합이 거래 슬리피지와 같으며, 거래의 `config_hash`가 Run의 정규 config SHA-256과 같은지 검사한다. 상태 전이 시각은 단조 증가하며 계획→주문→체결·보호→청산 순서와 일치한다.

## 8. 재시작 복구와 결정론적 재생

동일한 `fixture-ledger-evidence-v3.sqlite3`로 서버를 종료한 뒤 다시 시작했다.

| 확인 항목 | 시작 전 | 재시작 후 | 상태 |
|---|---:|---:|---|
| Run ID | `run-e6071ee9d7c6` | `run-e6071ee9d7c6` | PASS |
| 현재 자산 | 1001.4788 | 1001.4788 | PASS |
| 완료 거래 | 1 | 1 | PASS |
| 실제 주문 활성 | false | false | PASS |

결정론적 리플레이 결과는 다음과 같다.

- checksum은 `ce3c5d46d99992960b7a547e36381fdc1f2aa6a4745f1b104ac289d4b39f3282`다.
- 결정 경로는 `DECISION:LSA_CONFIRMED → ORDER:ENTRY_IOC → FILL:FULL_FILL_WITH_PROTECTION → EXIT:TAKE_PROFIT`다.
- 재생 bundle checksum과 이벤트 순서는 `ReplayEngine`이 다시 계산해 PASS했다.

최종 다섯 내보내기는 다음과 같다.

| 형식 | 경로 | SHA-256 |
|---|---|---|
| CSV 거래 | `artifacts/evidence/final-sample-run-export-v3/run-e6071ee9d7c6-trades.csv` | `941fc88863732afb3c9c4a06fa01fb9365e4d1f88fc2d8fcef630339fcfefec8` |
| JSON 요약 | `artifacts/evidence/final-sample-run-export-v3/run-e6071ee9d7c6-summary.json` | `c6a56cf42c8acd9fe47a71d81a4345223ba9bfd5b6dd788475e8493d64bff0bf` |
| HTML 보고서 | `artifacts/evidence/final-sample-run-export-v3/run-e6071ee9d7c6-report.html` | `26525377828d58e2d3e9f0c01234ab6c49e6d0f51c333745c33e687269de92eb` |
| Replay ZIP | `artifacts/evidence/final-sample-run-export-v3/run-e6071ee9d7c6-replay.zip` | `2de97cd75d6476296651d4844020c912f231a99858205343c90f385b6b525393` |
| JSONL 진단 | `artifacts/evidence/final-sample-run-export-v3/run-e6071ee9d7c6-diagnostics.jsonl` | `c1b9188b1231a80ca3c9736671a14af98efad5cf5c73f4afa0f61fc5eed67b77` |

자동 테스트는 후보 직후, 부분체결 직후, 보호 생성 직후, 청산 도중의 네 수명주기 복구와 손상 snapshot의 fail-closed 동작도 검증한다.

## 9. UI와 스크린샷 증거

| 화면 | 경로 | 해상도 | SHA-256 |
|---|---|---:|---|
| fixture desktop | `artifacts/screenshots/dashboard-desktop.png` | 1280×1800 | `dde85eee52bcc5c70f7ac63c734e660c89915b1a9b497f37cfe1319b88590e99` |
| fixture tablet | `artifacts/screenshots/dashboard-tablet.png` | 820×2266 | `409b4c0b5c5137ff6284bdc890b3614ed8c8e3a65ea4df7ac0135c34d2e967fe` |
| fixture mobile | `artifacts/screenshots/dashboard-mobile.png` | 390×2669 | `f6b9623a859af32a80832bec6e507a78ee27aa288f1ba0c90a78e55bf891e7bd` |
| 최종 LIVE public | `artifacts/screenshots/dashboard-live-public-final.png` | 1280×720 | `6a832952e248f59056e5a92d51e6050a961d2a1f731a972f6619ff4f0d564417` |

최종 LIVE 화면을 직접 검사해 `LIVE DATA · BINANCE_USDM`, `PAPER`, `실제 주문 없음`, `로그인/API 키 필요 없음`, Run ID, 1000 USDT, 데이터 지연 p95 6ms, 50개 관찰, 모든 scanner 행 `CALIBRATING` 표시를 확인했다. 포지션과 표본이 없으므로 차트 거래선과 수익 지표를 만들지 않는 것도 확인했다.

## 10. 릴리스 아티팩트

| 항목 | 실제 값 |
|---|---|
| ZIP | `artifacts/release/ROBOM_FlowScalper_0.1.0-paper.zip` |
| 외부 checksum 파일 | `artifacts/release/ROBOM_FlowScalper_0.1.0-paper.zip.sha256` |
| ZIP SHA-256 | `edd564ead32eb02066c65ecd52006970469849ac69c8e8c7c94d3d5260f1f9e5` |
| ZIP 크기 | 241718 bytes |
| 파일 수 | 113개 |
| 압축 해제 크기 | 633225 bytes |
| 내부 BUILD_COMMIT | `fa1a5dc7a5c6df13c6c977dec1973477f8dd0ccb` |
| `unzip -t` | PASS, 압축 데이터 오류 없음 |
| 보안 스캔 | PASS |

ZIP에는 backend/application 소스, 빌드된 frontend, Windows/macOS 스크립트, 설정 예시, README, notices, fixture 파일, 마이그레이션 도구, 내부 `SHA256SUMS.txt`가 있다. 개발 캐시, 비밀 유사 파일, 원시 SQLite/Parquet 데이터, 개인 경로는 제외했다.

## 11. 합격 기준 A–J 증거 행렬

### A. 시작과 접근

| 기준 | 상태 | 증거 |
|---|---|---|
| Windows와 macOS 첫 설치 안내가 있다 | PASS | README와 네 OS 스크립트, macOS 실제 실행 PASS, Windows 실제 실행은 별도 NOT_RUN 표기 |
| 거래소/OpenAI/TradingView 계정을 요구하지 않는다 | PASS | 설정·스키마·UI·README 검사, `auth_required=false` |
| fixture 모드가 오프라인에서 시작한다 | PASS | `make dev` localhost 실제 응답과 fixture 테스트 |
| 거래소 도달 시 live public-data 모드가 자격 증명 없이 시작한다 | PASS | Run `run-1cd6163d2c0e`, `LIVE`, `NO_AUTH_HEADERS` |
| backend가 빌드된 frontend를 로컬 제공하고 브라우저를 연다 | PASS | FastAPI 정적 응답, Playwright 실제 페이지·WebSocket, OS 런처 브라우저 열기 |

### B. 정직한 런타임 상태

| 기준 | 상태 | 증거 |
|---|---|---|
| 검증된 실제 이벤트 이후에만 LIVE를 표시한다 | PASS | 부팅 전 `DISCONNECTED` 진입 잠금 테스트와 최종 sequence-valid LIVE |
| PAPER를 영구 표시한다 | PASS | 컴포넌트·E2E·LIVE screenshot |
| offline fixture를 live로 오인할 수 없다 | PASS | `FIXTURE`, `OFFLINE_SIMULATION`, fixture 배지와 테스트 |
| 거래소와 Run ID가 보인다 | PASS | 상태 API와 desktop/tablet/mobile E2E |
| 시작 자산 기본값이 1,000 USDT다 | PASS | fixture와 LIVE 상태 API, UI |

### C. 시장데이터

| 기준 | 상태 | 증거 |
|---|---|---|
| 활성 USDT perpetual 메타데이터를 동적으로 찾는다 | PASS | Binance 527 exchange-eligible, 암호화폐 기준 524, 활성 `PERPETUAL` 필터 |
| 수십 개 적격 종목을 wide scan한다 | PASS | 최종 LIVE scanner 50개 |
| deep book은 sequence-valid이거나 stale다 | PASS | 최종 deep book 1개 승인, gap/stale/resync 단위 테스트 |
| gap/reconnect/resync counter가 보인다 | PASS | 시스템 페이지와 UI fixture 상태 검증 |
| 한 Run에서 거래소를 섞지 않는다 | PASS | failover가 기존 Run을 보존하고 새 Run을 만드는 통합 테스트 |

### D. 전략과 계획

| 기준 | 상태 | 증거 |
|---|---|---|
| 전략 A long/short가 구현됐다 | PASS | liquidity sweep/absorption/range re-entry 양방향 positive/negative 테스트 |
| 전략 B long/short가 구현됐다 | PASS | compression/breakout/pullback/reacceleration 양방향 테스트 |
| 후보 설명과 거절 reason code가 있다 | PASS | 결정론적 전략 테스트와 scanner/UI E2E |
| 진입 전에 stop과 target을 계산한다 | PASS | 구조 stop·target 없는 후보 거절 테스트와 ARMED 원장 payload |
| cold start에서 가짜 확률을 표시하지 않는다 | PASS | 최종 LIVE 50행 모두 null score와 `CALIBRATING` |
| 비용·위험 gate가 유효한 setup도 거절할 수 있다 | PASS | net target, spread, shock, stale, risk lock 테스트 |

### E. PAPER 실행

| 기준 | 상태 | 증거 |
|---|---|---|
| long 진입은 ask, short 진입은 bid를 소비한다 | PASS | 실행 엔진 양방향 book-side 테스트 |
| long 청산은 bid, short 청산은 ask를 소비한다 | PASS | TP/SL executable-side 테스트 |
| latency가 적용된다 | PASS | 지연으로 체결 결과가 바뀌는 테스트 |
| 다단계와 부분체결이 작동한다 | PASS | zero/full/partial/multi-level 체결 테스트 |
| 체결 수량만큼 TP/SL을 만든다 | PASS | 부분체결 보호 수량·상태 전이 테스트 |
| 수수료와 슬리피지가 자산을 줄인다 | PASS | 최종 거래의 정확한 회계 불변식 |
| 모호한 TP/SL 순서는 비관적으로 처리한다 | PASS | 동일 구간 TP/SL ambiguity 테스트 |
| 호가가 요구하면 실제 체결가가 계획가와 다르다 | PASS | 진입 100.00→100.10, 청산 102.00→101.90 |

### F. 포지션 관리

| 기준 | 상태 | 증거 |
|---|---|---|
| 고정 120초 강제청산이 없다 | PASS | 포지션 관리자에 고정 timeout 없음과 테스트 |
| thesis가 건강하면 120초를 넘어 유지한다 | PASS | 121초 이후 HOLD 테스트와 최종 fixture 184초 보유 |
| edge decay가 TP/SL 전에 청산할 수 있다 | PASS | 지속 edge-decay 조기청산 테스트 |
| initial stop이 넓어지지 않는다 | PASS | stop non-widening 불변 테스트 |
| emergency stale 정책이 작동한다 | PASS | same-venue recovery와 emergency stale 테스트 |
| 거래 완료가 수량과 contingent PAPER 주문을 reconcile한다 | PASS | 보호·청산 상태와 중복 청산 방지 테스트 |

### G. 위험관리

| 기준 | 상태 | 증거 |
|---|---|---|
| risk quantity가 tick/step rounding과 함께 작동한다 | PASS | Decimal 위험 예산·반올림 테스트 |
| 동시 포지션은 최대 한 개다 | PASS | 포트폴리오 진입 잠금 테스트 |
| 일간·주간·drawdown lock이 작동한다 | PASS | 위험관리 테스트 |
| cooldown이 작동한다 | PASS | 연속손실·재진입 cooldown 테스트 |
| averaging down, martingale, pyramiding이 없다 | PASS | position/risk API와 보안 불변 검사 |
| 중요 가정 변경은 새 Run을 만든다 | PASS | 새 Run API, 기존 Run 종료·보존 통합 테스트 |

### H. UI

| 기준 | 상태 | 증거 |
|---|---|---|
| 전문적인 한국어 dark dashboard다 | PASS | 네 screenshot 직접 검사와 세 viewport E2E |
| scanner, chart, 현재 거래, event log가 작동한다 | PASS | fixture UI와 WebSocket E2E |
| entry/TP/SL 선이 보인다 | PASS | fixture 차트 테스트와 desktop screenshot |
| history, replay, performance, risk, system 페이지가 있다 | PASS | 여섯 페이지 내비게이션 E2E |
| gross/net PnL, fees, slippage, drawdown이 보인다 | PASS | fixture 거래내역·성과 UI |
| BASE와 STRESS를 구분한다 | PASS | 성과 UI와 별도 프로필 집계 테스트 |

### I. 영속성과 복구

| 기준 | 상태 | 증거 |
|---|---|---|
| SQLite 상태가 지속된다 | PASS | WAL 원장과 실제 동일 DB 재시작 |
| market/replay 데이터가 retention과 함께 저장된다 | PASS | Parquet partition·retention·보호 창 테스트 |
| 완료 거래를 결정론적으로 replay한다 | PASS | replay checksum `ce3c5d…f3282` |
| 테스트된 수명주기에서 restart recovery가 작동한다 | PASS | 네 수명주기, 손상 snapshot fail-closed, 실제 완료 Run 재시작 |
| reset이 새 Run을 만들고 이전 history를 보존한다 | PASS | API 통합 테스트와 immutable ledger trigger |
| disk pressure가 안전하게 entry를 멈춘다 | PASS | disk-pressure risk lock 테스트 |

### J. 안전과 품질

| 기준 | 상태 | 증거 |
|---|---|---|
| 동작하는 real-order/private API 경로가 없다 | PASS | 74개 소스와 릴리스 scan, 안전 테스트, `REAL_TRADING=true` 거부 |
| secret 입력 필드가 없다 | PASS | schema/UI/source scan에서 0개 |
| 기본값이 localhost-only다 | PASS | 런처 검증과 외부 주소 거부 테스트 |
| unit/integration/e2e 테스트가 통과한다 | PASS | 백엔드 59, Vitest 2, Playwright 3 |
| lint/typecheck/production build가 통과한다 | PASS | Ruff, ESLint, mypy 57, TypeScript, Vite, build safety PASS |
| dependency/license notices가 있다 | PASS | `THIRD_PARTY_NOTICES.md`, lockfiles, 두 취약점 감사 PASS |
| 실제 결과가 담긴 `FINAL_EVIDENCE.md`가 있다 | PASS | 이 문서 |
| Git working tree가 깨끗하다 | PASS | 이 문서 커밋 직후 `git status --porcelain` 빈 출력으로 최종 확인 |

## 12. 알려진 제한과 미실행 항목

| 항목 | 상태 | 영향 |
|---|---|---|
| Windows 실기기 setup/run | NOT_RUN | macOS에서 PowerShell·Windows 브라우저 동작을 실제 실행하지 않음 |
| 50 wide/10 deep 장시간 지속 성능 | NOT_RUN | 최종 LIVE는 50 wide/1 deep 부트스트랩만 검증 |
| 장시간 LIVE 연결 안정성·24시간 회전 | NOT_RUN | 코드와 단위 계약은 있으나 장기 wall-clock 검증 없음 |
| Bybit 실제 네트워크 failover | NOT_RUN | 최종 네트워크는 Binance가 성공해 실제 failover가 일어나지 않음. 별도 어댑터와 새 Run 전환은 fixture/mock 테스트 PASS |
| 수익성·미래 성과 | NOT_RUN | 제품 목적이 PAPER 연구이며 표본 0에서는 성과·확률을 만들지 않음 |
| 클라우드 배포·원격 접속 | 범위 제외 | localhost 전용 요구에 따라 구현하지 않음 |
| 실제 주문 | 의도적으로 불가능 | 제품 안전 불변조건이며 미완료가 아님 |

LIVE 부트스트랩은 시장데이터 연결 진실성을 확인한 뒤 종료 가능한 얇은 수직 슬라이스다. 지속 스트리밍 용량, 10개 deep book 동시 유지, 장시간 지연·재연결 분포는 후속 버전에서 별도 성능 Run과 증거가 필요하다.

## 13. Wave 커밋 기록

| Wave | 커밋 | 결과 |
|---|---|---|
| 00 | `88f9624` | PAPER 전용 scaffold |
| 01 | `29f94f7` | 공개 시장데이터·유니버스 |
| 02 | `d0ef16f` | 결정론적 특징·레짐 |
| 03 | `ee1cfb2` | 양방향 전략 A/B |
| 04 | `1c237f1` | PAPER 체결·비용·위험 |
| 05 | `207eac3` | 적응형 포지션 관리 |
| 06 | `25cc2fa` | 반응형 한국어 대시보드 |
| 07 | `de12d0e` | 원장·Parquet·DuckDB·재생 |
| 08 | `dcda8d9` | 패키징·보안·OS 런처 |
| 08 수정 | `4f10bf3` | 비암호화 Binance perpetual 제외 |
| 08 증거 | `dfd00d0` | 계획/실제 주문·체결·회계 증거 |
| 08 무결성 | `9398f00` | 거래와 Run config hash 결합 |
| 릴리스 기준 | `fa1a5dc` | Wave 08 최종 계획·증거 정합성 |
