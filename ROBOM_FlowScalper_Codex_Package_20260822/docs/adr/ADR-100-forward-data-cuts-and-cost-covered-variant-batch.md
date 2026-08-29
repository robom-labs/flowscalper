# ADR-100. 순방향 데이터 누적과 비용회수형 후보 묶음

## 상태

채택한다. 해결된 결함의 재현검사, 동결된 공개시장 데이터와 전략시험 이력을 삭제하지 않고
누적한다. 새 파라미터 후보는 기존 100후보의 ID·정의·결과를 수정하지 않는 별도 manifest로만
실행한다. 이 결정은 PAPER 연구 속도를 높이기 위한 계산·검증 계약이며 수익성 증거가 아니다.

## 문제

장시간 수집한 `LIVE_PUBLIC` 자료가 있어도 실행 중 파일을 연구 입력에 섞거나, 이미 끝난 같은
전략·파라미터·데이터 시험을 이름만 바꿔 다시 돌리면 순방향 검증이 아니다. 전략 수를 빠르게
늘리려고 동결 100후보를 직접 수정하면 이전 결과와 새 결과의 계보도 끊어진다.

Wave 111 이전 100후보 진단에서는 F20의 과거 VWAP 계산이 실제 lookahead가 아닌데도 기본 경로와
재귀 감사 경로의 보관창이 각각 640봉·320봉이라 불일치로 판정됐다. 또한 현재 초단기 전략은 작은
목표가 BASE 13bp·STRESS 25bp 비용을 회수하지 못한 채 거래 수만 늘릴 수 있으므로, 목표승률만
높이는 청산 변경은 허용할 수 없다.

## 결정

1. `scripts/freeze_live_public_archive_cut.py`는 안정 대기시간이 지난 완결 Parquet만 새 연구 cut에
   포함한다. 파일 footer, 행 수, 이벤트 범위, batch checksum, 파일 SHA-256과 동결 전후
   inode·크기·mtime·ctime을 검증하고 동일 manifest를 `.research-pins/`에 원자적으로 기록한다.
   전체 읽기는 단일 연구 resource lock, OS 낮은 우선순위, 기본 4MiB/s 제한과 LIVE 원장 쓰기 우선
   공유잠금 아래에서만 수행하며 기존 증거를 덮어쓰지 않는다.
2. retention은 유효한 pin에 포함된 파일을 건너뛴다. pin이 변조되거나 스키마가 잘못되면 삭제를
   계속하지 않고 fail-closed로 중단한다. 실행 중 새 파일이 생기는 것은 허용하되 동결 대상 파일
   자체가 바뀌면 cut 생성을 실패시킨다.
3. F20 기본·재귀 feature builder는 같은 640개 과거 완료봉을 사용한다. 재현검사는 320봉을 넘긴
   뒤에도 두 경로의 snapshot이 완전히 같은지 확인한다.
4. E06은 기존 `EXIT_MODULES` 5개와 동결 20×5=100후보에 추가하지 않는다. F17~F20에만 붙인
   `COST_COVERED_EARLY_TP_RUNNER_V1` 별도 4후보 묶음으로 둔다.
5. E06은 진입 전에 1R 손절, 0.8R에서 70% 부분익절, 3R에서 30% 종료, TP1 뒤 ATR 2.5 trailing과
   비용 반영 본전 floor를 확정한다. BASE·STRESS에서 TP1 순보상이 양수이고 가중 순보상이 최소
   1.2R일 때만 계획을 허용한다. 이후 실제 PAPER 체결은 기존 executable bid·ask 깊이·수수료·
   슬리피지 경로를 그대로 사용한다.
6. `evidence/COST_COVERED_EXIT_VARIANT_MANIFEST.json`은 원본
   `STRATEGY_100_TRIAL_MANIFEST.json`의 내부 checksum과 파일 checksum을 부모 계보로 고정하고,
   E06 실행코드·feature·비용·위험·screening source checksum을 별도로 고정한다.
7. screening 집계기는 명시적으로 전달된 사전등록 trial 묶음만 허용한다. 기본 호출은 종전 100개
   계약과 수치를 그대로 유지하고, E06 호출은 정확히 4개·8개 BASE/STRESS 독립계좌만 계획한다.
8. 같은 가설·파라미터·dataset·코드·비용 지문의 완료시험은
   `RESEARCH_TRIAL_HISTORY.jsonl`에서 차단한다. 새 데이터 시험은 이전 불변 Run·checksum을 모두
   포함하면서 종료시각이 엄격히 뒤로 늘어날 때만 허용한다.
9. 재현 가능한 결함을 수정할 때는 결함의 입력·실패 불변조건을 단위검사와
   `config/regression_contracts.json` 앨커에 같이 고정한다. 이후 기능 변경은 해당 앨커를
   제거·우회하거나 구버전 코드를 다시 넣지 못하며, 이를 지키지 못하면 새 Wave를
   완료로 판정하지 않는다.
10. 후보 수를 빨리 늘리는 것과 같은 데이터를 반복 소비하는 것을 분리한다. 다양한
   가설은 작은 사전등록 batch로 병렬 비교하되, 같은 후보의 추가 검증은 새로 완결된
   `LIVE_PUBLIC` 시간 구간이 누적된 뒤에만 순방향 갱신으로 실행한다.

## 최신 외부연구 대조

