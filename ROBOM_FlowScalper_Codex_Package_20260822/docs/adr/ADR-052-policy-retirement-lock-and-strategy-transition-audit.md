# ADR-052. 정책 퇴역 잠금과 전략 설정 전환 감사

## 상태

승인. 2026-08-27.

## 배경

활성 원장의 정책 퇴역 전략에는 과거 SHADOW revision과 현재 RETIRED revision이 함께 보존돼 있다. 이는 재현성에 필요하지만, backend rollback은 정책 퇴역 여부를 확인하지 않아 과거 SHADOW revision을 복원할 수 있었다. 격리 테스트에서 해당 요청은 수정 전 HTTP 200으로 적용됐다.

UI는 모든 `lifecycle=RETIRED`를 정책 퇴역으로 취급했다. 따라서 연구 정책으로 잠긴 전략뿐 아니라 사용자가 일반 전략을 OFF로 바꾼 경우에도 다시 SHADOW·ACTIVE를 누를 수 없었다. 반대로 정책 퇴역 상세의 과거 revision 복원 버튼은 남아 있어 화면과 backend 보호가 서로 반대였다.

전략 설정 원장은 revision·actor·reason을 보존했지만 이전·새 상태와 관련 Run, 요청·응답 revision, 되돌릴 수 있는지 여부가 같은 행에 없었다. PAPER 진입 의도 전환에도 `occurred_ts_ms`와 `cause_code`가 payload에서 빠져 있었다.

## 결정

1. `policy_reactivation_locked`를 Registry가 전략 ID에서 계산해 API와 UI에 명시한다. 단순한 OFF·RETIRED 표시만으로 정책 잠금을 추론하지 않는다.
2. 비용후 연구로 정책 퇴역한 전략의 rollback은 backend에서 fail-closed한다. 과거 revision과 원장 수치는 삭제하지 않는다.
3. 정책 잠금이 없는 일반 전략은 사용자가 OFF로 바꾼 뒤에도 revision·확인·감사를 거쳐 SHADOW·ACTIVE로 되돌릴 수 있다.
4. 신규 strategy-setting payload에 transition ID, 이전·새 복합상태, 발생시각, 원인, 한국어 설명, actor, Run·전략·계좌·종목, 요청·응답 revision과 reversibility를 추가한다.
5. 이전·새 복합상태는 lifecycle, mode, LONG, SHORT와 manual lock을 한 문자열로 고정한다. 전체 설정 필드는 기존 payload에도 그대로 남긴다.
6. USER_UI·AUTO_GOVERNOR·RECOVERY actor를 직접 기록한다. 과거 호환용 `changed_by=MIGRATION`은 원본 필드로 보존하되 감사 actor는 실제 복구 단계인 `RECOVERY`로 표시한다.
7. 사용자 설정, rollback, Governor와 정책 migration은 각 전략 revision별 고유 transition ID로 불변 incident를 남긴다. 원자 champion 교체에서 여러 전략이 바뀌면 전략별 행으로 분리한다.
8. PAPER 진입 의도 전환에는 payload 발생시각과 원인 코드를 추가한다.
9. 과거 행을 재작성하거나 storage schema를 변경하지 않는다. 전략 신호·임계값·체결·비용·TP·SL·위험예산·계좌·Governor gate와 실제주문 0 경계는 바꾸지 않는다.

## 결과

- 정책 퇴역을 과거 revision 복원으로 우회할 수 없다.
- 일반 사용자 OFF는 정책 퇴역과 구분돼 되돌릴 수 있다.
- UI·API·strategy-settings 원장과 incident가 같은 revision 전환을 설명한다.
- 자동 Governor와 복구 migration도 동일한 actor·원인·revision 감사 계약을 사용한다.

## 검증 경계

격리 API 테스트는 수정 전 정책 우회 HTTP 200을 재현하고 수정 후 422 fail-closed와 설정 불변을 검증한다. 단위·통합·복구·UI·Playwright는 설정 원장, incident, 화면 잠금과 일반 OFF 복구를 검증한다. 현재 설치 서비스는 기준 commit을 계속 실행 중이므로 실제 배포 후 정책 잠금 화면과 신규 strategy transition 원장 행은 `NOT_RUN`이다.
