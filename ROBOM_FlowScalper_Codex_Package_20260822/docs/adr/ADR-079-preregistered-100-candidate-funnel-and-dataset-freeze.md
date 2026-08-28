# ADR-079. 100후보 사전등록 funnel과 역사·Forward 데이터 동결

- 상태는 `ACCEPTED_STAGE1_EXECUTED_NO_CANDIDATE_SELECTED`다.
- 적용 범위는 offline PAPER 연구, event replay, full PAPER replay와 이후 LIVE_PUBLIC
  SHADOW 승격 계약이다.
- 런타임 Registry, 현재 PAPER 계좌와 실제 주문 안전경계를 변경하지 않는다.

## 문제

100개 중 결과가 가장 좋아 보이는 전략만 고르면 multiple-testing과 사후 선택으로 우연한
승자를 만들 수 있다. 후보마다 다른 데이터·비용·exit를 사용하거나 실패 trial을 지우면
PBO·DSR·bootstrap과 OOS 결과도 의미가 없다. 반대로 100개를 모두 LIVE deep-book 경로에
넣으면 현재 공개시장 수집과 PAPER 보호를 느리게 만들 수 있다.

## 결정

1. 정확히 20 alpha family와 E01~E05 exit module을 곱한 100개 configuration을 결과 보기
   전에 고정한다. 각 trial은 고유 번호·ID·source·근거등급·parameter·비용·lifecycle·blocker를
   보존한다.
2. F01 SIHO exact와 F02 보수적 해석은 공개 entry·exit·timeframe·trailing·sizing이 확정되지
   않아 10개 모두 `BLOCKED`다. 누락값을 추측해 100개 수를 채우지 않는다.
3. manifest는 candidate Registry뿐 아니라 F03~F20 evaluator, 완료봉·point-in-time universe·
   미세구조 feature builder, 연구시간구간, trailing 상태 머신, Candidate Planner, dataset
   freeze와 exporter의 SHA-256을 함께 고정한다. 실제 archive event를 읽어 수신순으로
   정렬하고 호가·체결 입력으로 변환하는 공통 intraday reader도 같은 source bundle에 묶는다.
   이 중 하나가 바뀌면 새 manifest hash가 필요하다. evaluator는 사전등록 parameter 전체가
   정확히 일치하지 않으면 실행을 거부한다.
4. 과거 screening은 `evidence/WAVE34_INTRADAY_RESEARCH.json`에서 이미 실행된 완전 archive
   범위를 source로 삼되, 모든 Run의 범위·건수·종목·파일 checksum을 현재 archive에서 다시
   계산해 정확히 일치할 때만 `FROZEN_HISTORICAL_FORWARD_PENDING`으로 만든다.
5. Train·Validation·Final OOS는 서로 겹치지 않는 시간순 Run 집합으로 동결한다. Forward
   LIVE_PUBLIC은 미래 표본이며 과거 Run을 소급 배정하지 않는다. Stage 1의 최대 25개 선택은
   Train·Validation만 사용하고 Final OOS는 full PAPER finalist 최대 10개가 고정될 때까지
   봉인한다. Final OOS는 한 번만 열며 그 결과로 규칙·parameter·후보를 다시 조정하지 않는다.
6. 자원 funnel은 offline screening 100 → event replay 최대 25 → 공통 실행경로 full PAPER
   replay 최대 10 → LIVE_PUBLIC SHADOW 3~6이다. 이전 단계를 통과하지 못하면 다음 단계는
   비워 둔다.
7. BASE·STRESS 비용, bid·ask·latency·depth·partial fill, no-lookahead, walk-forward,
   purge·embargo, PBO·DSR·bootstrap과 concentration gate는 결과 보기 전에 고정한다.
8. 실패·무신호·BLOCKED trial도 삭제하지 않는다. ACTIVE는 0을 유지하며 runtime 설정을
   연구 결과가 자동으로 바꾸지 않는다.
9. purge·embargo와 진입 마감은 trial horizon에 맞춘다. `MICRO_SCALP`은 180,000ms,
   `FAST_INTRADAY`는 3,600,000ms, `INTRADAY_SWING`은 21,600,000ms를 각각 경계 양쪽에서
   제외하고, 진입 뒤 같은 최대보유시간과 1,000ms 체결 여유가 남는 경우에만 진입을 허용한다.
   네 Validation fold 중 하나라도 비면 해당 horizon은 `DATASET_WINDOW_INSUFFICIENT_*`로
   실패한다. 더 짧은 보유시간을 가장하거나 경계를 줄여 결과를 만들지 않는다.
