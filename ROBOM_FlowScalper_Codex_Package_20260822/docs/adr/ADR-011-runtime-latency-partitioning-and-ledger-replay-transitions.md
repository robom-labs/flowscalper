# ADR-011. LIVE 지연 격리·Run별 archive·PAPER 원장 전환 리플레이

## 결정

1. 공개시장 Parquet은 `venue/run/date/symbol/hour/event_type`로 분리한다. 이전 Run과 현재 Run이 같은 partition에 섞이지 않게 하되, replay는 Run 필터로 기존 자료도 계속 읽는다.
2. Binance 체결 스트림은 종목·방향·250ms bucket 안에서 수량과 명목가치를 정확히 합산한 VWAP 이벤트로 병합한다. 원본 수신 건수와 출력 건수를 telemetry에 따로 남긴다.
3. 전략 입력 history 통계와 호가 상위 20단계 계산은 snapshot 한 번만 수행해 6전략×LONG/SHORT 평가에서 공유한다.
4. 시장 저장은 2,000건 batch로 event loop 밖에서 수행하고 flush 횟수·최근·최대 시간을 진단에 표시한다. 종료 시 임계치 미만 잔여 buffer도 반드시 flush한다.
5. 완료 거래 집중 replay는 저장 시장 이벤트뿐 아니라 저장 PAPER 원장의 진입·종료 전환을 같은 결정적 시간축에 넣는다. 시장 마지막 이벤트가 종료 체결보다 먼저 끝나도 종료 marker와 CLOSED 상태를 숨기지 않는다.

## 이유

실제 장시간 Run 뒤 기존 Run archive와 고빈도 체결이 같은 저장·평가 경로에 몰리면서 UI에 표시되는 처리지연 p95가 수만 ms까지 상승했다. 또 DEMO 완료 거래의 마지막 시장 이벤트가 종료 시각보다 앞서면 `종료` 이동이 비활성화됐다. 두 문제 모두 전략 기준이나 PAPER 체결 규칙을 낮추지 않고 처리량과 시간축 정합성을 고쳐야 했다.

## 불변조건

- 체결 병합 전후의 방향별 수량·명목가치·VWAP는 같다.
- 실제 bid·ask 깊이를 사용하는 PAPER 체결과 비용·지연·위험 기준은 바꾸지 않는다.
- DEMO telemetry는 LIVE 지연·wide/deep 상태를 상속하지 않는다.
- replay 원장 전환은 저장된 PAPER trade/fill에서만 만들며 실제 주문이나 private API를 추가하지 않는다.
- 실제 주문, 인증 header, API Key, secret, wallet 경로는 계속 0이다.

## 검증

- backend 162 PASS, frontend 29 PASS, Ruff·mypy·ESLint·TypeScript·build·security PASS.
- 실제 공개시장 통합 180초에서 처리지연 p95 최대 458ms, queue 최대 2, reconnect·gap·drop·persistence fault 0.
- 실제 브라우저에서 50개 제어와 데스크톱·태블릿·모바일 집중 replay를 조작했고 실패 0.
- 6시간·24시간 실행은 이번 결정 검증에서 `NOT_RUN`이다.
