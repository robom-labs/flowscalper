# SIHO 공개 매수·매도 규칙 조사표

## 증거 경계

현재 단계에서 정확한 매수·매도 공식은 확인되지 않았다. 아래 `확인됨`은 공개 설명에 해당
개념이 등장했다는 뜻이며, 전략 수치나 순서가 확인됐다는 뜻이 아니다.

| 항목 | 공개 설명에서 확인됨 | exact 값 | 증거등급 | 현재 처리 |
|---|---:|---|---|---|
| EMA 추세 분석 | 예 | `UNKNOWN` | `PUBLIC_AMBIGUOUS` | 영상 timeline 조사 |
| RSI 모멘텀 | 예 | `UNKNOWN` | `PUBLIC_AMBIGUOUS` | 영상 timeline 조사 |
| Market Structure | 예 | `UNKNOWN` | `PUBLIC_AMBIGUOUS` | 영상 timeline 조사 |
| Retest | 예 | `UNKNOWN` | `PUBLIC_AMBIGUOUS` | 영상 timeline 조사 |
| LONG 허용 | `UNKNOWN` | `UNKNOWN` | `UNKNOWN` | 차단 유지 |
| SHORT 허용 | `UNKNOWN` | `UNKNOWN` | `UNKNOWN` | 차단 유지 |
| higher/setup/trigger timeframe | 아니요 | `UNKNOWN` | `UNKNOWN` | 차단 유지 |
| closed-candle 조건 | 아니요 | `UNKNOWN` | `UNKNOWN` | 차단 유지 |
| exact long entry | 아니요 | `UNKNOWN` | `UNKNOWN` | `BLOCKED_MISSING_ENTRY_RULE` |
| exact short entry | 아니요 | `UNKNOWN` | `UNKNOWN` | `BLOCKED_MISSING_ENTRY_RULE` |
| signal invalidation | 아니요 | `UNKNOWN` | `UNKNOWN` | 차단 유지 |
| re-entry·cooldown | 아니요 | `UNKNOWN` | `UNKNOWN` | 차단 유지 |
| position sizing | 아니요 | `UNKNOWN` | `UNKNOWN` | `BLOCKED_MISSING_POSITION_SIZING` |

화면에 값이 보이더라도 발화나 최신 설명과 대조되기 전에는 `SCREEN_INFERENCE`로만 기록한다.
모호한 항목을 임의 숫자로 채우지 않으며, 별도 연구 변형을 만들 때는
`SIHO_INTERPRETATION_*`와 `RESEARCH_HYPOTHESIS`를 사용한다.
