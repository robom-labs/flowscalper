# ROBOM FlowScalper 0.2.0-paper Wave 10

## 릴리스 목적

Wave 10은 로그인 후 자동 복구되는 localhost 서비스, 비전문가용 홈·scanner·전략 표현, 안정된 candle·거래량·이동평균 chart, 지연·저장 병목 제거와 schema v6 hybrid archive를 포함한다.

이 프로그램은 실제 공개시장 데이터를 사용하지만 실제 주문·private API·거래소 인증 경로가 없는 PAPER 연구 도구다.

## 주요 변경

- macOS LaunchAgent `RunAtLoad`·`KeepAlive`와 고정 `127.0.0.1:8870`.
- 프로그램 상태·진행 거래·완료 거래·순손익·정밀 관찰 종목 중심의 쉬운 홈.
- 고정 내부 스크롤 scanner와 종목별 상승·하락 관찰·진입 준비 상태.
- 실제 candle·거래량·한국시간과 선택형 5·10·20·60 이동평균선.
- chart 인스턴스 재사용과 animation-frame resize 병합.
- replay·analytics worker thread 분리와 dashboard ledger cache.
- 지연 percentile 256표본 cache, feature 단일 순회, 전략 평가 500ms 제한.
- PAPER 포지션 관리는 250ms deep 호가 경로 유지.
- SQLite schema v6 PAPER 원장과 외장 1,000-event ZSTD Parquet 공개시장 archive.
- row·batch checksum, 경로 containment와 merged replay.

## 검증 기준선

- backend pytest 105 PASS.
- frontend Vitest 3 files, 5 PASS.
- Ruff·mypy·ESLint·TypeScript·Vite build PASS.
- security scan 88 source, violation·secret-like file·real-order path 0.
- 4분 이상 실제 공개시장 집중 측정 종료 p95 140ms.
- 집중 측정 pause·drop·gap·reconnect·persistence fault 0.
- SQLite `PRAGMA quick_check=ok`.
- 최신 Run SQLite raw market event 0, 외장 Parquet replay PASS.

최신 UI의 Codex in-app browser DOM·screenshot은 admin-enforced policy 확인 실패로 `BLOCKED`였으며 다른 브라우저 자동화로 우회하지 않았다.

GitHub AI 인계·버전정리 maintenance를 합친 현재 tag source에서는 repository hygiene 테스트 2개가 추가되어 backend 107 PASS다. 아래 제품 ZIP은 Wave 10 기능 build를 고정한 asset이고, 현재 문서·CHANGELOG·CI·위생검사는 같은 tag의 GitHub source archive에 포함된다.

## 배포 파일

| 파일 | 값 |
|---|---|
| ZIP | `ROBOM_FlowScalper_0.2.0-paper-wave10-20260823.zip` |
| ZIP bytes | `10,970,142` |
| ZIP entries | `243` |
| ZIP SHA-256 | `1f433e47f4b3e405dcc483239206e13a3bbd9caa244a4b7b84a52ee70f7ccfe9` |
| 내부 BUILD_COMMIT | `23a709ca2e40f39c16e20f28b960f67492bbb1f6` |
| 내부 checksum | 242 entries PASS |
| 최종 증거 SHA-256 | `ac1340f33bf3bffe432b38622a62d222245d01fb8aa116812278d52d28d1c4d6` |

GitHub Release asset에는 ZIP, ZIP checksum, `FINAL_UPGRADE_EVIDENCE`와 그 checksum을 첨부한다. 원시 SQLite·Parquet 실행데이터, `.venv`, `node_modules`, cache와 로그는 포함하지 않는다.

## 읽기 순서

다른 AI 또는 개발자는 `00_AI_HANDOFF_먼저읽기.md`부터 읽고 `FINAL_UPGRADE_EVIDENCE.md`에서 PASS·NOT_RUN·BLOCKED 경계를 확인한다.
