# ADR-036. LIVE 전략 분석의 비차단 cache 경계

- 상태: Accepted
- 날짜: 2026-08-25
- 범위: 전략 성과와 전략·종목 분석 HTTP API

## 배경

활성 LIVE Run에서 `/api/analytics/strategies`는 매 요청마다 전체 `shadow_trades`와 Run 설정을 query-only SQLite 연결로 다시 읽고 checksum을 검증했다. 일반 `/api/dashboard` 다섯 요청은 0.009~0.016초였지만 전략 성과 요청 세 건은 13.723~16.010초가 걸렸다. 이후 저장 경합이 풀리면 0.027~0.028초로 회복돼 HTTP나 프론트 렌더링 전체 문제가 아니라 활성 원장 반복 읽기 경계로 좁혀졌다.

런타임은 시작과 복구 때 이미 전체 거래 checksum을 검증해 현재 전략 version과 과거 version을 메모리 cache로 분리한다. 현재 프로세스가 완료한 PAPER 거래는 같은 trade ID로 병합할 수 있으므로 LIVE HTTP 요청에서 원장을 다시 읽을 필요가 없다.

## 결정

1. LIVE 전략 성과·전략 종목 API는 시작 시 검증한 현재-version cache와 현재 프로세스의 완료 거래를 trade ID로 병합한다.
2. 제외된 과거 version 수는 검증된 prior-version cache에서 읽는다.
3. Replay와 다른 비LIVE mode는 기존 원장 읽기 계약을 유지한다.
4. LIVE API가 활성 원장을 다시 읽지 않는 회귀검사와 cache 결과가 실제 저장 거래를 포함하는 검사를 유지한다.

## 결과와 한계

이 변경은 분석 조회가 시장 저장 writer와 경쟁하는 경로를 제거한다. 배포 후 반복 HTTP 지연과 실제 브라우저 성과 화면은 별도로 재측정해야 하며, 코드 테스트 통과만으로 장시간 무버벅임이나 6시간·24시간 안정성을 주장하지 않는다.
