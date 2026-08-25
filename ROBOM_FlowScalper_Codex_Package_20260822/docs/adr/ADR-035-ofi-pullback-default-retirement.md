# ADR-035. OFI 눌림 전략의 기본 중지

- 상태: Accepted
- 날짜: 2026-08-25
- 범위: `OFI_CONTINUATION_PULLBACK_V1` 기본 mode와 전략 revision

## 배경

Wave32의 시간순 저장 `LIVE_PUBLIC` 선별에서 D는 train BASE 4건 모두 손실, 기대값 -14.289bp였고 후기 holdout 자연신호는 없었다. 이후 더 늦은 실제 Run `run-04a41901147e`에서 BNBUSDT LONG과 ENAUSDT LONG 자연 BASE 거래가 각각 20.146초와 29.580초 보유 후 종료됐다. 두 거래는 후보, 실제 진입, TP, SL, 수량과 비용을 진입 전에 확정했고 1~2초 비정상 종료가 아니었다.

BNB는 가격 방향이 맞아 gross +0.07770 USDT였지만 왕복 수수료·슬리피지 뒤 -0.09981930 USDT였다. ENA는 gross -0.0170800 USDT, 비용후 -0.096500292 USDT였다. 두 BASE 합계는 0승 2패, 기대값 -10.885530bp, PF 0, 순손익 -0.196319592 USDT다. STRESS도 0승 2패, 기대값 -23.087757bp, 순손익 -0.453259184 USDT였다.

## 결정

1. D를 기본 `SHADOW`에서 `OFF`로 내리고 구현 revision을 `2026-08-25-wave33`으로 분리한다.
2. D의 evaluator, 문서화된 조건, 불변 과거 거래, BASE·STRESS 계좌, LONG·SHORT 제어와 사용자의 명시적 재활성화는 삭제하지 않는다.
3. B만 `ACTIVE`, C/F/G/I/J는 `SHADOW`, A/D/E/H는 `OFF`를 기본으로 한다.
4. 부족한 표본을 만들기 위해 D 조건이나 공통 비용 게이트를 낮추지 않고 다른 전략을 자동 승격하지 않는다.

## 한계

저장 train 4건과 후기 자연 BASE 2건은 수익성이나 장기 성능을 판단하기에 부족하다. 이 결정은 높은 승률을 주장하는 것이 아니라, 현재까지 6건 모두 비용후 음수인 가설에 기본 PAPER 노출을 계속 허용하지 않는 보수적 제품 기본값이다. 30건 미만이므로 전략 순위는 계속 `NOT_PROVEN`이다.