10. source Run마다 PAPER 계좌를 1,000 USDT로 다시 초기화하지 않는다. trial·비용 프로필별
    현재자산·최고자산·연속손실을 다음 시간순 Run으로 넘기고, 보존 거래 순손익 및 ShadowLedger와
    일치하지 않으면 보고서 생성을 거부한다.
11. 데이터 구간 부족·종료 시점 미체결로 FAILED된 trial은 선택과 PBO에서는 제외하되,
    실행한 BASE·STRESS 계좌의 평가·신호·거부·거래·비용 결과는 숨기지 않고
    `FAILED_PRESERVED`로 남긴다. 이 계좌를 EXECUTED 계좌 수에 섞지 않는다.
12. 네 Validation fold는 고정 사전등록 parameter의 anchored·rolling 창을
    각각 생성한다. symbol·venue·regime·신호 시점 volatility·bull/bear/range·
    BASE/STRESS cost holdout은 그룹 부족·라벨 누락을 성공으로 바꾸지 않는다.
    정확한 창·라벨·상태 계약은 ADR-081을 따른다.

## 증거 상태

- `evidence/STRATEGY_100_TRIAL_MANIFEST.json`은 100개, eligible 90개, blocked 10개,
  ACTIVE 0개, LIVE SHADOW 0개와 실제 실행 source 25개의 SHA-256을 기록한다. runner는
  manifest 내부 checksum뿐 아니라 현재 25개 파일을 다시 해시해 하나라도 바뀌면 실행 전에
  거부한다. 현재 manifest SHA-256은
  `0b4340ae832e21754ad55c05ef5ce4ae1b948a874a9eef4f3b572a932eac72fc`이다.
- F03은 완료 1h·4h 추세와 완료 15m setup, F09는 2개 완료봉 AVWAP 확인, F07은
  Wilder ATR recursive Supertrend, F15는 24h 수익률의 변동성 비율, F20은 반전 방향의
  bid/ask refill을 사용한다. 이 구현은 아직 screening 결과가 아니다.
- screening 집계는 90개 실행가능 trial의 BASE·STRESS 1,000 USDT 독립계좌와 10개
  BLOCKED trial을 모두 요구한다. 누락·중복·비용 불일치·숨긴 PBO trial은 보고서 생성을
  거부하며 최대 25개 선택은 Validation 표본·비용·bootstrap·DSR·PBO·집중도 gate 뒤에만
  가능하다. Stage 1 입력에 Final OOS 거래가 하나라도 있으면 fail-closed로 거부한다. 계획된
  독립계좌는 100×2=200개이고, 현재 공개규칙 미확정으로 BLOCKED인 SIHO 10개 trial의 20개
  계좌는 실행하지 않으므로 실행가능 계좌는 90×2=180개로 따로 기록한다.
- FAILED trial의 양쪽 계좌는 보존 집계에 남지만 EXECUTED 계좌 수나 선택 수에
  포함하지 않는 회귀를 작성했다. 최종 전체 회귀 수치는 릴리스 전 다시 기록한다.
- 실제 screening runner는 동일 결과 checksum에서 paired trailing ablation,
  4-fold walk-forward와 multiple-testing 보고서를 한 번에 생성한다. 5개 exit 모듈이
  같은 Run·signal event·종목·방향에서 모두 끝나지 않은 경우 ablation을 비교하지
  않는다. BASE와 STRESS의 bootstrap·DSR도 독립적으로 충족해야 한다.
- 최종 소스의 200,000 event bounded benchmark에서 90 trial·180 PAPER 계좌를 실제
  234.054764초에 처리했다. 854.501 events/s·484.331 후보평가/s, 종료 RSS
  673.688MiB·peak RSS 687.734MiB, 완료거래 0이며 상태는 PASS다. 단독 실행했지만
  queue·persistence·dashboard·replay를 계측하지 않은 동기 benchmark라는 한계와 수익성
  `NOT_PROVEN`을 같이 기록한다.
