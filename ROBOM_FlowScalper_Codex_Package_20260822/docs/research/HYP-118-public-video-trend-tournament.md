# HYP-118. 공개 영상 기반 중단타 추세 12후보 사전등록

- 상태. `PREREGISTERED_BEFORE_EXECUTION`.
- 등록일. 2026-08-30.
- 연구범위. Binance USDⓈ-M 공개 완성 5분봉을 15분·1시간·4시간으로 집계한 PAPER 연구다.
- 고정 실행범위. UTC `2025-12-01` 이상 `2026-08-25` 미만.
- 후보 지문. `bb612fc58647ec4e91dca7736f027cb835336f719eee0e5f7c8a927ad4969657`.
- 실행 전 코드 commit. `ea0af17b949ac1827c4b2b1e398ed561cc119a7b`.
- 성과 상태. `NOT_PROVEN`.
- 실자금 준비. `NOT_READY`.

## 연구 질문

유튜브와 TradingView에서 반복되는 중단타 개념 가운데 기존 ROBOM 124후보와 중복되지 않고
결정적으로 표현 가능한 유동성 훑기 후 복귀와 일목균형표 추세 재개를 같은 시장 입력, 같은 비용,
같은 시간순 gate로 비교했을 때 재현 가능한 양의 순기대값 후보가 남는가?

영상 제목의 `profitable`, `best`, `secret`, 조회 수, 좋아요, 게시자 수익과 승률은 입력도 정답도
아니다. 게시물은 가설 발굴 자료일 뿐이며 ROBOM 성과는 자체 PAPER 원장으로만 판정한다.

## 고정 후보 12개

두 계열마다 `LONG`, `SHORT`, `BOTH`를 분리하고 `BALANCED`, `SELECTIVE`를 적용한다.
따라서 2계열 × 3방향 × 2강도 = 12개다.

| 계열 | 주기 | 핵심 진입 | BALANCED | SELECTIVE |
|---|---:|---|---|---|
| 유동성 훑기 후 복귀 | 15m | 시장 추세 반대쪽 과거 고가·저가를 wick으로 넘은 뒤 범위 안 종가 복귀 | 12봉·0.75ATR 최대 훑기 | 24봉·0.45ATR·직전 봉까지 재돌파 |
| 일목균형표 눌림 재개 | 1h | 9·26·52 일목 선과 26봉 선행 구름이 정렬된 추세에서 눌림 후 재돌파 | 완화 레짐·TP 1.5R/4R | 강한 레짐·구름 완전 정렬·TP 2R/5R |

정확한 candidate ID와 임계값은
`scripts/research_public_video_trend_tournament.py`의 `PREREGISTERED_VIDEO_CANDIDATES`가 유일한
실행계약이다.

## 미래정보·체결·청산 계약

- 고정 12종목은 `BTCUSDT`, `ETHUSDT`, `BNBUSDT`, `SOLUSDT`, `XRPUSDT`,
  `DOGEUSDT`, `ADAUSDT`, `AVAXUSDT`, `LINKUSDT`, `DOTUSDT`, `LTCUSDT`,
  `BCHUSDT`다.
- 완성된 봉과 그 시점까지 존재한 데이터만 사용한다.
- 일목 선행스팬은 현재 화면 위치에 해당하는 26봉 전 계산값만 사용한다.
- 신호 다음 봉 시가에 진입하고 신호봉 종가로 소급 체결하지 않는다.
- 손절은 sweep 극값 또는 눌림·구름 구조와 ATR buffer로 정하고 진입 후 넓히지 않는다.
- 최초 위험거리는 0.65~4.0ATR만 허용한다.
- TP1 40%, TP2 60%이며 같은 봉에 손절과 목표가가 모두 닿으면 손절을 먼저 적용한다.
- 고정 최대보유시간과 일반 근거약화 청산은 사용하지 않는다.
- 데이터 끝까지 TP·SL이 닿지 않으면 `CENSORED_OPEN`으로 보존하고 승패에서 제외한다.
- BASE 13bp와 STRESS 25bp를 차감하고 후보별 최대 동시 2포지션·하루 2진입을 적용한다.
- 실제 주문, private API, API Key, 인증, secret, wallet과 입출금 경로는 0이다.

## 판정 계약

- 최소 180일, 50% Train·20% Validation·30% 진단 OOS와 경계별 7일 embargo를 사용한다.
- development 60건과 validation 20건 미만은 순위를 매기지 않는다.
- OOS 30건, BASE·STRESS 양의 기대값, PF, bootstrap 하한, DSR, PBO와 종목 집중 gate를 모두
  통과해야 역사 진단 통과로 표시한다.
- 역사 통과도 독립 미래 LIVE_PUBLIC 실제 bid·ask BASE·STRESS SHADOW 표본 30개 전에는
  `NOT_PROVEN`이다.
- 승률 70%는 탐색 진단일 뿐 승격 기준을 대신하지 않는다.

## 출처와 제외 경계

출처별 채택·병합·제외 판단은
[Wave 118 공개 영상 중복 대조](WAVE118_YOUTUBE_TRADINGVIEW_IDEA_MAPPING_KO.md)에 기록했다.
외부 Pine 코드, 비공개 지표, 주문블록 라벨과 게시자의 성과 숫자는 복사하지 않았다.

결과를 본 뒤 같은 ID의 임계값을 바꾸지 않는다. 결함 수정 또는 새 수치 가설은 새 가설 번호와
새 결과 파일로 분리하고 이전 원장과 실패 기록을 보존한다.
