# ADR-078. 실행가능 호가 기반 부분익절 러너와 활성화형 trailing 상태 머신

- 상태는 `ACCEPTED_IMPLEMENTATION_NOT_RUNTIME_PROMOTED`다.
- 적용 범위는 내부 PAPER 실행·복구·리플레이 계약이다.
- 실제 주문, private API, API Key, wallet, 런타임 AI 경로는 추가하지 않는다.

## 문제

기존 `PaperPortfolioEngine`은 실제 bid·ask 깊이와 지연을 적용하고, TP1·TP2 부분체결,
수수료 반영 손익분기 stop, 재시작 payload를 지원한다. 그러나 trailing runner의 활성화,
유리한 executable 가격, 단조 stop, 부분익절 상태, giveback과 전이 감사가 하나의 명시적
계약으로 묶여 있지 않았다. 기존 `PROTECTED`·`EXIT_PENDING` 수명주기만으로는 차트·원장·
리플레이가 runner의 세부 상태를 같은 의미로 표시하기 어렵다.

## 결정

`backend/app/execution/trailing.py`에 다음 PAPER 전용 상태를 둔다.

1. `ENTRY_PENDING`
2. `INITIAL_PROTECTION`
3. `PROFIT_ACTIVATION_PENDING`
4. `TRAIL_ARMED`
5. `PARTIAL_TP_PENDING`
6. `RUNNER_ACTIVE`
7. `TRAIL_EXIT_PENDING`
8. `CLOSED`

LONG은 신선하고 sequence-valid한 best bid만 최고 유리가격과 trail trigger에 사용한다.
SHORT은 같은 조건의 best ask만 최저 유리가격과 trigger에 사용한다. last price, mark,
candle high·low는 실행 trigger에 쓰지 않는다. stale, sequence-invalid, 중복, 과거순서
event는 favorable mark와 trail을 움직이지 않는다.

trail은 LONG에서 내려가지 않고 SHORT에서 올라가지 않는다. initial stop과 수수료 반영
breakeven보다 불리하게 이동하지 않는다. trigger 뒤 종료는 기존 `PaperExecutionEngine`의
지연과 실제 depth 소진을 그대로 사용하므로 gap에서 trail 가격 체결로 꾸미지 않는다.
거부·미체결·부분체결이면 남은 PAPER 수량을 계속 보호한다.

ATR·Chandelier·구조형 정책의 참조값은 신호시각 전에 끝난 동일 종목·동일 시간구간의
연속 완성봉에서만 계산한다. 시간구간 누락, 중복·비정렬 시각, 잘못된 OHLCV, 미완성봉을
거부한다. ATR과 구조 stop, 마지막 완성시각과 시간구간을 진입 전 `CandidatePlan`에 고정하고,
참조가 한 시간구간보다 오래됐으면 새 진입을 거부한다. `CHANDELIER_STRUCTURE`는 ATR과 구조
stop이 모두 있어야 하며, `STRUCTURE`는 구조 stop이 없으면 실행되지 않는다.

`EDGE_ADAPTIVE`는 단일 tick에 반응하지 않는다. 기본 사전등록 계약은 OFI, aggressor flow,
microprice, spread 중 서로 다른 adverse 사유 두 개 이상이 건강한 데이터에서 3초 연속된
경우에만 ATR multiplier를 좁힌다. adverse 시작시각·사유·활성상태도 복구 payload와
`TRAILING_EDGE_STATE_UPDATED` 감사에 보존한다.

각 전이는 `TRAILING_STATE_TRANSITION` append-only audit 행으로 노출한다. 행에는 계좌·거래·
전략버전·비용 profile·event/receive 시각·activation·유리한 bid/ask·현재/이전 trail·초기
stop·fee breakeven·원/실현/runner 수량·MFE·MAE·peak/current unrealized·giveback·actor·
reason·data health를 기록한다.

상태는 같아도 새 유리한 executable 호가 또는 단조 trail이 바뀌면
`TRAILING_MARK_UPDATED`를 기록한다. 이 행은 `state_checksum`을 포함하며 recovery snapshot
쓰기 대상으로 분류한다. 따라서 활성화 뒤 다음 상태전이가 오기 전에 재시작해도 마지막 저장
favorable mark와 trail로 복구한다. 단순 미실현손익 변화만으로는 snapshot을 쓰지 않는다.

