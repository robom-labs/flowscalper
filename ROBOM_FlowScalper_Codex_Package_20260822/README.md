# ROBOM FlowScalper 0.1.0-paper

ROBOM FlowScalper는 실제 공개 암호화폐 USDT 무기한선물 시장데이터를 연구하되, 모든 결정과 체결을 1,000 USDT 내부 가상계좌에서만 시뮬레이션하는 로컬 애플리케이션입니다.

이 제품은 거래소 로그인, API 키, OpenAI 키, TradingView 계정, 지갑을 요구하지 않습니다. 실제 주문·인출·이체·사설 API 호출 경로는 소스와 UI에 없으며, `REAL_TRADING=true`는 부팅과 빌드에서 거부됩니다.

## 제품 경계

- `FIXTURE_OFFLINE`은 네트워크 없이 재현 가능한 테스트 시장을 사용합니다.
- `LIVE_SHADOW_PAPER`는 Binance USDⓈ-M을 우선하고 Bybit Linear를 별도 Run 후보로 사용합니다.
- `REPLAY`는 기록된 이벤트·설정·시드·전략 버전으로 결정 경로를 재현합니다.
- `LIVE`는 REST 메타데이터와 sequence-valid 공개 WebSocket 이벤트가 모두 검증된 후에만 표시됩니다.
- 실행은 `127.0.0.1`에만 바인딩됩니다. 지원 런처는 `0.0.0.0`과 LAN·공인 주소를 거부합니다.

## 시스템 요구사항

- macOS 13 이상 또는 Windows 10/11.
- Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 22 이상, [pnpm](https://pnpm.io/installation).
- Node/pnpm은 최초 설치·프런트엔드 빌드에만 필요하고, 일반 실행은 FastAPI가 정적 번들을 제공합니다.
- LIVE 모드에만 거래소 공개 REST/WebSocket 네트워크가 필요합니다.

## macOS 첫 실행

```bash
./scripts/setup_macos.sh
./scripts/run_macos.command
```

런처는 고정 의존성을 설치하고 React 번들·SQLite 스키마를 준비한 후 `http://127.0.0.1:8765`를 엽니다. 종료는 실행 터미널에서 `Ctrl+C`를 누릅니다.

## Windows 첫 실행

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
scripts\run_windows.bat
```

런처는 브라우저를 열고 종료 방법을 콘솔에 표시합니다.

## 모드 선택

안전한 기본값은 `FIXTURE_OFFLINE`입니다.

```bash
ROBOM_MODE=FIXTURE_OFFLINE make run
ROBOM_MODE=LIVE_SHADOW_PAPER make run
```

Windows Command Prompt에서는 `set ROBOM_MODE=LIVE_SHADOW_PAPER`를 실행한 뒤 `scripts\run_windows.bat`을 실행합니다. Binance가 도달 불가능해 Bybit로 전환할 때는 기존 Run을 종료·보존하고 새 Run ID를 생성합니다. 어떤 경우에도 열린 PAPER 포지션을 거래소 사이에서 옮기지 않습니다.

## 화면 구성

- 라이브에서 동적 종목 스캐너, bid/ask/mid/microprice, PAPER 진입·TP·SL, 현재 거래, 이벤트 로그를 확인합니다.
- 거래내역은 총·순손익, 수수료, 슬리피지, 종료 사유를 구분합니다.
- 리플레이는 보존된 완료 거래만 재생합니다. 공개호가 수신만으로 결정·체결을 만들지 않습니다.
- 성과분석은 표본 수와 `CALIBRATING`, BASE/STRESS를 같이 표시합니다.
- 위험관리는 일간·주간·drawdown·연속손실 잠금과 새 Run 생성을 표시합니다.
- 시스템은 거래소, Run ID, 지연, gap/reconnect/resync, 저장소·보존정책, 자격 증명 사용 여부를 표시합니다.

데스크톱·태블릿·모바일은 동일한 사용자 흐름을 제공하며 핵심 조작부는 48px 이상입니다.

## 저장·복구·내보내기

- `data/run-ledger.sqlite3`에 Run, 상태 전이, PAPER 주문·체결·거래, 위험 잠금과 사고를 WAL 트랜잭션으로 보존합니다.
- `data/parquet/venue=.../date=.../symbol=.../hour=.../event_type=...`에 압축 시장·특징 이벤트를 보존합니다.
- 기본 보존기간은 deep-book 7일, 1초 특징·캔들 90일입니다. 후보·거래 창은 자동 정리에서 보호됩니다.
- 저장소 여유 공간이 기준보다 작으면 원장을 우선 보호하고 신규 PAPER 진입을 잠깁니다.
- CSV, JSON Run 요약, HTML, checksum 리플레이 ZIP, JSONL 진단로그를 내보낼 수 있습니다.

`make clean-data-safe`는 기존 Run을 지우지 않고 내보내기 후 사용자가 파일을 명시적으로 선택하라는 안내만 합니다.

## 설정과 새 Run

`config/*.example.yaml`에 거래소, 유니버스, 전략, 비용, 위험, 저장소 예시가 있습니다. 현재 Run의 시드, 전략 버전, 비용·지연·위험 가정은 불변입니다. 중요 가정을 바꾸면 기존 Run을 종료·보존한 뒤 새 Run을 만듭니다.

## 검증 명령

```bash
make setup
make test
make lint
make typecheck
make build
make e2e
make network-smoke
make security-scan
make package-release
```

`make network-smoke`는 오프라인 CI와 분리된 선택적 공개 네트워크 검사이며 `PASS`, `FAIL`, `NOT_RUN`을 숨기지 않고 기록합니다.

## 문제 해결

- 화면이 열리지 않으면 `curl http://127.0.0.1:8765/api/status`로 서버를 확인하고 터미널 오류를 읽습니다.
- `DISCONNECTED` 또는 `RECONNECTING`은 LIVE가 아닙니다. DNS, 방화벽, 거래소 접근 정책을 확인하고 `make network-smoke`를 실행합니다.
- `CRITICAL_MARKET_LAG_ENTRY_LOCK`이 보이면 데이터는 실제이어도 신규 PAPER 진입이 잠긴 상태입니다. UI로 우회할 수 없습니다.
- sequence gap은 해당 호가를 stale로 표시하고 새 snapshot으로 재동기화합니다.
- SQLite checksum 불일치·손상은 복구를 중단하고 신규 진입을 잠깁니다.
- 포트가 사용 중이면 `ROBOM_PORT=8876 make run`처럼 localhost 포트만 바꿉니다.

## 알려진 제한

- 연구용 PAPER 시스템이며 수익성을 보장하지 않습니다.
- 실시간 부트스트랩은 50개 공개 book-ticker 광역 관찰과 1개 sequence-valid deep book을 검증합니다. 50 wide/10 deep 지속 성능은 실증하지 않았습니다.
- LIVE 부트스트랩은 시장데이터 진실성과 연결을 검증하지만 장시간 연속 운영 안정성을 증명하지 않습니다.
- 표본 30건 미만은 `CALIBRATING`으로 표시하며 승률·확률로 성과를 과장하지 않습니다.
- 자동 배포, 원격 접속, 클라우드 동기화, 실제 거래는 포함하지 않습니다.

정확한 실행 증거, 샘플 PAPER 거래, 재시작 복구, 네트워크 스모크, 보안 검사, 릴리스 checksum은 `FINAL_EVIDENCE.md`에 기록됩니다.
