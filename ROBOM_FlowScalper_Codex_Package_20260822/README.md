# ROBOM FlowScalper 0.2.0-paper

ROBOM FlowScalper는 실제 공개 암호화폐 USDT 무기한선물 시장데이터를 연구하되, 모든 결정과 체결을 1,000 USDT 내부 가상계좌에서만 시뮬레이션하는 로컬 애플리케이션입니다.

> 다른 GPT·Claude·Codex가 이 저장소를 검토한다면 먼저 [`00_AI_HANDOFF_먼저읽기.md`](00_AI_HANDOFF_먼저읽기.md)를 읽으세요. 사용자가 GPT에 그대로 전달할 업그레이드 방향 요청문은 [`01_GPT_업그레이드_방향_요청프롬프트_KO.txt`](01_GPT_업그레이드_방향_요청프롬프트_KO.txt), 짧은 버전 기록은 [`CHANGELOG.md`](CHANGELOG.md), 반복 업그레이드 정리 규칙은 [`docs/18_VERSIONING_AND_UPGRADE_POLICY_KO.md`](docs/18_VERSIONING_AND_UPGRADE_POLICY_KO.md), Wave 10 배포 요약은 [`RELEASE_NOTES_v0.2_WAVE10.md`](RELEASE_NOTES_v0.2_WAVE10.md)에 있습니다.

현재 `main`에는 최신 실행 소스 한 벌만 둡니다. 과거 버전은 복사 폴더로 쌓지 않고 `CHANGELOG.md`의 짧은 요약, Git tag·history와 GitHub Release의 ZIP·checksum으로 보존합니다.

GitHub 저장소 최상위에는 이 프로그램 폴더와 GitHub가 자동화를 인식하는 숨김 메타폴더 `.github`만 있습니다. 제품 소스·문서·실행파일은 모두 이 프로그램 폴더 안에 있고, 최상위 `.github`에는 CI와 PR checklist만 있습니다.

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

서비스는 보존된 Run 상태를 안전하게 복구하며 화면의 `자동 관찰 시작`을 누르면 `연결 중`을 거쳐 `작동 중`이 표시됩니다. 일시적인 데이터 안전잠금에서는 시장 관찰을 유지한 채 `작동 중 · 안전 대기`로 전환했다가 조건이 정상화되면 새 PAPER 진입을 자동 복귀합니다. canonical 소스·활성 SQLite 원장·공개시장 Parquet·불변 릴리스·Python base·venv·cache·temp·운영 로그는 모두 외장 APFS에 보존합니다. 내장에는 macOS가 요구하는 작은 `~/Library/LaunchAgents/kr.robom.flowscalper.plist`만 남기고 로그는 외장에서 10MiB 단위로 회전합니다. 공개시장 원본 이벤트는 1,000건 단위 ZSTD Parquet으로 저장하고, archive와 활성 원장 파일시스템 중 하나라도 여유공간이 5GiB 미만이거나 4% 미만이면 신규 PAPER 진입을 fail-closed로 잠급니다. 컴퓨터가 꺼져 있는 동안 localhost는 열 수 없으며, 로그인 후 One Touch가 연결되면 외장 bootstrap이 sparsebundle을 연결하고 서비스를 다시 시작합니다.

자동 시작을 해제하되 거래 원장과 외장 파일을 보존하려면 다음 명령을 실행합니다.

```bash
./scripts/uninstall_macos_service.sh
```

대형 활성 원장에 full `quick_check`를 직접 실행하지 마세요. 포지션이 0인 유지관리 시간에만 다음 명령으로 닫힌 APFS clone을 만든 뒤 서비스를 먼저 복구하고, 다른 device의 임시 사본에서만 전수검사합니다. 실행 전 원장 크기와 외장·검증 device 여유공간을 확인하고 경로를 명시적으로 지정해야 합니다.

```bash
uv run python scripts/verify_macos_ledger_maintenance.py \
  --source "$ROBOM_DB_PATH" \
  --snapshot-dir "$ROBOM_LEDGER_SNAPSHOT_DIR" \
  --verification-dir "$ROBOM_LEDGER_VERIFICATION_DIR" \
  --output evidence/WAVE48_MACOS_LEDGER_MAINTENANCE.json
```

유지관리는 localhost를 잠시 내린다. 실제 Wave 48에서 동일 Run은 16.912초 후 복구됐고 전송·검사 동안은 작동 중이었다. 안전선을 넘으면 fail-closed하며, PASS 후 외장 clone과 검증 device의 임시 사본을 제거한다. 세부 계약은 `docs/adr/ADR-049-closed-cross-device-ledger-integrity.md`에 있습니다.

