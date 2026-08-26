# ADR-055. 런타임 전략 연구 계약 공개

## 상태

승인. 2026-08-27.

## 배경

현재 Registry는 11개 PAPER 전략의 horizon, 예상 보유시간, 신호 반감기, 시간구간, exit model, TP1·TP2, 최대보유와 비용모델을 공개했다. 그러나 승인된 전면점검 목표가 요구하는 필수 시장데이터, 최소 warmup, 진입 가설과 반증 조건, 공동·독립 PAPER 위험예산, 대상 종목, 미래정보 방지와 1차 연구 근거는 실행 descriptor와 한국어 화면에서 하나의 계약으로 확인할 수 없었다.

`STRATEGY_CATALOG_KO.md`의 현재 상태도 실행 코드와 어긋났다. 문서는 B를 `ACTIVE`, K를 `SHADOW`로 설명했지만 Registry의 안전 기본값은 공동계좌 `ACTIVE` 0개, B/C/F/G/I/J `SHADOW`, A/D/E/H/K `RETIRED·OFF`였다. 과거 연구 결과와 현재 실행 상태가 섞이면 사용자가 전략이 켜졌는지와 검증이 끝났는지를 오해할 수 있다.

## 결정

1. 등록된 각 전략은 불변 `StrategyResearchContract` 하나를 갖는다.
2. 계약은 명시적 strategy version, 필요한 공개시장 데이터, 최소 warmup, 진입 가설, 반증 조건, edge-decay 정책, 공동·독립 PAPER 위험예산, 대상 종목, point-in-time 미래정보 방지와 연구 Source ID를 포함한다.
3. 기존 전략 API 행에 계약을 평탄하게 추가하고 한국어 전략 상세 drawer에서 쉬운 이름으로 표시한다. lifecycle와 change reason은 기존 동적 설정값을 사용해 정적 연구 계약과 현재 운용상태를 함께 보여 준다.
4. Source ID는 `docs/20_RESEARCH_FOUNDATIONS_AND_ADAPTATION.md`의 1차 근거 catalog에 실제로 존재해야 하며 backend 회귀에서 모든 전략을 대조한다.
5. micro 전략 공통 warmup은 건강한 종목별 공개시장 10초와 현재 이전 prefix 통계를 요구한다. J는 동일 종목 prefix 호가기울기 32표본, K는 완성 1시간봉 200개를 명시한다.
6. 미래정보 방지는 현재 event timestamp 이전 이력, 평가 후 snapshot 추가, stale·sequence-invalid·미래 timestamp fail-closed를 명시한다. K는 진행 중 1시간봉을 제외한다.
7. 현재 lifecycle의 진실은 실행 Registry를 기준으로 문서를 교정한다. 과거 거래·연구 증거·계좌는 삭제하거나 다시 쓰지 않는다.
8. 연구 근거는 가설 출처다. Source ID 존재, 테스트 PASS 또는 descriptor 공개를 수익성 증거로 해석하지 않는다.
9. 전략 임계값·evaluator·모드·비용·TP·SL·위험예산 상수·Governor·PAPER 체결·실제주문 0 경계는 변경하지 않는다.

## 결과

- 비전문가도 각 전략이 어떤 공개데이터를 기다리고 무엇이 가설을 깨는지 상세 화면에서 확인할 수 있다.
- 구현·테스트·문서가 동일한 11개 전략 연구 계약을 사용한다.
- 현재 `SHADOW`와 `RETIRED·OFF` 상태가 과거 연구 설명과 섞이지 않는다.
- 계약 변경이 evaluator 변경을 대신하지 않으며, 전략 수익성은 계속 `NOT_PROVEN`이다.

## 검증 경계

수정 전 표적 backend·frontend 회귀는 descriptor와 상세 화면 필드 부재를 각각 재현했다. 현재 미배포 소스에서 backend 437, frontend 60, fixture 18, Playwright desktop·tablet·mobile 3과 정적검사·build·PAPER safety·security·저장소 위생이 PASS했다. Playwright 첫 실행 3건은 `위험예산` label과 value를 함께 찾은 비엄격 test locator 때문에 실패했고 exact label selector로 고친 뒤 최종 3건이 PASS했다. 설치 서비스는 아직 기준 commit을 실행하므로 실제 8870 화면과 API, 배포 후 원장, GitHub main·Actions는 `NOT_RUN`이다. 기준 commit의 6시간·24시간 observer는 계속 진행 중이며 수익성은 `NOT_PROVEN`이다.
