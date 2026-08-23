# ROBOM FlowScalper v0.2 LIVE SHADOW PAPER 운영서

## 1. 프로그램 위치와 실행

현재 기준 작업공간은 One Touch 외장하드 안의 APFS 볼륨에 있다.

- 외장 시작 파일은 `/Volumes/One Touch/ROBOM_AUTOTRADING/FlowScalper_v0.2_20260822/START_ROBOM_FlowScalper.command`이다.
- macOS 앱은 `/Volumes/ROBOM_FLOWSCALPER/01_WORKSPACE/자동매매/ROBOM_FlowScalper_Codex_Package_20260822/ROBOM_FlowScalper.app`이다.
- 최상위 Terminal 실행파일은 같은 프로젝트 폴더의 `ROBOM_FlowScalper.command`이다.
- Windows에서는 `scripts\run_windows.bat`을 실행한다.

가장 간단한 macOS 실행 순서는 다음과 같다.

1. One Touch를 연결한다.
2. `START_ROBOM_FlowScalper.command`를 더블클릭한다.
3. 전용 `ROBOM_FLOWSCALPER` APFS 볼륨이 자동으로 마운트되는지 확인한다.
4. Terminal에 표시된 localhost 주소로 브라우저가 열리는지 확인한다.
5. 종료할 때는 해당 Terminal에서 `Control+C`를 누른다.

실행기는 기존 서버를 강제로 종료하지 않는다. 기본적으로 8870부터 빈 localhost 포트를 선택하며 LAN이나 인터넷 주소에는 바인딩하지 않는다.

## 2. 첫 설치 요구사항

- macOS 13 이상 또는 Windows 10/11이 필요하다.
- Python 3.12를 관리하는 `uv`가 필요하다.
- 최초 프런트엔드 빌드에 Node.js 22.13 이상 또는 24 이상과 `pnpm`이 필요하다.
- 일반 실행에는 거래소 계정, API 키, OpenAI 키, TradingView 계정, 지갑이 필요하지 않다.

의존성이나 정적 화면이 없으면 실행기가 고정 lockfile을 사용해 설치와 빌드를 먼저 수행한다. 수동 설치 명령은 macOS에서 `./scripts/setup_macos.sh`, Windows에서 `scripts\setup_windows.ps1`이다.

## 3. 안전한 시작 상태

프로그램은 `READY`로 열린다. 이 상태의 관찰 가능한 기준은 다음과 같다.

- 시작자산 1,000.00 USDT.
- 실현·미실현 손익 0.
- 수수료·슬리피지 0.
- 완료 거래 0.
- 시장데이터 `DISCONNECTED` 또는 시작 대기.
- 실행 상태 `PAPER`.
- 실제 주문 `false`, 인증 필요 `false`.

오프라인 DEMO는 사용법 확인용 별도 Run이다. DEMO 거래, 손익, 성과는 LIVE PAPER 기본 화면과 성과 집계에 섞이지 않는다.

## 4. LIVE PAPER 시작과 확인 순서

1. 시장 화면에서 `자동 관찰 시작`을 한 번 누른다.
2. 즉시 `연결 중`과 실제 연결 단계가 표시된다. 새 Run ID가 생성되고 공개시장 검증 전에는 신규 진입이 잠긴다.
3. Binance USDⓈ-M 공개 REST와 WebSocket이 우선 연결된다.
4. Binance 공개 데이터가 불가능하고 열린 복구 포지션이 없을 때만 새 Run으로 Bybit Linear 공개시장 전환을 시도한다.
5. 50개 종목 광역 감시와 현재 설정의 정밀 호가 감시가 확인된 뒤에만 `작동 중`이 표시된다.
6. 지연, sequence gap, queue drop, 디스크 압박, 원장 오류 중 하나라도 안전 기준을 벗어나면 PAPER 신규 진입은 잠긴다.
7. 자동 회복 가능한 잠금은 `작동 중 · 안전 대기`로 표시되며 시장 관찰은 계속된다. 안전조건이 정상화되면 새 PAPER 진입도 자동 복귀한다. 사용자가 직접 멈춘 경우에만 `새 진입 다시 시작`을 누른다.

LIVE 표시는 단순 인터넷 연결 표시가 아니다. sequence-valid 공개 depth 이벤트와 거래소·Run 일치가 검증돼야 한다.

## 5. 전략과 PAPER 계좌 운영

- `실전 PAPER`는 전략을 main 후보 중재와 독립 shadow 계좌에 모두 참여시킨다.
- `가상 관찰`은 main 후보에서 제외하고 전략별 BASE·STRESS shadow 계좌에서만 관찰한다.
- `끄기`는 전략 평가와 신규 shadow 진입을 중지한다.
- LONG·SHORT 스위치는 전략별로 독립 적용된다.
- 신규 C/D 전략은 `EXPERIMENTAL`이며 실제 주문이 아니라 PAPER 연구 전용이다.