전수검사가 길어 검사 중 자연 PAPER 진입이 열릴 수 있다면 먼저 화면에서
`새 진입 잠시 멈추기`를 누르고 포지션 0건을 확인한 뒤 `--require-manual-pause`를
추가한다. 이 옵션은 사용자 일시정지·시장 관찰 유지·새 진입 비활성을 재기동
전후와 검사 샘플링마다 다시 확인한다. 검사가 성공하거나 중단된 뒤에는 화면에서
`새 진입 다시 시작`을 눌러야 한다. 포지션·critical lag·실제 주문·인증·저장·재연결
안전 상한은 느슨하지 않는다. 세부 결정은
`docs/adr/ADR-083-verified-manual-pause-ledger-maintenance.md`에 있다.

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
- 열린 PAPER 포지션이 있으면 차트 위에 방향·전략·BASE/STRESS·entry·TP1·SL을 표시하고, 시장 화면의 진행 목록에서 원하는 종목을 바로 선택합니다.
- 전략 설정에서 현재 등록 전략의 `공동·독립 모의 중`·`독립 모의 중`·`꺼짐`과 상승·하락 방향을 독립 제어합니다. 현재는 추세형 V2 네 개를 포함한 10개 SHADOW 전략을 동시에 감시하고, 비용후 검증에 실패한 5개 퇴역 전략은 원장과 과거 성과를 보존하되 새 연구 전까지 재활성화를 잠급니다. 각 전략은 현재 감시상태, 최근 조건 대기 이유와 평가경로 수를 함께 표시합니다.
- 거래내역은 총·순손익, 수수료, 슬리피지, 종료 사유를 구분합니다.
- 리플레이는 저장 공개시장 이벤트를 같은 Strategy Registry·후보·PAPER 체결 파이프라인으로 다시 처리하고 입력 checksum과 결정 경로를 표시합니다. 거래 상세 재생의 압축 cache 기록은 선택적 가속일 뿐이며 활성 원장이 잠겨도 원본 원장으로 만든 재생 결과를 사용자에게 반환합니다.
- 성과분석은 표본 수와 `CALIBRATING`, BASE/STRESS를 같이 표시합니다.
- 위험관리는 일간·주간·drawdown·연속손실 잠금과 새 Run 생성을 표시합니다.
- 시스템은 거래소, Run ID, 실제 호가·체결·wide scanner의 분리된 지연, gap/reconnect/resync, 저장소·보존정책, 자격 증명 사용 여부를 표시합니다.

데스크톱·태블릿·모바일은 동일한 사용자 흐름을 제공하며 핵심 조작부는 48px 이상입니다.

## 저장·복구·내보내기

- 수동 개발 실행은 외장 프로젝트의 `data/run-ledger.sqlite3`을 사용합니다. macOS 자동 서비스의 원장·불변 릴리스·Python 실행환경·cache·temp·로그는 모두 같은 외장 APFS volume의 `05_RUNTIME/ROBOM_FlowScalper` 아래에 있습니다. 내장에는 macOS가 요구하는 작은 LaunchAgent plist만 남습니다. 외장 프로젝트·runtime·sparsebundle 계약을 통과하지 못하면 자동 서비스 설치와 시작을 거부합니다.
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
make service-soak-30m
# 정확히 6시간·24시간을 채울 때만 실행
make service-soak-6h
make service-soak-24h
make security-scan
make repo-hygiene
make package-release
```

`make network-smoke`는 오프라인 CI와 분리된 선택적 공개 네트워크 검사이며 `PASS`, `FAIL`, `NOT_RUN`을 숨기지 않고 기록합니다.

`make soak-30m`는 독립 공개시장 연결을 만드는 자원 진단입니다. 실행 중인 8870 서비스를 증명할 때는 별도 연결·Run·writer를 만들지 않는 `make service-soak-30m`을 사용합니다. 각 결과는 실제로 채운 벽시계 시간만 PASS로 판정합니다.

## 문제 해결

- 화면이 열리지 않으면 Terminal에 출력된 URL 뒤에 `/api/status`를 붙여 확인하고 Terminal의 전체 오류를 읽습니다.
- `DISCONNECTED` 또는 `RECONNECTING`은 LIVE가 아닙니다. DNS, 방화벽, 거래소 접근 정책을 확인하고 `make network-smoke`를 실행합니다.
- `CRITICAL_MARKET_LAG_ENTRY_LOCK`이 보이면 데이터는 실제이어도 신규 PAPER 진입이 잠긴 상태입니다. UI로 우회할 수 없습니다.
- sequence gap은 해당 호가를 stale로 표시하고 새 snapshot으로 재동기화합니다.
- SQLite checksum 불일치·손상은 복구를 중단하고 신규 진입을 잠깁니다.
- 최상위 실행기는 빈 localhost 포트를 자동 선택합니다. 수동 실행에서 포트를 고정하려면 `ROBOM_PORT=8876 make run`처럼 localhost 포트만 지정합니다.
- 자동 실행 사이트가 열리지 않으면 `launchctl print gui/$(id -u)/kr.robom.flowscalper`와 `/Volumes/ROBOM_FLOWSCALPER/05_RUNTIME/ROBOM_FlowScalper/logs/service-error.log`를 확인합니다. One Touch와 APFS sparsebundle이 연결돼 있는지도 함께 확인합니다.

## 알려진 제한

- 연구용 PAPER 시스템이며 수익성을 보장하지 않습니다.
- 독립 공개시장 soak와 실행 서비스 soak는 별도 증거입니다. 6시간·24시간 연속 운영은 정확한 실제 시간을 채우기 전까지 `NOT_RUN`입니다.
- 거래소 공개 API의 지역 제한, 유지보수, protocol 변경은 로컬 코드로 제거할 수 없으며 이 경우 LIVE 대신 fail-closed 상태를 표시합니다.
- 표본 30건 미만은 `CALIBRATING`으로 표시하며 승률·확률로 성과를 과장하지 않습니다.
- 자동 배포, 원격 접속, 클라우드 동기화, 실제 거래는 포함하지 않습니다.

정확한 실행 증거, 실제 공개시장 기록·리플레이, 재시작 복구, 소크, 네트워크 스모크, 보안 검사와 릴리스 checksum은 `FINAL_UPGRADE_EVIDENCE.md`에 기록됩니다. 0.1의 짧은 사용자용 기준선은 `CHANGELOG.md`, 전체 과거 파일은 Git history와 기존 릴리스 ZIP에 보존됩니다.
