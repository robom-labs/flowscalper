# ROBOM FlowScalper 0.1.0-paper에서 v0.2.0-paper로 이동

## 기준과 보존 원칙

v0.2는 새 프로젝트가 아니라 기존 0.1.0-paper의 최종 소스와 Run 원장을 확장한 버전이다. 기존 SQLite 파일을 자동 삭제하거나 기존 Run 결과를 다시 쓰지 않는다. schema migration은 `scripts/migrate.py`와 `SQLiteLedger`가 idempotent하게 적용한다.

업그레이드 전에는 실행 중인 기존 서버를 종료하고 `data` 폴더를 별도 복사하는 것이 권장된다. 이미 열린 PAPER Run이 있다면 snapshot과 WAL 파일이 안정적으로 닫힌 뒤 백업한다.

## 외장하드 기준 위치

2026-08-22부터 canonical 작업공간은 One Touch의 ExFAT 파일시스템에 직접 두지 않고 다음 APFS sparsebundle 안에 둔다.

- sparsebundle은 `/Volumes/One Touch/ROBOM_AUTOTRADING/FlowScalper_v0.2_20260822/ROBOM_FlowScalper_Workspace.sparsebundle`이다.
- 마운트 지점은 `/Volumes/ROBOM_FLOWSCALPER`이다.
- Git 작업공간은 `/Volumes/ROBOM_FLOWSCALPER/01_WORKSPACE/자동매매`이다.
- 입력 ZIP과 지시문은 `/Volumes/ROBOM_FLOWSCALPER/00_INPUTS`이다.
- 최종 릴리스는 `/Volumes/ROBOM_FLOWSCALPER/02_RELEASES`에 둔다.

APFS 경계는 Python 가상환경, Node 심볼릭 링크, 실행권한, Git 객체를 보존한다. 내부의 이전 작업 경로는 외장 canonical 위치를 가리키는 작은 심볼릭 링크만 남긴다.

## 주요 제품 변화

| 0.1 상태 | v0.2 변화 |
|---|---|
| 시작 시 fixture가 기본 화면에 보임 | READY에서 1,000 USDT, 손익·비용·거래 0으로 시작하고 DEMO를 별도 Run으로 격리 |
| LIVE 첫 이벤트 뒤 연결 종료 | 23시간 45분 계획 rotation, backoff, 재연결, bounded queue를 갖춘 지속 supervisor |
| 제한된 deep 관찰 | 최대 50개 wide, 기본 10개 deep과 8~12개 불변조건 |
| A/B 모듈이 LIVE 실행과 분리 | A/B/C/D Registry를 LIVE 피처, 계획, main·shadow PAPER 실행에 종단 연결 |
| 전략별 제어 없음 | ACTIVE·SHADOW·OFF와 LONG·SHORT 독립 설정 |
| fixture 화면값 중심 | 실제 main 포지션, 실현·미실현 순손익, 비용, 불변 원장 연결 |
| 단순 SVG 관찰선 | Lightweight Charts v5 실제 candle·bid·ask·microprice와 entry·TP1·TP2·SL·marker |
| 요약 리플레이 | 저장 공개 이벤트를 같은 전략·후보·체결 파이프라인으로 재처리하는 backend ReplayEngine |
| 승률 중심 요약 | 기대값, Profit Factor, 비용, 낙폭, 보유시간, 표본상태, BASE·STRESS 비교 |
| 부분 snapshot 복구 | main·8개 실행계좌·shadow 회계·pending·position·exit·risk 전체 복구와 checksum fail-closed |
| 정적 시스템 값 | 실제 CPU·메모리·thread·uptime·디스크·storage·원장 buffer 진단 |

## 데이터베이스 변화

v0.2 원장은 schema version 6을 사용하며 기존 기본 테이블을 보존한 채 market events, candles, candidates, strategy settings, strategy account snapshots, shadow trades, replay runs, 빠른 `market_event_stats`와 불변 `market_event_archives` manifest를 추가한다. 신규 시장 이벤트 통계는 row trigger가 아니라 저장 batch 단위로 누적하고, 기존 대용량 Run은 업그레이드 때 전체 재계수하지 않아 부팅과 홈페이지를 보호한다. 고빈도 공개시장 이벤트는 1,000건 단위 ZSTD Parquet으로 외장에 저장하고 SQLite에는 PAPER 상태와 checksum manifest를 둔다. 기존 Run과 새 Run은 Run ID, venue, config hash, app·strategy version으로 분리된다.

복구 snapshot payload는 schema version 1을 사용한다. 이것은 SQLite 전체 schema version과 다른 PAPER 상태 직렬화 버전이다. checksum이 맞더라도 Run, venue, 전략 계좌 집합, 비용 프로필, 수량, 위험상태 불변조건이 틀리면 복구를 거부한다.

## 호환성과 주의사항

- 0.1 완료 거래와 fixture 기록은 보존되지만 LIVE_PUBLIC과 DEMO_FIXTURE 집계는 분리된다.
- 0.1의 열린 snapshot에 v0.2 전체 portfolio payload가 없으면 열린 lifecycle 자동 복구를 허용하지 않고 fail-closed로 시작한다.
- 전략 설정과 위험 가정은 Run 내부에서 불변이므로 중요한 설정을 바꾸면 기존 Run을 보존하고 새 Run을 만든다.
- 실제 주문이나 private API로의 migration 경로는 없다.
- 6시간·24시간 soak는 실행 스크립트가 제공되더라도 실제 수행 전까지 `NOT_RUN`이다.

## 업그레이드 후 확인

```bash
uv sync --frozen --all-groups
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
uv run python scripts/migrate.py
make test
make lint
make typecheck
make security-scan
```

macOS에서는 최상위 `ROBOM_FlowScalper.command`를 실행하고 READY 상태의 1,000 USDT와 모든 손익·비용·거래 0을 확인한다. 이후에만 `자동 관찰 시작`으로 새 공개시장 Run을 연다. 로그인 후 사이트를 자동 복구하려면 `./scripts/install_macos_service.sh`를 한 번 실행한다.

자동 서비스는 `~/Library/Application Support/ROBOM FlowScalper/active-ledger/run-ledger.sqlite3`를 소형 활성 거래 원장으로 사용하고, 외장 `data/market-parquet-v6`를 공개시장 archive로 사용한다. macOS LaunchAgent가 One Touch 직접 쓰기를 차단하고 외장 디스크 이미지의 SQLite checkpoint가 실시간 유입량을 따라가지 못하는 것을 실측했기 때문이다. 내장·외장에 각각 여유공간 5GiB·4% 안전 잠금을 적용한다. 이전 진단 원장 `data/active/run-ledger.sqlite3`·`data/active-v5/run-ledger.sqlite3`·`data/active-v6/run-ledger.sqlite3`와 기존 대용량 `data/run-ledger.sqlite3`는 외장 과거 기록으로 보존한다. 로그인 후 빠른 시작을 위해 Python 실행환경만 내장 `~/Library/Application Support/ROBOM FlowScalper/runtime-venv`에 복사하고 소스·릴리스·고빈도 공개시장 데이터는 외장에 유지한다.