적격 후보가 생기면 진입 전에 entry, worst entry, TP1, TP2, SL, 수량, 위험예산, 최대계획손실, 수수료, 슬리피지, 순 R:R을 불변 계획으로 확정한다. 자연스러운 신호가 없으면 임계값을 낮추지 않고 `CALIBRATING` 또는 거절 사유를 유지한다.

## 6. 체결과 포지션 관리

- 롱 진입은 latency 이후 ask 깊이를, 숏 진입은 bid 깊이를 소진한다.
- 롱 청산은 bid, 숏 청산은 ask를 사용한다.
- 가격 제한 밖의 잔여 수량은 채우지 않으며 부분체결을 기록한다.
- TP1·TP2·SL과 초기 수량이 보호 주문으로 연결된다.
- 고정 120초 종료는 없다.
- TP, SL, 근거 감쇠, 이익 보호, 데이터 안전, 사용자의 PAPER 비상종료 요청으로만 종료한다.
- 초기 손절은 불리한 방향으로 넓어지지 않는다.

`현재 PAPER 포지션 비상종료`는 거래소 주문을 보내는 버튼이 아니다. 다음 유효 공개호가에서 내부 PAPER 청산을 지연 체결하도록 요청한다.

## 7. 저장, 재시작, 복구

기본 원장은 프로젝트의 `data/run-ledger.sqlite3`에 저장된다. 외장 실행기를 사용하면 데이터도 외장 APFS 작업공간에 남는다.

- Run, 전략 설정, 후보, 주문, 체결, main·shadow 거래, snapshot, 사고, 리플레이 결과를 SQLite WAL 원장에 저장한다.
- 공개시장 이벤트와 캔들은 Run·venue·symbol 범위를 유지한다.
- 재시작 시 checksum-valid 최신 Run만 복구한다.
- 열린 포지션 또는 진입대기 종목은 wide·deep 구독에 고정한다.
- 동일 거래소에서 해당 종목의 fresh sequence-valid 호가가 오기 전에는 복구 잠금을 풀지 않는다.
- 복구 중 다른 거래소로 자동 전환하지 않는다.
- checksum, schema, Run, venue, 계좌 집합이 맞지 않으면 READY fail-closed로 시작한다.

원장 쓰기 실패는 main 위험상태를 영구 fault로 두고 신규 진입을 차단한다. 시장 retry buffer는 10,000건, 캔들은 5,000건으로 제한된다.

## 8. 운영 진단과 장애 대응

시스템 화면의 `고급 진단 보기`에서 다음 값을 확인한다.

- 공개 WebSocket 상태, event 수, reconnect, gap, resync, drop, queue 깊이.
- 지연 p50·p95.
- 현재 프로세스 CPU, max RSS 또는 Windows working set, thread, uptime.
- 디스크 전체·사용·여유 공간과 storage guard 상태.
- 원장 오류 횟수, 마지막 오류, 보존 buffer 수.
- 인증 헤더 0건과 실제 주문 경로 비활성 상태.

대표 장애별 대응은 다음과 같다.

- `RECONNECTING`이면 LIVE로 보지 말고 자동 재연결을 기다린다.
- `ENTRY_LOCK_DATA_HEALTH`이면 sequence-valid depth가 돌아오는지 확인한다.
- `CRITICAL_MARKET_LAG_ENTRY_LOCK`이면 공개 이벤트 지연 p95가 1,500ms 아래로 회복되는지 확인한다. health flag가 사라져도 자동 일시정지는 유지되므로 안전 상태를 확인한 뒤 명시적으로 재개한다.
- `ENTRY_LOCK_RECOVERY_REVALIDATION`이면 복구 종목의 원 거래소 공개호가를 기다린다.
- `STORAGE_PRESSURE_ENTRY_LOCK`이면 외장 볼륨 연결과 여유 공간을 확인한다.
- `PERSISTENCE_FAULT_ENTRY_LOCK`이면 원장 파일 권한·디스크 오류를 점검하고 기존 Run을 보존한다.
- 화면만 끊겼다면 backend PAPER 관리는 계속되므로 브라우저를 새로고침한다.

## 9. 검증 명령

```bash
make test
make lint
make typecheck
make build
ROBOM_E2E_CAPTURE=0 make e2e
make network-smoke
make security-scan
make soak-30m
make package-release
```

6시간·24시간 명령은 각각 `scripts/soak_6h.command`, `scripts/soak_24h.command`이다. 실제 벽시계 시간 동안 실행하지 않은 결과는 PASS로 추정하지 않고 `NOT_RUN`으로 기록한다.

## 10. 금지된 범위

이 프로그램은 실제 주문, private API, API 키, 출금, 이체, 지갑, 원격 제어, 수익 보장을 제공하지 않는다. `REAL_TRADING=true`는 부팅과 빌드에서 거부된다.