직전 event 하나가 아니라 최근 256개 event ID를 복구 payload에 보존한다. 같은 ID가 인접하지
않게 재등장해도 trail을 다시 움직이지 않는다. 더 오래된 event는 event-time 역행 규칙이
차단하며, 고정 크기 window로 snapshot 크기를 제한한다.

`CandidatePlan.trailing_policy`가 명시된 사전등록 PAPER 후보만 이 상태 머신을 사용한다.
기존 전략에는 정책을 자동 부여하지 않는다. 따라서 구현 완료가 전략 승격이나 수익성 증거를
뜻하지 않으며 ACTIVE는 계속 0개다.

실제 평균 진입체결과 왕복 수수료를 반영한 breakeven이 TP1 기반 activation에 도달하거나
넘으면 해당 계좌 진입만 `TRAILING_ACTIVATION_NOT_FEE_SAFE`로 거부한다. 이 검증은 체결 주문,
포지션, 위험예산을 계좌에 반영하기 전에 끝내며 거부 뒤 pending 위험과 명목금액은 모두 0으로
복구한다. Stage 1 전체 archive에서 STRESS 계좌 3건이 이 사유로 안전 거부됐고 runner 전체는
계속 실행됐다. activation 기준이나 비용을 낮춰 결과를 만들지 않는다.

완료된 `PaperTrade`와 `ShadowTrade`에는 activation 시각과 실제
`RUNNER_ACTIVE` 전환 시각을 따로 고정한다. 부분익절 필수 정책은 activation 후에도
TP1 체결 전이면 runner로 계산하지 않는다. 함께 최고 미실현손익, peak giveback,
runner 비용후 순기여, trigger 뒤 실제 depth 체결에서 발생한 가격차이 비용과 최종 state
checksum을 보존한다. 메인과 전략별 계좌는 같은 변환기를 거쳐 불변 SQLite payload로
저장하며, 현재 전략버전 `LIVE_PUBLIC` 분석과 거래 상세 UI가 동일 필드를 사용한다.
전략 통계는 TP1 체결률, activation 횟수, 실제 runner 시작 횟수, runner 순기여,
MFE 캡쳐, giveback 중앙·P90, trailing stop 종료, activation 전 stop과 activation 후
순손실을 분리한다. 이 지표는 trailing 동작을 감사하기 위한 값이지 성과 승격
근거를 대신하지 않는다.

## 복구와 호환성

PAPER recovery schema는 5로 올린다. 기존 schema 1~4는 trailing 정책이 없는 상태로 계속
복구한다. schema 5는 상태 머신, transition history와 이미 외부 audit으로 내보낸 cursor를
보존해 재시작 뒤 과거 전이를 중복 기록하지 않는다.

복구 시 transition 연결, 허용 상태전이, 시각 순서, 계좌·거래·전략·종목 식별자와 결정적
transition ID checksum을 다시 검증한다. 수량, 단조 trail, fee-adjusted 보호경계, activation
시각, strict boolean, 데이터 건강상태와 adverse 사유 목록도 검증한다. 하나라도 다르면
fail-closed한다.

## 검증 계약

다음은 관련 테스트의 필수 범위다.

- LONG bid·SHORT ask 기준과 양방향 단조성.
- activation 이전 무동작과 TP1 부분익절·runner 전이.
- activation 시각과 실제 runner 시작 시각의 분리·순서·복구.
- 중복·stale·sequence invalid·out-of-order 무효화.
- trail exit 거부와 남은 수량 보호.
- 부분체결 뒤 반복 종료.
- payload roundtrip과 checksum.
- 연속 완성봉 ATR·구조 참조와 incomplete·gap·stale 참조 거부.
- adverse 사유 두 개·3초 지속 전후의 adaptive trail과 복구.
- 문자열 boolean, 잘못된 reason 목록과 단조성 위반 payload의 fail-closed.
- 같은 입력 replay의 동일 전이·TP1·runner milestone과 최종 checksum.
- 실제 주문 호출 0.

직접 trailing·포트폴리오 회귀 50건과 두 번의 runner 결함 수정 뒤 관련 회귀 62건을 통과했다.
Stage 1은 Train 6·Validation 2 Run의 보존 거래 77건을 끝까지 처리했으며 Final OOS는 열지
않았다. 새 설치 릴리스의 자연 PAPER 체결·복구·실제 브라우저·6시간·24시간은 별도 검증 전이라
아직 `NOT_RUN`이다.
