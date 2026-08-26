# ADR-058. 리플레이 결과의 Run·종목 범위 고정

## 상태

승인. 2026-08-27.

## 배경

실제 기준 8870의 거래 기록에는 이번 Run의 공동계좌 1건과 전략별 계좌 74건이 표시됐고, 과거 재생은 저장 Run·종목 미리보기·정밀 이벤트·재생 제어를 정상 제공했다. 그러나 화면이 `/api/replay/results`에서 Run별 최신 결과만 가져오고 리플레이 결과 자체에는 검증 종목이 없었다. 같은 Run에서 종목을 바꾸면 직전 종목의 checksum·전략 평가·종단간 결과가 새 선택 종목의 증거처럼 남을 수 있었다.

또한 `StoredMarketReplay.run()`을 직접 호출할 때 소문자 종목 필터가 원장 조회 전에 정규화되지 않아 저장 이벤트가 있는데도 0건 결과를 만들 수 있었다. HTTP 경로는 미리 대문자로 바꾸지만, 재사용 가능한 리플레이 경계 자체가 같은 불변조건을 보장해야 한다.

## 결정

1. 저장 리플레이 결과에 `scope_symbol`을 추가한다. 전체 Run 검증은 `null`, 종목 검증은 공백 제거·대문자 정규화한 종목을 기록한다.
2. 정규화한 값 하나를 원장 이벤트 필터와 결과 범위에 함께 사용한다. 호출 경로에 따라 종목 대소문자 의미가 달라지지 않게 한다.
3. 화면은 현재 선택한 Run과 종목에 모두 일치하는 결과만 checksum·평가·종단 결과로 표시한다.
4. 과거 result JSON에 `scope_symbol`이 없으면 `symbol_counts`가 정확히 한 종목일 때만 그 종목 범위로 복구한다. 범위가 모호한 과거 결과는 현재 종목의 증거로 표시하지 않는다.
5. 새 검증이 실행 중이면 직전 결과를 숨기고 진행 상태만 표시한다. 완료 결과가 같은 Run·종목으로 저장된 뒤에만 다시 표시한다.
6. 화면 상단에는 `검증 완료 · <종목> · <replay_id>`를 표시해 초보자도 결과 범위를 확인할 수 있게 한다.
7. replay_runs 테이블은 변경하지 않는다. `scope_symbol`은 기존 append-only `result_json`에 추가하고 과거 기록은 다시 쓰지 않는다.
8. 전략 조건·체결·비용·TP·SL·계좌·원장 거래·실제주문 0 경계는 변경하지 않는다.

## 결과

- 다른 종목의 과거 checksum이나 전략 평가가 현재 선택 종목 결과처럼 보이지 않는다.
- 소문자·대문자 종목 입력이 같은 저장 이벤트를 재생한다.
- 신규 결과와 단일 종목임이 명백한 과거 결과를 함께 사용할 수 있고, 모호한 증거는 fail-closed한다.
- DB schema migration 없이 append-only 리플레이 기록의 호환성을 유지한다.

## 검증 경계

수정 전 backend 표적 테스트는 소문자 `btcusdt`가 저장된 4건을 찾지 못해 event_count 0으로 실패했다. frontend 표적 테스트는 `검증 완료 · BTCUSDT · replay-btc` 범위가 없고 다른 종목 선택 전에 직전 checksum을 그대로 표시해 실패했다.

수정 뒤 backend 표적 1건, backend 전체 442건, frontend 전체 14 files·64건, Ruff·mypy 95 source files·ESLint·TypeScript·PAPER safety·security·repository hygiene가 PASS했다. commit `7b593cbc5ca24e366a23cf28df4d983ffb604c2f`의 별도 불변 릴리스 build와 desktop·tablet·mobile Playwright 3건도 PASS했다. 실제 설치 8870은 기준 6시간 observer를 유지하므로 아직 새 commit으로 배포하지 않았다. 새 범위 문구의 설치 브라우저 확인·배포 후 원장·GitHub main·Actions는 `NOT_RUN`, 수익성은 `NOT_PROVEN`이다.
