# ROBOM FlowScalper 0.2.0-paper

ROBOM FlowScalper는 실제 공개 암호화폐 USDT 무기한선물 시장데이터를 연구하되, 모든 결정과 체결을 1,000 USDT 내부 가상계좌에서만 시뮬레이션하는 로컬 애플리케이션입니다.

이 제품은 거래소 로그인, API 키, OpenAI 키, TradingView 계정, 지갑을 요구하지 않습니다. 실제 주문·인출·이체·사설 API 호출 경로는 소스와 UI에 없으며, `REAL_TRADING=true`는 부팅과 빌드에서 거부됩니다.

## 제품 경계

- `READY`는 LIVE나 DEMO를 시작하기 전의 1,000 USDT, 손익·비용·거래 0 상태입니다.
- `DEMO_FIXTURE`는 네트워크 없이 재현 가능한 격리 테스트 시장을 사용합니다.
- `LIVE_SHADOW_PAPER`는 Binance USDⓈ-M을 우선하고 Bybit Linear를 별도 Run 후보로 사용합니다.
- `REPLAY`는 기록된 이벤트·설정·시드·전략 버전으로 결정 경로를 재현합니다.
- `LIVE`는 REST 메타데이터와 sequence-valid 공개 WebSocket 이벤트가 모두 검증된 후에만 표시됩니다.
- 실행은 `127.0.0.1`에만 바인딩됩니다. 지원 런처는 `0.0.0.0`과 LAN·공인 주소를 거부합니다.

## 시스템 요구사항

