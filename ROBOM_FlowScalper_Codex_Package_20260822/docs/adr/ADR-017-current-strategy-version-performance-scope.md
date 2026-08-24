# ADR-017. 현재 전략 버전 성과 범위와 불변 과거 원장

## 상태

Accepted, 2026-08-25.

## 문제

`shadow_trades`는 공개시장 PAPER 전략의 독립 BASE·STRESS 결과를 Run 경계로 불변 저장한다. 기존 성과 API는 `sample_type=LIVE_PUBLIC`만 검사했기 때문에 이전 진입·종료 로직으로 만든 표본과 현재 교체 후 표본이 승률, 기대값, Profit Factor, 비용, 낙폭과 보유시간에 함께 섹였다. 이는 과거 원장의 감사 가치를 보존하면서 현재 소프트웨어의 성과를 분리해야 하는 요구와 충돌한다.

또한 replay와 demo에서 공통 거래 변환기가 `sample_type=LIVE_PUBLIC`을 상수로 넣는 경로가 있어, 오프라인 표본이 현재 LIVE 성과에 오염될 수 있었다.

## 결정

1. 전략 식별자 목록과 구현 revision을 분리하고 `STRATEGY_VERSION=<현재 Registry ID 목록>@<implementation revision>` 형식으로 Run과 거래에 기록한다.
2. 기본 전략 성과는 `sample_type=LIVE_PUBLIC` 이면서 `strategy_version` 전체가 현재 빌드와 같은 독립 shadow 거래만 집계한다.
3. 이전 버전 거래는 삭제·수정하지 않고 불변 원장에 남긴다. 현재 집계에서 제외한 건수를 전략·프로필 및 API 전체 범위에 표시한다.
4. 예전 shadow payload에 `strategy_version`과 `config_hash`가 없으면 checksum을 먼저 검증한 뒤 연결된 불변 Run `config_json`과 `config_hash`로 조회 결과만 보강한다. 저장 payload나 checksum은 다시 쓰지 않는다.
5. 신규 shadow 완료 거래 payload에 `strategy_version`과 Run `config_hash`를 즉시 기록한다.
6. 실행 모드별 표본 유형을 LIVE는 `LIVE_PUBLIC`, demo는 `DEMO_FIXTURE`, replay는 `REPLAY`로 기록한다. LIVE 성과는 뒤의 두 유형을 절대 집계하지 않는다.
7. 성과표의 수수료·슬리피지·낙폭은 해당 현재 버전 성과 report 값을 사용한다. 이번 Run 계좌 합계는 별도 요약으로만 표시한다.
8. LIVE 대시보드는 요청마다 SQLite를 읽지 않고 Run 시작 때 나눈 현재·이전 버전 cache를 사용한다.

## 검증

- 현재 버전 LIVE_PUBLIC, 이전 버전 LIVE_PUBLIC, OFFLINE fixture를 같은 원장에 넣고 현재 표본만 집계되는지 검사한다.
- 이전 payload를 변경하지 않고 Run 메타데이터로 버전·설정 hash가 보강되는지 검사한다.
- LIVE dashboard cache 경로가 SQLite writer lock을 기다리지 않으면서 제외 건수를 유지하는지 검사한다.
- 전략 상세, 전체 성과, 전략×종목 화면에 현재 버전 범위와 제외 건수가 보이는지 검사한다.

## 한계

버전 분리는 서로 다른 로직의 표본 오염을 막을 뿐 수익성을 증명하지 않는다. 구현 로직을 바꾸는 후 revision을 갱신하지 않으면 다시 오염될 수 있으므로 전략 변경은 revision 변경·회귀검사·ADR을 한 작업으로 묶는다.
