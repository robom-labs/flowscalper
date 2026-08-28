# SIHO 현재 공개전략 판정

## 현재 판정

- `SIHO_CHANNEL_VERIFICATION = VERIFIED`
- `CURRENT_STRATEGY = UNCONFIRMED`
- `SIHO_PUBLIC_CURRENT_BASELINE_V1 = NOT_REGISTERED`
- `PROFITABILITY = NOT_PROVEN`
- 조사 checkpoint는 2026-08-27T14:49:28Z다.

공식 채널 동일성은 [SIHO_CHANNEL_IDENTITY.json](../../evidence/SIHO_CHANNEL_IDENTITY.json)에
기록했다. 공개 채널 목록 59개 중 YouTube `동영상` 탭 32개와 `Shorts` 탭 27개의 video id는
[SIHO_VIDEO_INDEX.json](../../evidence/SIHO_VIDEO_INDEX.json)에 색인했다. 현재 색인 상태는
`COMPLETE_IDS_PARTIAL_METADATA`이며 전체 영상의 설명·transcript·frame 분석은 아직 완료되지
않았다. [SIHO_FRAME_EVIDENCE_MANIFEST.json](../../evidence/SIHO_FRAME_EVIDENCE_MANIFEST.json)은
hydration 전 `NOT_RUN_PENDING_HYDRATED_SCOPE`다. 자산 수집과 실제 전체 영상 검토는 서로 다른
상태로 기록한다.

## 현재 직접 확인한 공개 내용

기준 영상 `1mJDNm4Yko4`와 최신 장문 영상 `cCLI_ge6Tzg`, `RXKPiGnufOc`의 공개 설명은 같은
SHA-256 `c3d3113d0c91b1551e266e63f1191b1737bf4183e160956ebb8e6ca7678b5eaa`였다. 세 설명은
다음 개념을 언급한다.

- EMA 추세 분석.
- RSI 모멘텀 분석.
- Market Structure 분석.
- Retest 확인.
- 자동 Stop Loss.
- 자동 Take Profit.
- 자동 Trailing Stop.
- 리스크 관리.

이 목록에는 timeframe, EMA·RSI parameter, 진입식, 청산 우선순위, stop·target 거리,
trailing activation, retracement rate·distance, position sizing이 없다. 따라서 모두
`PUBLIC_AMBIGUOUS`이며 `EXACT_PUBLIC_RULE`이 아니다.

설명의 실제 계좌·실제 거래·성과 문구는 채널의 공개 주장일 뿐 독립 검증된 사실이나
FlowScalper의 성과가 아니다. 설명에 적힌 Bybit API, Flask webhook, 실제 주문 경로는 이
PAPER 전용 저장소의 구현 범위에서 제외한다.

## exact baseline 차단 상태

| 필수 항목 | 현재 상태 | 판정 |
|---|---|---|
| 현재 전략의 명시적 버전 | 확인되지 않음 | `CURRENT_STRATEGY = UNCONFIRMED` |
| LONG entry | 방향만 추정 가능 | `BLOCKED_MISSING_ENTRY_RULE` |
| SHORT entry | 확인되지 않음 | `BLOCKED_MISSING_ENTRY_RULE` |
| timeframe | 확인되지 않음 | `BLOCKED_MISSING_TIMEFRAME` |
| initial stop | 기능 언급만 있음 | `BLOCKED_MISSING_EXIT_RULE` |
| take profit·partial take profit | 기능 언급만 있음 | `BLOCKED_MISSING_EXIT_RULE` |
| trailing activation·distance·rate | 기능 언급만 있음 | `BLOCKED_MISSING_TRAILING_PARAMETER` |
| position sizing | 리스크 관리 언급만 있음 | `BLOCKED_MISSING_POSITION_SIZING` |

## 남은 확인 절차

1. 32개 장문 영상의 exact upload metadata와 설명 checksum을 고정한다.
2. 최신 30개와 전략 keyword 과거영상의 처음부터 끝까지 ASR timeline을 검사한다.
3. indicator 설정창, entry·stop·target, 전략 변경 선언이 보이는 frame을 timestamp와 함께
   대조한다.
4. 최신 명시적 선언과 과거 규칙의 변경 연표를 만든다.
5. exact 규칙이 완성된 경우에만 baseline을 만들고, 그렇지 않으면 차단 판정을 유지한다.
