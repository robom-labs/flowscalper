# 실제 자금 투입 준비상태

`FUNDING_READINESS = NOT_READY`

## 현재 결론

이 저장소는 공개 암호화폐 시장데이터를 입력으로 사용하는 PAPER 연구 프로그램이다. 실제
주문 endpoint, private API, API Key, secret, wallet과 자금이체 기능을 추가하지 않는다.

현재 어떤 전략도 실제 자금 투입의 근거를 충족하지 않았다. 기존 전략의 비용 후 수익성은
`NOT_PROVEN`이며 ACTIVE 전략은 0개다. 새 SIHO baseline은 exact 공개규칙이 완성되지 않아
등록하지 않았다. 20×5 Registry·현재 25개 실행 소스·13개 Run의 2,690,582개 event를
각각 checksum으로 동결했고 200,000-event 자원 benchmark는 PASS했다. Stage 1 archive는
Train 6·Validation 2 Run을 완료했지만 선택 후보가 0개이므로 현재 상태는
`SCREENING_EXECUTED_NO_CANDIDATE_SELECTED_NOT_PROVEN`이다.

## 준비 게이트

| 게이트 | 현재 상태 | 근거 또는 남은 작업 |
|---|---|---|
| 실제 주문·private API 0 | `PASS` | 저장소의 영구 PAPER 안전 경계. 새 연구코드도 runtime 비활성 |
| SIHO exact 공개규칙 | `BLOCKED` | 59개 metadata·설명과 장편 32개 ASR timeline·2,589개 frame 자산 수집은 완료했지만 timeline·frame·전체 영상 내용 검토는 0개라 entry·exit·timeframe·trailing·sizing exact 근거가 아직 없음 |
| 100후보 사전등록 | `PASS` | 정확히 20 alpha × 5 exit, eligible 90·SIHO BLOCKED 10·ACTIVE 0·LIVE SHADOW 0. 현재 25개 실행 소스 checksum을 묶은 manifest SHA-256은 `0b4340ae832e21754ad55c05ef5ce4ae1b948a874a9eef4f3b572a932eac72fc` |
| 동일 dataset·split 고정 | `PASS_HISTORICAL_FORWARD_PENDING` | 13개 Run·2,690,582 events의 범위·건수·symbol·archive checksum을 재검증했다. Train 6·Validation 2·봉인 Final OOS 5이며 미래 LIVE_PUBLIC Forward는 아직 비어 있음 |
| 100후보 offline screening | `EXECUTED_NO_SELECTION` | 8 Run·77거래. EXECUTED 20·FAILED 보존 70·BLOCKED 10, 선택 0, Final OOS 봉인. Validation 표본·기간·다양성·no-lookahead metadata·recursive·bootstrap·DSR·PBO gate 미충족 |
| 최대 25 event replay | `NOT_RUN_BLOCKED_GATE` | screening 선택 후보 0 |
| 최대 10 full PAPER replay | `NOT_RUN_BLOCKED_GATE` | event replay 후보 0 |
| 3~6 LIVE SHADOW | `NOT_RUN_BLOCKED_GATE` | 승격 후보 0 |
| BASE expectancy > 0 | `NOT_PROVEN` | 충분한 독립 OOS·LIVE 표본 없음 |
| STRESS expectancy > 0 | `NOT_PROVEN` | 충분한 독립 OOS·LIVE 표본 없음 |
| bootstrap 95% 하한 > 0 | `NOT_PROVEN` | 후보별 최소표본 없음 |
| PBO ≤ 0.20 | `NOT_PROVEN` | trial 실패와 표본 부족으로 조합 0·PBO 산출 불가 |
| DSR ≥ 0.95 | `NOT_PROVEN` | Validation gate 통과 0 |
| drawdown·집중도 gate | `NOT_PROVEN` | 충분한 OOS·LIVE 표본 없음 |
| replay checksum·recovery | `PARTIAL` | 새 trailing lifecycle·완료봉·수신순·복구 단위 및 통합 회귀는 PASS했다. 실제 공통 ReplayEngine 결정성과 새 설치 릴리스 복구는 아직 NOT_RUN |
| 수정 전 post-quarantine 6시간 | `FAIL_BASELINE_PRESERVED` | 구 불변 release에서 21,600.025초·720표본을 채웠으나 최종 데이터 건강 잠금, 처리·event-loop·WAL 지연, gap·resync 기준으로 FAIL. 새 코드의 PASS로 재사용하지 않음 |
| 수정 후 post-quarantine 6시간 | `NOT_RUN` | 새 불변 릴리스 배포·짧은 실제 회귀 뒤 실제 21,600초를 별도로 채워야 함 |
| 24시간 안정성 | `NOT_RUN` | 실제 86,400초 미실행 |
| 독립 코드·금융위험 검토 | `NOT_RUN` | 저장소 외부의 별도 사람 검토 필요 |

## 상태 변경 규칙

위 기술·통계·운영 게이트가 모두 통과해도 이 파일을 자동으로 `READY`로 바꾸지 않는다. 모든
근거가 충족되면 최대 `FUNDING_READINESS = INDEPENDENT_REVIEW_REQUIRED`까지만 변경한다.
실제 자금 판단은 별도의 독립 코드감사, 손실 감내범위, 법률·세금·거래소 위험 검토가 필요하다.

PAPER 수익, 높은 승률, 짧은 양의 구간과 테스트 통과는 실제 수익을 보장하지 않는다.
