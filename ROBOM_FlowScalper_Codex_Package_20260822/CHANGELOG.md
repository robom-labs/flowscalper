# 변경기록

이 파일은 사용자가 알아야 할 중요한 변화만 짧게 기록한다. 세부 구현 이력은 Git commit, 검증 증거와 GitHub Release에 보존하고 과거 소스 복사본은 현재 트리에 두지 않는다.

형식은 [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)의 취지를 따르며, 버전 번호는 [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html)을 제품의 사용자 동작·저장 schema·로컬 API 계약에 적용한다. `-paper`는 실제 주문이 없는 PAPER 전용 제품임을 뜻한다.

## 아직 배포하지 않음

- 아직 기록된 변경이 없다.

## 0.2.0-paper — 2026-08-23

- 실제 공개시장 장시간 supervisor, 50개 wide·10개 deep 관찰, A/B/C/D Registry와 main·shadow PAPER 계좌를 연결했다.
- 보수적 bid·ask 체결, 불변 진입계획, 포지션·위험·SQLite v6·외장 Parquet·ReplayEngine·전략 성과를 종단 간 연결했다.
- 비전문가용 한국어 홈, 고정 scanner, 실제 candle·거래량·선택형 이동평균 chart와 macOS 자동복구를 구현했다.
- 다른 AI가 제품·요구·코드·검증을 파악할 수 있는 인계 메모와 업그레이드 요청 프롬프트를 추가했다.
- 단일 최신 소스, 짧은 변경기록, Git tag·Release 보존 원칙과 저장소 위생 자동검사를 추가하고 TypeScript 생성 파일을 제거했다.
- 실제 주문·private API·인증 경로는 계속 0이다.

## 0.1.0-paper — 2026-08-22

- credential 없는 로컬 PAPER 연구 도구의 안전 경계와 첫 기준선을 만들었다.
- 초기 fixture·공개시장 adapter·PAPER 원장·React 화면·검증 문서를 제공했다.
- 이후 기능과 UI는 0.2.0-paper의 현재 구현으로 대체됐다.
