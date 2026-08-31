# ADR-087. 증거 기반 70% 승률 운영 후보 정책

## 상태

승인·로컬 검증 완료, 배포 전. 2026-08-29.

V6 변경. 공통 70% 승격·퇴역·격리 조항은 `ADR-V6-strategy-family-and-four-page-user-interface.md`의 공통 비용·강건성 및 family별 win/payoff gate로 대체한다. 고유기회, BASE·STRESS 분리, 작은 표본 금지와 증거 보존 결정은 유지한다.

## 배경

사용자 목표는 비용 후에도 승률 70% 이상을 유지하는 PAPER 전략만 운영 후보로 남기고,
충분한 검증 뒤 70%에 못 미치는 전략은 중지하는 것이다. 기존 Governor는 표본 수,
기대값, Profit Factor, OOS 하한, DSR, PBO, 파라미터 강건성과 레짐 수를 검사했지만
BASE·STRESS 승률 70%를 명시적인 승격·생존 기준으로 검사하지 않았다.

승률만 높이기 위해 목표가를 줄이거나 손절을 키우면 수수료와 큰 손실을 숨길 수 있다.
또한 한 공개시장 기회에서 생성되는 BASE·STRESS 두 행은 독립 기회 두 건이 아니다.
따라서 70%는 기존 비용 후 강건성 기준을 대체하지 않고 추가하는 보수적 운영 문턱이다.

## 결정

1. `base_win_rate`와 `stress_win_rate`를 Strategy Governor 증거 계약에 추가한다.
2. 자동 승격은 현재 전략 버전의 자연 `LIVE_PUBLIC` 기회가 BASE·STRESS 각각 30건 이상이고 양쪽 관측 승률이 모두 70% 이상일 때만 검토한다.
3. 승률 기준을 통과해도 비용 후 기대값, Profit Factor, OOS 하한, DSR, PBO, 파라미터 강건성, 위험 계약과 독립 기간 기준을 모두 계속 적용한다.
4. BASE·STRESS 한 쌍은 하나의 공개시장 기회로 취급한다. 30건 기준을 두 비용 프로필을 합친 60행으로 부풀리지 않는다.
5. SHADOW·CHALLENGER는 BASE·STRESS 각각 30건, 최소 7일, 2개 레짐이 쌓인 뒤 어느 한 비용 프로필이라도 승률 70% 미만이면 `RETIRED`·`OFF`로 전환한다.
6. 퇴역은 전략 소스, 불변 거래, 계좌 결과와 설정 revision을 삭제하지 않는다. 별도 사전등록 연구와 새 증거 없이 과거 실패를 지우거나 같은 전략을 우수 후보로 다시 표시하지 않는다.
7. ACTIVE는 한 번의 흔들림으로 끄지 않는다. 새 표본이 추가된 평가 주기만 세며, 전체와 최근 50건의 BASE·STRESS 중 어느 한쪽이 두 평가 주기 연속 70% 미만이면 `QUARANTINED`·`OFF`로 안전 격리한다.
8. 1건 승리처럼 작은 100% 표본은 승격도 퇴역도 결정하지 않는다. 30건 미만은 계속 `NOT_PROVEN`이다.
9. 화면에는 기술 reason code 대신 기본 비용·보수 비용에서 70% 미달 또는 표본 부족이라는 쉬운 한국어를 표시한다.
10. 실제 주문, private API, 인증, API Key, secret, wallet과 입출금 경로는 계속 0이다.

## 결과

- 현재 ACTIVE 전략은 0개이므로 이 결정으로 실제 PAPER 대표 전략이 즉시 생기지 않는다.
- 현재 자연표본이 30건보다 적은 전략은 낮은 승률만으로 성급히 삭제하거나 퇴역시키지 않는다.
- 이후 70%를 통과한 전략도 기대값과 비용 강건성이 불충분하면 운영 후보가 될 수 없다.
- 이 정책과 단위·UI 테스트 통과는 수익성을 증명하지 않는다. 충분한 미래 자연표본 전에는 전체 프로젝트 수익성 상태를 `NOT_PROVEN`으로 유지한다.

## 검증 경계

동일 저장 공개시장 입력의 BASE·STRESS replay, 시간순 OOS, 실제 장시간 PAPER 관찰과 현재 버전 자연표본을 별도로 채워야 한다. 6시간·24시간은 실제로 경과한 증거가 없으면 각각 `NOT_RUN`으로 기록한다.

## replay 요약 계약 보강

2026-08-29 코드 감사를 통해 `research_runtime_strategy_replay.py`의 요약이 BASE·STRESS
각 30행과 관측승률 70%만으로 `ranking_eligible=true`를 만들 수 있음을 확인했다. 이는 이
ADR의 결정 3·4와 달랐다. 다음처럼 보강했다.

- BASE·STRESS 행을 합치지 않은 `signal_event_id` 기준 고유 시장기회도 30개 이상이어야
  관측 70% gate가 통과한다.
- BASE·STRESS 각각 비용후 기대값·순손익 양수와 Profit Factor 1 초과를 별도 gate로
  계산한다. 손실이 하나도 없는 양수 표본의 Profit Factor는 무한대로 취급한다.
- 이 단일 replay runner는 시간순 OOS 강건성, bootstrap 하한, DSR, PBO, drawdown과 독립
  미래 `LIVE_PUBLIC`을 계산하지 않으므로 결과가 아무리 좋아도 `ranking_eligible=false`를
  유지하고 누락 gate를 기계판독 blocker로 기록한다.
- 30개 BASE·STRESS 행이 모두 같은 한 신호를 반복한 fixture와, 30개 고유기회·80%·양수
  기대값 fixture를 모두 검사했다. 전자는 고유기회 gate에서 실패하고 후자는 승률·비용
  gate만 통과한 채 강건성 미평가로 승격되지 않는다.
