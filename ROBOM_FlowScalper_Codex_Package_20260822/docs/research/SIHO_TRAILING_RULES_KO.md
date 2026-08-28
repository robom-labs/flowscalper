# SIHO 공개 Trailing 규칙 조사와 연구 경계

## SIHO 원본규칙 판정

공개 설명은 `자동 추적손절(Trailing Stop)` 기능을 언급하지만 activation price, distance,
rate, favorable price 기준, 부분익절, runner 비율, update 주기와 같은 실행 규칙을 공개하지
않는다. 현재 판정은 다음과 같다.

- 증거등급은 `PUBLIC_AMBIGUOUS`다.
- `BLOCKED_MISSING_TRAILING_PARAMETER`다.
- `SIHO_PUBLIC_CURRENT_BASELINE_V1`에는 아직 넣지 않는다.

## 별도 연구가설

아래 내용은 SIHO가 사용한다고 확인된 규칙이 아니다. 공식 Bybit 문서와 기존 PAPER 실행
안전 원칙을 바탕으로 비교할 `RESEARCH_HYPOTHESIS`다.

- activation 전에는 initial stop을 유지한다.
- LONG favorable mark는 신선하고 sequence-valid한 executable best bid만 사용한다.
- SHORT favorable mark는 신선하고 sequence-valid한 executable best ask만 사용한다.
- LONG trail은 절대 내려가지 않고 SHORT trail은 절대 올라가지 않는다.
- fixed distance, fixed percentage, ATR Chandelier, 완료된 structure, edge-decay adaptive를
  서로 다른 exit module로 사전등록한다.
- activation 후보와 TP1·runner 비율은 결과를 보기 전에 제한된 ablation으로 고정한다.
- trigger 뒤에는 지연 후 실제 반대편 depth를 소진하고, partial fill의 남은 수량을 계속
  보호한다.
- 같은 candle 안의 TP·trail 순서를 알 수 없으면 보수적 결과를 사용한다.

공식 Bybit 공개문서는 distance 방식의 LONG을 `highest price - distance`, SHORT를
`lowest price + distance`로, rate 방식의 LONG을 `highest price × (1 - rate)`, SHORT를
`lowest price × (1 + rate)`로 설명한다. activation price는 선택사항이다. 이 공식은 구현
참고 근거일 뿐 SIHO exact rule의 증거가 아니다.

출처는 2026-08-27 확인한 Bybit 공식 문서
`https://www.bybit.com/en/help-center/article/Trailing-Stop-Order-Perpetual-and-Futures-Trading`다.