- Stage 1은 Train 6·Validation 2의 8개 Run을 끝까지 실행했다. 100 trial 중 MICRO 20개만
  `EXECUTED`, FAST 55개와 SWING 15개는 각각 horizon별 네 fold가 남지 않아
  `FAILED_PRESERVED`, SIHO 10개는 `BLOCKED`다. 관찰 계좌는 180개, EXECUTED 40개,
  FAILED 보존 140개이며 보존 거래는 77건이다. Final OOS는 `SEALED_NOT_USED_FOR_SELECTION`이다.
- 20개 실행 trial도 Validation 표본이 프로필별 0~2건이고, 현재 instrument metadata는
  point-in-time이 아니며 recursive 비교 982,240회 중 1,080회 warmup mismatch가 있었다.
  bootstrap·DSR·PBO·집중도·기간·종목·레짐 gate를 통과한 trial은 0개다. 따라서 event replay
  선택 0, ACTIVE 0, LIVE SHADOW 0, 수익성 `NOT_PROVEN`을 유지한다.
- paired trailing 완전 cohort는 0개라 ablation은
  `EXECUTED_INSUFFICIENT_PAIRED_COHORTS`, walk-forward와 multiple-testing은
  `EXECUTED_VALIDATION_ONLY`다. event/full PAPER replay와 LIVE SHADOW는 앞 gate 후보가
  없으므로 `NOT_RUN/BLOCKED_GATE`이며 Final OOS를 열지 않는다.
- dataset freeze는 13개 Run·2,690,582 events의 범위·건수·종목·파일 SHA-256을 다시
  검증해 `FROZEN_HISTORICAL_FORWARD_PENDING`으로 생성했다. dataset manifest SHA-256은
  `61765a668d29b950e50fd8c6bccc372b7e747885e0a0870206411b0e46165e20`이다.
- manifest와 dataset 생성, benchmark PASS는 수익성·replay·recovery PASS가 아니다.
- secondary report와 bounded benchmark는 실제 실행했다. screening manifest SHA-256은
  `cdbee9dbcd6b402192d8a09e8b3cb936d7c6f9c7b17271ca74a0b9a00f436ad9`이며 audit·77개
  JSONL 거래 연결 checksum과 9개 manifest 내부 checksum을 독립 재검증했다.
- 첫 전체 실행은 실제 체결 뒤 TP1 activation이 비용 본전보다 불리한 STRESS 계획에서
  예외로 실패했다. 계좌 반영 전 안전 거부와 사유코드를 추가했다. 두 번째는 계산 완료 뒤
  `Counter`를 `asdict()`한 tuple key 때문에 감사 JSON 직렬화가 실패했다. 문자열 key dict
  변환 회귀를 추가했다. 두 실패 모두 성공 결과로 덮지 않고 원인을 보존했으며 세 번째 전체
  실행에서 최종 여섯 결과 파일까지 원자 생성했다.
- 전략 100 runner가 공유하는 archive reader·호가/체결 변환기를 trial manifest source
  checksum에 추가했다. 비유한 값과 0 이하 공개 체결은 candle·feature 전에
  `FeatureInputError`로 거부하고 trial 실패 근거를 보존한다. 관련 회귀와 manifest
  current-source mismatch 회귀, 전체 pytest가 PASS했고 manifest·dataset도 다시 생성했다.
- 기존 Wave 34 source evidence의 두 Validation Run은 약 26.997분·25.574분이고 이를
  반으로 나눈 뒤 위 horizon 계약을 적용한 정적 preflight에서 `MICRO_SCALP`만 네 fold에
  각각 약 4.482·4.482·3.770·3.770분의 진입구간이 남는다. `FAST_INTRADAY`와
  `INTRADAY_SWING`은 usable fold가 0이므로 실제 dataset freeze·screening 전에도 데이터
  보강 없이는 실패해야 한다. 이 계산은 기존 source 문서 범위 점검이며 screening 결과나
  수익성 증거가 아니다.

## 승격 결과

모든 통계·운영 gate가 통과해도 이 저장소는 PAPER 전용이다. 실전 자금 상태는 자동으로
READY가 되지 않고 최대 `INDEPENDENT_REVIEW_REQUIRED`까지만 올릴 수 있다. 현재는
`FUNDING_READINESS = NOT_READY`다.