2026-08-26 공개된 [Point-in-Time Audit Before Alpha](https://arxiv.org/abs/2608.25348)는 Binance
BTC perpetual 공개 archive에서 event time, publication time과 availability time을 분리했다. 거래,
mark, index와 realized funding을 핵심 stream으로 제한한 뒤에도 전수 감사가 겉보기 통과율을 크게
낮췄고, 역사 holdout 후보는 비용 후 net Sharpe가 음수였다. 특히 공개 OI의 publication time을
검증하지 못해 선택입력에서 제외했다. 따라서 ROBOM도 OI를 당장 방향신호로 추가하지 않고, 먼저
정확한 point-in-time 보존·가용시각 계약을 갖춘 뒤 별도 사전등록한다.

[How Much Sharpe is Illusory?](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7350238)는
137개 Binance USDT perpetual의 표준 factor를 nested walk-forward, 비용과 DSR로 다시 평가했을 때
단순 평가가 Sharpe를 크게 부풀리고 기준 설정에서 여섯 factor 모두 deflation을 통과하지 못했다고
보고했다. [Anatomy of a Null Result](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7085378)도
대규모 설정탐색의 독립 forward 전이가 거의 없음을 보고했다. 후보 개수를 성과로 보지 않고 작은
사전등록 묶음과 순방향 데이터 갱신을 우선하는 직접 근거로 사용한다.

[Risk Control as the Durable Edge](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7345542)는
변동성 극단 진입거부와 이익 ratchet이 특정 구간 손실을 줄일 수 있지만 항상 수익을 내는 전략은
아니라고 명시한다. 이를 즉시 방향전략으로 복제하지 않는다. E06 비교 뒤에도 조기 손실과 비용이
집중되면, 기존 신호를 만들거나 뒤집지 않는 `VOLATILITY_EXTREME_ENTRY_REFUSAL_V1`을 별도 작은
다중시험 family로 사전등록한다.

[Explainable Patterns in Cryptocurrency Microstructure](https://arxiv.org/abs/2602.00776)는 여러
Binance Futures 자산에서 OFI·spread·trade feature의 설명 패턴이 비슷할 수 있음을 보였다. 현재
F17~F20과 입력이 겹치므로 CatBoost나 새 전략 이름을 먼저 추가하지 않는다. 단순 규칙 후보가 같은
BASE/STRESS 비용과 독립 holdout을 통과한 뒤에만 frozen model artifact가 있는 별도 연구선으로
검토한다.

[Order Flow Imbalance and Short-Horizon BTC/USDT Returns](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7227998)는
같은 walk-forward 절차로 표본을 7일에서 17일로 늘리는 동안 OFI의 OOS 결론이 두 번 뒤집혔음을
보고했다. 따라서 F17~F20의 짧은 양수 구간이나 높은 승률을 바로 생존자로 올리지 않고,
새 `LIVE_PUBLIC` cut을 시간순으로 계속 늘려 결론 반전 여부를 기록한다.

[Machine Learning-Based Bitcoin Trading Under Transaction Costs](https://arxiv.org/abs/2606.00060)는
10bp 비용에서 단순 방향 매매가 실패하고 예측폭이 비용을 넘을 때만 진입하는 filter가 회전율을
낮춘다고 보고했지만, 모델 우월성은 bootstrap으로 확정되지 않았다. 이를 E06의 비용회수
진입거부를 유지하는 근거로 쓰되, 어떤 학습모델도 즉시 운영 전략으로 승격하지 않는다.

[Short-Horizon Directional Non-Predictability in Cryptocurrency Perpetual Futures](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7306538)는
20개 코인·40 물론 기술·주문흐름·심리·거시정보를 합친 40,857개 스냅샷에서도 단기 방향성이
강건하게 예측되지 않았다고 보고했다. 따라서 인터넷 전략 수를 늘리거나 LLM 투표를 붙이는 것을
성과로 해석하지 않고, 실행 전 사전등록과 순방향 실패 보존을 더 엄격히 유지한다.

## 실행 순서

1. Wave 111 공통 `NONE` baseline과 전체전략 TP1 가능성 후보를 같은 commit·manifest·archive byte로
   한 번씩만 비교한다.
2. E06 manifest와 runner의 PAPER·부모계보·source checksum·동적 4후보 집계를 단위검사한다.
3. 대형 replay가 끝난 뒤 단일 resource lock과 15% 협조 CPU 상한 아래 E06 4후보를 동일
   Train·Validation 입력에서 한 번 실행한다. LIVE 500ms 지연·queue·누락·저장 오류를 함께
   감시하고 위반 시 결과를 승격하지 않은 채 중단한다. 결과는 `NOT_PROVEN`에서 시작하고 Final
   OOS를 열지 않는다.
4. 현재 Run의 안정된 완료파일을 새 순방향 cut으로 pin한 뒤, 과거 F03~F16의 기간부족 후보를 새
   dataset version에서 다시 평가한다. 이전 불변 Run과 결과는 삭제하지 않는다.
5. 30개 독립 시장기회, BASE·STRESS 양수 기대값과 Profit Factor, bootstrap 하한, DSR, PBO,
   drawdown·종목·레짐·시간 분산을 모두 통과한 후보만 최대 10개 생존목록 비교 대상이 된다.

## 검증과 증거 경계

- manifest·실행·F20 parity·retention pin·회귀계약 테스트 PASS는 코드 계약 증거다.
- 짧은 replay나 작은 양수 표본은 수익성 또는 70% 승률 증거가 아니다.
- 실제 주문, private API, 인증, API Key, secret, wallet, 입출금과 runtime AI 주문판단은 계속 0이다.
- 6시간·24시간 장시간 조건을 실제로 채우지 않으면 `NOT_RUN`, 충분한 자연표본과 모든 gate가 없으면
  `NOT_PROVEN`으로 남긴다.
