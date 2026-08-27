# ADR-070 replay I/O와 LIVE 저장 안정화

- 상태는 승인이다.
- 날짜는 2026-08-27이다.

## 맥락

Wave 69의 30분 무오염 기준선은 PASS했지만 485,283-event 고정 replay를 같은 외장 저장장치에서 실행하면 Parquet 읽기·checksum과 활성 원장의 `synchronous=FULL` 쓰기가 I/O를 경쟁했다. replay를 `nice(19)`로 낮춘 것만으로는 SQLite 원자 커밋과 WAL checkpoint가 충분히 전진하지 못했고, 반대로 저장 burst가 커지면 queue와 LIVE 지연이 증가했다. 완료하지 못한 replay 시도는 수익성이나 재현성 증거가 아니다.

서비스 재시작 직후에는 큰 원장의 현재 전략버전 cache가 아직 준비되지 않았는데 과거 또는 부분 통계가 화면에 잠깐 나타날 수 있었다. 이는 수치 자체보다 통계 범위의 진실성 문제다.

## 결정

- full replay는 frozen durable prefix, 제한된 worker thread와 chunk, 저우선순위 process, LIVE observer 자동중단 계약을 유지한다.
- archive 압축은 background I/O를 유지하지만 짧은 ledger 커밋은 worker niceness 10에서 끝낸다.
- 시장 저장 burst는 1,000 events로 제한하고 PASSIVE WAL checkpoint는 4 flush마다 수행한다.
- 실패한 flush나 checkpoint는 buffer 복원과 신규 PAPER 진입 잠금으로 fail-closed한다.
- 현재 전략버전 cache가 준비되기 전에는 승률·기대값·순위를 표시하지 않는다.
- 대형 replay와 별개로 작은 불변 공개시장 Run을 실제 브라우저에서 불러오고, 같은 조건 검증·input checksum·종단 결과·다음 이벤트 전진을 확인한다.
- 전략 상태는 승률로 즉시 바꾸지 않는다. 11전략·22계좌는 같은 입력을 공유하고 ACTIVE 0, SHADOW 6, RETIRED/OFF 5를 유지하며, 최소 30건·비용후 OOS·STRESS·강건성 gate 전에 승격하지 않는다.
- 전략 임계값, TP1·TP2·SL, 체결, 비용과 위험예산은 성능 측정 결과를 좋게 보이게 하기 위해 변경하지 않는다.

## 결과

최종 commit `667ad7b61587cf9e0a58d57150fe53f677d92d5d`의 300.031초 관찰은 event +21,706, 전략평가 +79,224, queue 최대 1, 처리/체결 p95 최대 55.290/90.192ms였다. flush 최대 14.831초, checkpoint 최대 8.274초였고 비계획 reconnect·gap·resync·drop·저장결함은 0이었다.

실제 브라우저에서 `run-c74c67ff5976`의 ETHUSDT 125 events를 14.635초에 재검증했다. input checksum을 표시했고 288번 평가에서 조건 미충족으로 거래 0을 유지했으며 실제 주문과 인증 경로는 0이었다. 대형 485,283-event replay 완료와 변경 후 6시간·24시간은 `NOT_RUN`, 전략 수익성은 `NOT_PROVEN`이다.
