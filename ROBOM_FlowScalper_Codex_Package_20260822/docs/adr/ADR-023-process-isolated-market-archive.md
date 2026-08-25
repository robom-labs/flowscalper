# ADR-023. 공개시장 Parquet 저장 프로세스 격리

## 상태

Accepted, 2026-08-25.

## 맥락

공개시장 이벤트 수신과 전략평가는 asyncio event loop에서 진행하고, Parquet 저장은 `asyncio.to_thread`로 옮겨 둔 상태였다. 그러나 Python JSON 정규화·row checksum·Arrow table 생성·zstd 압축·fsync를 묶은 2,000건 flush는 같은 Python 프로세스의 GIL과 CPU를 경쟁한다.

실제 `run-e2cd64bac738` 970.3초 관찰에서 한 flush가 1,579ms 걸린 시각과 20개 TRADE 이벤트의 보정 처리지연 1,597.6~1,629.5ms 구간이 겹쳤다. 예정된 15분 WebSocket 회전은 성공했고 비정상 reconnect·gap·drop·저장 fault는 0이었으므로, 연결 회전보다 프로세스 내부 저장 작업이 실행경로 지연을 만든 직접 원인이었다.

첫 process 격리본 `run-64d8e843f38f`에서도 17개 TRADE 이벤트가 1,502.1~1,577.2ms로 늦어졌다. 저장 archive 순서로 이 burst 직전 이벤트는 52,497번째였고 기존 메모리 보관은 10,001번째부터 매 2,500건마다 과거 객체 2,500개를 한꺼번에 삭제했으므로 52,501번째 일괄 해제 경계와 정확히 겹쳤다. process flush와 별개로 메인 event loop의 대량 객체 해제도 제거해야 했다.

같은 핫패스에는 최대 2,000개 계획 거부 설명을 모은 뒤 500개를 한꺼번에 삭제하는 목록도 남아 있었다. 화면에는 이 전체 과거 목록을 직접 사용하지 않으므로 보존 상한은 유지하되 같은 종류의 일괄 객체 해제를 제거해야 했다.

## 결정

1. 장시간 persistence worker의 시장 이벤트 직렬화·checksum·Parquet 압축·fsync를 AnyIO worker process에서 실행한다.
2. 메인 프로세스에는 배치 인출·오류 재적재·SQLite 불변 manifest와 종목별 건수 반영만 남긴다.
3. process가 실패하면 기존과 같이 최대 10,000개 시장 이벤트와 5,000개 캔들을 재적재하고 PAPER 신규진입을 fail-closed한다.
4. 명시적인 종료·replay 직전 동기 flush 계약은 유지한다. 장시간 자동 worker만 process 격리 경로를 사용한다.
5. 메모리 최근 이벤트는 최대 10,000개를 유지하되 `deque(maxlen=10_000)`으로 매 수신마다 가장 오래된 1건만 교체한다. 과거 2,500개 객체 일괄 삭제를 없앤다.
6. 계획 거부 설명은 최대 2,000개를 유지하되 `deque(maxlen=2_000)`으로 가장 오래된 1건만 교체한다. 500개 일괄 삭제를 없앤다.
7. 시장 이벤트별 canonical JSON과 SHA-256 row checksum으로 이미 계산한 batch checksum을 Parquet 파일명 digest로 재사용한다. 동일 archived row 전체를 파일명 때문에 두 번째로 JSON 직렬화하지 않는다.
8. 파일 checksum, batch checksum, row checksum, SQLite manifest, replay 읽기 검증과 보존정책은 바꾸지 않는다.
9. 실제 주문, private API, 인증, API Key, secret과 wallet 경로는 계속 없다.

## 검증

- 2,000개 시장 이벤트를 실제 별도 process worker로 저장하는 동안 event loop heartbeat가 계속 진행되는지 검사한다.
- 최근 이벤트 10,000개를 유지하면서 10,001번째 append가 과거 1개만 교체하는지 검사한다.
- 계획 거부 2,000개를 유지하면서 2,001번째 append가 과거 1개만 교체하는지 검사한다.
- 저장 batch 1개, 종목별 event count 2,000, buffer 0, fault·drop 0을 확인한다.
- 기존 외장 Parquet 불변 manifest, row·batch checksum 변조 거부와 replay 회귀를 다시 실행한다.
- 실제 8870에서 새 Run을 시작해 여러 flush와 기본 15분 회전 전후의 실행경로 critical lag, 진입잠금, reconnect, gap, drop, fault를 관찰한다.

## 결과와 한계

최종 `run-b85a51c5daed` 16분 관찰은 event 2,150→160,850, 메모리 이벤트 10,000 고정, 계획 회전 1·전체 reconnect 1·비계획 reconnect 0, drop·gap·persistence fault 0이었다. process flush 최대 5,591ms에도 실행경로 rolling P95는 최종 343.373ms였고 진입잠금은 외부 공개 거래 스트림의 순간 임계지연에만 fail-closed로 작동한 뒤 자동 해제됐다. 내부 52,501 일괄 폐기 경계는 critical 증가 없이 두 번 통과했다.

worker process serialization에도 인수 전달과 SQLite manifest 비용은 남는다. 외부 공개 거래 스트림은 순간적으로 늦을 수 있으므로 지연 0을 보장하지 않고, 임계 데이터로 진입하지 않는 안전잠금과 자동회복을 유지한다. 6시간·24시간 soak와 전략 수익성은 별도 검증이다.
