# 변경기록

이 파일은 사용자가 알아야 할 중요한 변화만 짧게 기록한다. 세부 구현 이력은 Git commit, 검증 증거와 GitHub Release에 보존하고 과거 소스 복사본은 현재 트리에 두지 않는다.

형식은 [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)의 취지를 따르며, 버전 번호는 [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html)을 제품의 사용자 동작·저장 schema·로컬 API 계약에 적용한다. `-paper`는 실제 주문이 없는 PAPER 전용 제품임을 뜻한다.

## 아직 배포하지 않음

- 장시간 LIVE 처리지연 병목을 Run별 archive, 250ms 방향별 체결 VWAP 병합, 전략 통계 공유, 상위 호가 계산과 비동기 2,000건 저장으로 개선했다.
- 모바일 시작 버튼과 LIVE/샘플 PAPER 상태를 항상 보이게 하고 하위 메뉴가 거래 집중 제목을 가리던 반응형 겹침을 제거했다.
- DEMO가 LIVE 지연·wide/deep 수치를 상속하지 않게 했고, 완료 거래 replay의 진입·종료 PAPER 원장 전환을 항상 이동할 수 있게 했다.
- 첫 화면을 5개 메뉴의 compact 시장 작업공간으로 바꾸고 Binance USD-M 전체 catalog와 Upbit KRW 관찰 전용 catalog를 추가했다.
- 기본 3분봉 200개, 거래량 overlay, MA10·MA20, 동적 RSI·MACD pane과 고정 종목 rail을 구현했다.
- deep 20 안전 회전, 전략별 종목 성과, 실제 fill 기반 포지션 집중 3열 화면과 0.5~80배 거래 단위 replay를 연결했다.
- 태블릿·모바일에서 chart 폭을 유지하는 계획·손익 sheet와 비용 포함 순손익 rail을 추가했다.
- 실제 주문, private API, 인증 경로는 계속 0이며 Upbit는 PAPER 실행에 사용하지 않는다.

- Strategy Registry를 A-F 6개로 확장하고 A/B는 ACTIVE, C/D/E/F는 SHADOW로 시작한다.
- 전략별 BASE/STRESS 12개 1,000 USDT PAPER 계좌가 서로 다른 종목을 3개까지 독립 체결한다.
- 위험기반 최대 5배 상한, 1.5% 총 계획위험, 계좌별 손실, drawdown, cooldown 잠금을 추가했다.
- 호가 쏠림 E와 강한 체결 흐름 F는 실제 event timestamp 500ms 지속성을 확인한다.
- REVERSION 70/30, TREND 40/60 청산과 schema v2 다중 포지션 복구, v1 읽기 호환을 추가했다.
- 시작·새 Run을 즉시 `202` 응답하는 작업 상태로 바꾸고 중복·충돌·취소·재시도를 명확히 표시한다.
- 초보자용 홈, 6개 전략·12개 BASE/STRESS 계좌 리그, 진행 거래, 고급 터미널을 분리했다.
- 고정 스캐너와 상세 drawer, MA·EMA·VWAP·볼린저·RSI·MACD, 증분 차트 갱신, 현재로 돌아가기와 전체화면을 추가했다.
- 실제 주문, private API, 인증 경로는 계속 0이다.

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
