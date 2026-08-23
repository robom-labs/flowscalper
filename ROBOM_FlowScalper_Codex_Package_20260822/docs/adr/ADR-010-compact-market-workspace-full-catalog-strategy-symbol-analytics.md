# ADR-010. Compact 시장 작업공간·전체 공개 catalog·거래 집중 재생

- 상태는 Accepted다.
- 날짜는 2026-08-23이다.

## 결정

1. 첫 화면은 5개 주 메뉴와 48px compact header를 쓰는 시장 작업공간이다. 종목 rail과 chart는 서로 크기를 바꾸지 않는다.
2. Binance USD-M의 활성 USDT perpetual 전체를 PAPER 실행 catalog로, Upbit KRW 전체를 관찰 전용 catalog로 읽는다. 인증·private API는 사용하지 않는다.
3. 기본 chart는 실제 3분봉 200개, 거래량 overlay, MA10·MA20이다. RSI·MACD pane은 선택할 때만 만들고 닫을 때 제거한다.
4. supervisor는 wide 50개 이상, deep 20개를 유지하고 15분 안전 회전에서 한 번에 최대 4개만 바꾼다. pin·open·pending 종목은 보호한다.
5. 실제 PAPER fill 뒤에는 `focus_positions`를 원본으로 공용 `PositionFocusWorkspace`를 연다. 후보나 pending은 자동 집중시키지 않는다.
6. 저장 거래 재생은 `ReplayFocusSession`과 `ReplayClock`을 사용한다. 미래 marker를 숨기고 0.5배부터 80배까지 같은 frame 순서를 사용한다.
7. 전략별 종목 성과는 비용 포함 표본 30건 전에는 순위를 만들지 않는다.

## 안전 결과

- PAPER 실행 거래소는 Binance USD-M 하나다. Upbit는 관찰만 하고 PAPER 포지션과 결합하지 않는다.
- 실제 주문, API Key, private API, secret, wallet endpoint와 수동 매수·매도 버튼은 계속 없다.
- 실제 공개시장 자연 fill이 없으면 threshold를 낮추지 않고 `NOT_OBSERVED`로 기록한다.

## 검증

결정적 backend·frontend·browser E2E, 실제 Chrome 1408×900, Binance·Upbit 공개 network smoke와 30분 wall-clock soak를 서로 분리해 기록한다. 6시간·24시간은 실행하지 않으면 `NOT_RUN`이다.
