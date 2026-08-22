# ADR-006. 실행계좌 복구와 자원압력 fail-closed

- Status: Accepted
- Date: 2026-08-22
- Owners: ROBOM / Codex

## Context

기존 SQLite 복구는 임의 lifecycle 문자열과 작은 payload의 checksum을 읽는 기반만 있었다. 실제 `CandidatePlan`, 부분 체결, 보호 주문, TP1 이후 잔여 수량, 청산 지연, main·shadow 위험상태와 전략 설정은 새 `PaperRuntime`에 돌아오지 않았다. 시스템 화면의 CPU·메모리·디스크는 실제 값이 아니었고 Parquet 디스크 보호기도 LIVE 신규진입 게이트와 분리돼 있었다.

## Decision

1. 모든 열린 Run snapshot은 `schema_version`, `run_id`, main과 전략별 BASE·STRESS 실행계좌, 독립 shadow 회계, 위험상태, pending candidate, 열린 포지션, entry·protection·exit 주문, 체결, TP별 잔여 수량, pending exit, 완료 거래를 포함한다.
2. snapshot은 기존 SQLite canonical JSON SHA-256 검사를 통과한 경우에만 복구한다. Run·venue·Registry 계좌 집합·비용 프로필·수량·위험상태가 하나라도 맞지 않으면 main 위험상태를 `faulted`로 두고 신규 진입을 차단한다.
3. 원장에 이미 완료 거래가 있는데 snapshot이 직전 열린 포지션을 가리키는 crash 창은 append-only 완료 거래를 최종 진실로 삼아 포지션을 닫고 자산·낙폭·거래 수를 재계산한다. 주문·거래 ID 집합도 복구해 중복 기록을 막는다.
4. 복구한 LIVE Run은 항상 `ENTRY_LOCK_RECOVERY_REVALIDATION` 상태로 시작한다. 복구된 포지션 또는 진입대기 종목을 wide·deep 공개 유니버스에 고정하고, 해당 종목의 fresh sequence-valid 공개호가가 돌아오기 전에는 잠금을 해제하지 않는다. 복구 중에는 다른 venue로 자동 전환하지 않으며, 원 venue가 불가하면 Run과 PAPER 상태를 보존한 채 fail-closed로 남는다. 열린 포지션의 마지막 snapshot 시각을 data-gap 시작으로 보존하고 첫 fresh 호가에서 TP·SL·stale 정책을 보수적으로 평가한다.
5. 전략별 ACTIVE·SHADOW·OFF와 LONG·SHORT는 같은 Run의 최신 checksum 검증 설정을 복구한다. shadow 가상계좌는 전략·비용 프로필 계좌 집합이 정확히 일치할 때만 복구한다.
6. 기본 실행환경은 SQLite 경로와 같은 volume에 Parquet storage guard를 둔다. 기본 임계는 여유 2GiB와 5%이며 둘 중 하나라도 미달하면 LIVE 신규진입을 잠근다. `ROBOM_MIN_FREE_BYTES`, `ROBOM_MIN_FREE_RATIO`로 환경별 임계를 명시할 수 있다.
7. SQLite market batch나 실행원장 쓰기가 실패하면 신규진입을 영구 차단하고 오류·횟수를 실제 telemetry에 남긴다. 재시도 메모리 buffer는 market 10,000건, candle 5,000건으로 제한한다. 이 상태를 UI 재개 버튼으로 풀 수 없다.
8. 시스템 고급진단은 현재 프로세스 CPU, max RSS/Windows working set, thread, uptime, 전체·사용·여유 디스크, storage guard, 저장 buffer와 오류를 실제 측정값으로 표시한다.
9. Binance와 Bybit 공개 WebSocket은 24시간 제한 전에 23시간 45분에 계획 rotation한다. fixture에서 rotation·disconnect·bounded queue를 시간 압축 검증한다.
10. `soak_live.py`는 원장을 키우지 않는 공개시장 자원진단으로 30분·6시간·24시간을 같은 코드로 실행한다. 6시간·24시간 macOS 실행기는 별도 `.command`로 제공하며 실제 미실행은 `NOT_RUN`으로 기록한다.
11. 공개 이벤트 지연 p95가 1,500ms를 넘으면 supervisor와 런타임이 모두 신규 PAPER 진입을 잠근다. 기준을 낮추지 않으며, 정상 지연의 fresh sequence-valid depth가 돌아오고 사용자가 재개하기 전까지 런타임은 paused 상태를 유지한다. 실제 네트워크 soak는 외부 지연이 전혀 없음을 합격조건으로 오인하지 않고, 모든 임계 초과 표본에서 fail-open이 0인지와 종료 시 지연이 정상 또는 진입잠금 상태인지 검사한다.

## Safety impact

복구 실패, 디스크 압박, 원장 쓰기 실패는 모두 실제 주문이 아닌 PAPER 신규진입만 막는다. 열린 PAPER 포지션 관리는 브라우저와 분리해 계속되지만 감사 가능한 상태를 보존할 수 없는 저장 실패는 새 거래를 허용하지 않는다. private endpoint·credential·실제 주문 경로는 추가하지 않는다.

## Validation

- pending entry, protected position, exit pending을 JSON roundtrip하고 main·8개 shadow 실행계좌와 shadow 회계가 동일한지 검사한다.
- 실제 SQLite Run을 entry fill 뒤 닫고 두 차례 재개해 열린 포지션·pending TP1 exit·최종 trade와 전략 설정이 복구되는지 검사한다.
- 복구 종목이 원래 상위 50개 밖에 있어도 50 wide·10 deep 크기를 유지한 채 양쪽 유니버스에 고정되는지 검사하고, 해당 종목의 첫 fresh depth에서만 복구 잠금이 해제되는지 검사한다.
- checksum 손상 DB가 READY fail-closed로 부팅되고 새 거래를 만들지 않는지 검사한다.
- 가짜 disk pressure와 SQLite write fault를 주입해 신규진입 잠금, 영구 risk fault, bounded retry buffer를 검사한다.
- 실제 OS sampler에서 CPU·메모리·디스크가 숫자로 측정되는지 검사한다.
- 1,500ms 초과 공개 지연을 주입한 뒤 supervisor·런타임 동시 잠금, rolling p95 회복, fresh depth 재검증, 명시적 재개 순서를 검사한다.
- 실제 공개시장 30분 smoke와 6시간·24시간 스크립트 결과를 `SOAK_TEST_REPORT.md`에 구분해 기록한다.
