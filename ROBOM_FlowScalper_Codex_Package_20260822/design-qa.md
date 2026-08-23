# 3차 화면 디자인 QA

검수 기준은 사용자가 제공한 포지션 집중 참고 이미지와 실제 구현 화면을 같은 캔버스에 둔 `evidence/screenshots/phase03-reference-vs-position-focus.png`이다. 참고 화면의 정보 구조만 사용했고 브랜드, 수치, 10배 레버리지와 주문 UI는 복제하지 않았다.

## 최종 판정

| 우선순위 | 발견 내용 | 조치 | 상태 |
|---|---|---|---|
| P0 | 실제 저장 거래를 열 때 SQLite candle에 차트 `time`이 없어 실제 Chrome 화면이 비는 문제 | replay focus 응답에서 candle 숫자와 `time`을 정규화하고 실제 Chrome에서 다시 열었다. | 해결 |
| P1 | TP1이 기존 자동축 밖으로 나가 계획선이 안 보이는 문제 | entry·TP1·TP2·초기/현재 stop을 autoscale 범위에 포함했다. | 해결 |
| P1 | 참고 이미지의 핵심인 좌측 계획·중앙 대형 chart·우측 순손익 위계가 약함 | 176px / 중앙 가변 / 208px 3열과 비용 포함 순손익 우선 rail로 정리했다. | 해결 |
| P1 | 태블릿·모바일에서 양쪽 rail이 chart 폭을 줄일 수 있음 | chart 폭을 고정하고 계획·손익을 side sheet로 열게 했다. drawer 전후 chart box 불변을 E2E로 확인했다. | 해결 |
| P2 | 리플레이 핵심 이동 버튼이 일반 browser 기본색으로 보여 제품 톤과 어긋남 | 기존 dark token의 border·text·background를 적용했다. | 해결 |
| P0 | 390px READY 화면에서 공개시장·샘플 시작 버튼이 숨고 DEMO의 LIVE 아님 표시도 숨음 | READY toolbar를 두 줄로 고정하고 샘플 PAPER·LIVE PAPER 진실 banner를 phone/tablet에서 영구 표시했다. | 해결 |
| P0 | 완료 거래 뒤 시장 틱이 없으면 집중 replay의 `종료` 이동이 비활성화됨 | 저장 PAPER 원장의 entry/exit transition frame을 결정적으로 추가하고 26-frame 실제 화면에서 종료 이동을 재검증했다. | 해결 |
| P1 | phone/tablet 하위 메뉴가 거래 집중 제목 상단을 덮음 | `has-secondary` navigation 높이를 flow에 예약하고 replay viewport 높이를 다시 계산했다. | 해결 |

## 실측

- 시장 화면 1408×900은 root 1408×900, chart panel 1116×780, canvas 1020×666이다.
- 거래 집중 재생 1408×900은 root 1408×900, 중앙 chart panel 984×796, canvas 894×682이다.
- desktop·tablet·mobile 모두 document 가로 overflow 0이다.
- 실제 진입·TP1·초기 손절 label은 같은 viewport에 표시된다. 초기 손절과 현재 손절이 같을 때는 중복 price label 대신 rail에 `변경 없음`을 표시한다.
- 사용자 화면에 매수·매도·실제 주문 버튼은 없다.
- 실제 in-app browser에서 50개 제어를 클릭했고 실패 0이다. 390×844 샘플·LIVE 진실표시, 820×1180과 1408×900 집중 replay를 별도 캡처했다.

## 남은 관찰 경계

실제 공개시장 자연신호 PAPER fill은 이번 30분 실행에서 발생하지 않았다. 따라서 LIVE 자동 집중 화면은 `NOT_OBSERVED`이며, 자동 전환·다중 선택·반응형 상호작용은 결정적 fixture E2E로 검증했다.
