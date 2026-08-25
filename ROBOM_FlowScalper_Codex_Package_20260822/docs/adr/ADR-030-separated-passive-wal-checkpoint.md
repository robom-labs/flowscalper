# ADR-030. COMMIT 경로와 WAL checkpoint 분리

## 상태

승인. 2026-08-25. 지속 지연 결론은 ADR-031로 보완한다.

## 문제

ADR-029의 단일 원자 커밋은 두 번의 `synchronous=FULL` 커밋을 한 번으로 줄였지만, 같은 실제 Run의 후속 159,663 이벤트·79회 저장에서 원장 커밋이 다시 15.520초까지 늘었다. 활성 WAL이 약 1,000 page 규모에 도달한 시점과 주기적 장기 커밋이 겹쳤다.

SQLite 공식 [WAL 문서](https://www.sqlite.org/wal.html)는 기본 자동 checkpoint가 1,000 page에서 COMMIT을 실행한 동일 스레드에 의해 수행돼 간헐적으로 COMMIT이 훨씬 느려질 수 있다고 설명한다. 공식 [`sqlite3_wal_autocheckpoint`](https://www.sqlite.org/c3ref/wal_autocheckpoint.html) 문서는 0으로 자동 checkpoint를 끌 수 있음을 명시한다. 공식 [PRAGMA 문서](https://www.sqlite.org/pragma.html)는 WAL의 `synchronous=FULL`이 각 COMMIT 뒤 WAL을 동기화해 전원 손실 내구성을 제공하고, PASSIVE checkpoint는 독자나 작성자 종료를 기다리지 않는다고 설명한다.

## 결정

1. 모든 활성 원장 연결의 `wal_autocheckpoint`를 0으로 고정한다.
2. 시장 저장 8회마다 COMMIT 호출 경로와 분리된 process에서 `PRAGMA wal_checkpoint(PASSIVE)`를 실행한다.
3. FULL 내구성, WAL, checksum, 불변 trigger와 원자 커밋은 유지한다.
4. 독자 때문에 일부 프레임만 반영되면 다음 저장 뒤 재시도한다. checkpoint 오류나 미완료 상태에서 WAL이 64MiB 이상이면 새 PAPER 진입을 fail-closed한다.
5. 자동 checkpoint 설정, 시도·부분·2초 이상·오류 횟수, 전체·반영 프레임과 최근·최대 소요를 시스템 고급진단에 표시한다.
6. 전략 신호·비용·TP·SL·위험 기준과 실제 주문 0 경계는 변경하지 않는다.

## 결과와 한계

단위·통합 테스트는 기본 자동 checkpoint 0, 별도 PASSIVE 전체 반영, 부분 checkpoint 재시도와 64MiB fail-closed를 검증했다. 실제 `run-517b78c88366`의 첫 세 checkpoint는 702·614·641 프레임을 모두 반영했고 시장 이벤트 최대 공백은 0.584초, 임계 지연 사건은 0이었다.

그러나 Run을 194,449 이벤트·97회 저장까지 계속 관찰하자 checkpoint 자체는 최대 17.496초였고, 원장 커밋도 별도로 최대 7.741초까지 늘었다. 최종 임계 지연 사건은 4회·최장 45.896초였다. 비계획 재연결·sequence gap·drop·저장 fault·버퍼 유실은 0이고 잠금은 자동 복구됐지만, 자동 checkpoint 분리만으로 장시간 성능이 완료됐다고 판정하지 않는다. 남은 Parquet와 FULL 원장 커밋을 시장 처리 프로세스 밖으로 함께 격리하는 결정은 ADR-031에 기록한다.