- macOS 13 이상 또는 Windows 10/11.
- Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 22.13 이상 또는 24 이상, [pnpm](https://pnpm.io/installation).
- Node/pnpm은 최초 설치·프런트엔드 빌드에만 필요하고, 일반 실행은 FastAPI가 정적 번들을 제공합니다.
- LIVE 모드에만 거래소 공개 REST/WebSocket 네트워크가 필요합니다.

## macOS 첫 실행

```bash
./scripts/setup_macos.sh
./ROBOM_FlowScalper.command
```

Finder에서는 `ROBOM_FlowScalper.app` 또는 `ROBOM_FlowScalper.command`를 더블클릭합니다. 런처는 고정 의존성을 설치하고 React 번들·SQLite 스키마를 준비한 후 8870부터 사용 가능한 localhost 포트를 선택해 브라우저를 엽니다. 기존 포트의 프로세스를 종료하지 않으며, 종료는 실행 Terminal에서 `Ctrl+C`를 누릅니다.

현재 One Touch 설치는 `/Volumes/One Touch/ROBOM_AUTOTRADING/FlowScalper_v0.2_20260822/START_ROBOM_FlowScalper.command` 하나로 APFS 작업공간 마운트와 앱 실행을 처리합니다.

### macOS 항상 켜지는 로컬 사이트

최초 설치 후 다음 명령을 한 번 실행하면 로그인·재부팅·프로세스 종료 뒤 `http://127.0.0.1:8870/` 서버가 자동으로 복구됩니다.

```bash
./scripts/install_macos_service.sh
```

서비스는 안전한 `READY`로 시작하며 공개시장 모의거래는 화면의 `자동 관찰 시작`을 눌러야 시작됩니다. canonical 소스·작업공간·릴리스와 고빈도 공개시장 기록은 외장에 보존합니다. macOS가 LaunchAgent의 One Touch 직접 쓰기를 차단하고 외장 디스크 이미지의 고빈도 SQLite 쓰기가 실시간 처리를 지연시킨 것이 실측되어, 자동 서비스의 소형 거래 상태·설정·archive manifest용 활성 SQLite, 약 283MB Python 실행환경 복사본, LaunchAgent plist와 운영 로그만 `~/Library/Application Support/ROBOM FlowScalper`에 둡니다. 공개시장 원본 이벤트는 1,000건 단위 ZSTD Parquet으로 외장 `data/market-parquet-v6`에 저장됩니다. 내장 또는 외장 여유공간이 5GiB 미만이거나 4% 미만이면 신규 진입을 fail-closed로 잠급니다. 이전 진단 원장 `data/active/run-ledger.sqlite3`·`data/active-v5/run-ledger.sqlite3`·`data/active-v6/run-ledger.sqlite3`와 기존 1.3GB `data/run-ledger.sqlite3`는 삭제하거나 덮어쓰지 않습니다. 컴퓨터가 꺼져 있는 동안 localhost는 열 수 없으며, 로그인 후 외장 소스가 보이면 자동으로 다시 실행됩니다.

자동 시작을 해제하되 거래 원장과 외장 파일을 보존하려면 다음 명령을 실행합니다.

```bash
./scripts/uninstall_macos_service.sh
```

## Windows 첫 실행

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
scripts\run_windows.bat
```

런처는 브라우저를 열고 종료 방법을 콘솔에 표시합니다.

## 모드 선택

안전한 기본값은 `READY`입니다.

```bash
ROBOM_MODE=READY make run
ROBOM_MODE=DEMO_FIXTURE make run
ROBOM_MODE=LIVE_SHADOW_PAPER make run
```

Windows Command Prompt에서는 `set ROBOM_MODE=LIVE_SHADOW_PAPER`를 실행한 뒤 `scripts\run_windows.bat`을 실행합니다. Binance가 도달 불가능해 Bybit로 전환할 때는 기존 Run을 종료·보존하고 새 Run ID를 생성합니다. 복구된 포지션이나 진입대기 상태가 있으면 원 거래소와 종목을 고정하고 failover하지 않습니다. 어떤 경우에도 열린 PAPER 포지션을 거래소 사이에서 옮기지 않습니다.

## 화면 구성

- 홈에서 프로그램 상태, 진행·완료 거래, 현재 순손익, 정밀 관찰 종목을 먼저 확인합니다.
- 관찰 종목은 상승·하락 방향과 진입 준비 상태만 먼저 보이며, 전략·비용·손익비·거절 이유는 `상세`에서 확인합니다.
- 실제 캔들·거래량·5선·10선이 기본 표시되고 20선·60선·호가선은 버튼으로 선택합니다. 이동평균은 선택한 차트 시간구간의 캔들 수 기준입니다.
- 매매 설정에서 A/B/C/D의 `자동 모의매매`·`기록만 하기`·`사용 안 함`과 상승·하락 방향을 독립 제어합니다.
- 거래내역은 총·순손익, 수수료, 슬리피지, 종료 사유를 구분합니다.
- 리플레이는 저장 공개시장 이벤트를 같은 A/B/C/D·후보·PAPER 체결 파이프라인으로 다시 처리하고 입력 checksum과 결정 경로를 표시합니다.
- 성과분석은 표본 수와 `CALIBRATING`, BASE/STRESS를 같이 표시합니다.
- 위험관리는 일간·주간·drawdown·연속손실 잠금과 새 Run 생성을 표시합니다.
- 시스템은 거래소, Run ID, 지연, gap/reconnect/resync, 저장소·보존정책, 자격 증명 사용 여부를 표시합니다.

데스크톱·태블릿·모바일은 동일한 사용자 흐름을 제공하며 핵심 조작부는 48px 이상입니다.

## 저장·복구·내보내기

- 수동 실행은 `data/run-ledger.sqlite3`, macOS 자동 서비스는 `~/Library/Application Support/ROBOM FlowScalper/active-ledger/run-ledger.sqlite3`에 Run, 상태 전이, PAPER 주문·체결·거래, 위험 잠금, archive manifest와 사고를 WAL 트랜잭션으로 보존합니다.
- 자동 서비스의 공개시장 event는 외장 `data/market-parquet-v6`에 ZSTD Parquet으로 보존합니다. 각 row와 batch checksum, root 경로 검증 뒤 SQLite event와 시간순 병합해 replay합니다. candle, 후보, strategy account와 replay 결과는 Run 범위의 SQLite 원장에 보존합니다.
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

- 화면이 열리지 않으면 Terminal에 출력된 URL 뒤에 `/api/status`를 붙여 확인하고 Terminal의 전체 오류를 읽습니다.
- `DISCONNECTED` 또는 `RECONNECTING`은 LIVE가 아닙니다. DNS, 방화벽, 거래소 접근 정책을 확인하고 `make network-smoke`를 실행합니다.
- `CRITICAL_MARKET_LAG_ENTRY_LOCK`이 보이면 데이터는 실제이어도 신규 PAPER 진입이 잠긴 상태입니다. UI로 우회할 수 없습니다.
- sequence gap은 해당 호가를 stale로 표시하고 새 snapshot으로 재동기화합니다.
- SQLite checksum 불일치·손상은 복구를 중단하고 신규 진입을 잠깁니다.
- 최상위 실행기는 빈 localhost 포트를 자동 선택합니다. 수동 실행에서 포트를 고정하려면 `ROBOM_PORT=8876 make run`처럼 localhost 포트만 지정합니다.
- 자동 실행 사이트가 열리지 않으면 `launchctl print gui/$(id -u)/kr.robom.flowscalper`와 `~/Library/Application Support/ROBOM FlowScalper/service-error.log`를 확인합니다.

## 알려진 제한

- 연구용 PAPER 시스템이며 수익성을 보장하지 않습니다.
- 30분 공개시장 soak는 자동 수용 smoke이며 6시간·24시간 연속 운영은 실제 실행 전까지 `NOT_RUN`입니다.
- 거래소 공개 API의 지역 제한, 유지보수, protocol 변경은 로컬 코드로 제거할 수 없으며 이 경우 LIVE 대신 fail-closed 상태를 표시합니다.
- 표본 30건 미만은 `CALIBRATING`으로 표시하며 승률·확률로 성과를 과장하지 않습니다.
- 자동 배포, 원격 접속, 클라우드 동기화, 실제 거래는 포함하지 않습니다.

정확한 실행 증거, 실제 공개시장 기록·리플레이, 재시작 복구, 소크, 네트워크 스모크, 보안 검사, 릴리스 checksum은 `FINAL_UPGRADE_EVIDENCE.md`에 기록됩니다. 0.1 기준선 증거는 `FINAL_EVIDENCE.md`에 보존됩니다.
