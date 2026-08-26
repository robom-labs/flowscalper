# ADR-046. 활성 원장 writer 아래 거래 상세 cache의 best-effort 경계

## 상태

승인. 2026-08-26.

## 배경

실제 거래기록 행은 조회됐지만 `거래 데이터 다시보기`를 누르면 서버가 원장과 공개시장 archive로 재생 세션을 완성한 뒤 `replay_focus_cache`를 기록하는 단계에서 `sqlite3.OperationalError: database is locked`가 발생했다. 활성 서비스의 out-of-process durable writer와 선택적 cache 쓰기가 경합한 것이다. API가 500을 반환해 사용자는 원본 거래와 재생 데이터까지 없는 것으로 보게 됐다.

## 결정

1. 거래 상세 세션은 불변 PAPER 원장과 checksum 검증 공개시장 자료로 먼저 완성한다.
2. 완성 세션의 압축 cache는 성능 최적화이며 진실의 원본이 아니다.
3. cache 기록에서 SQLite `locked` 또는 `busy`만 발생하면 cache를 생략하고 이미 검증된 세션을 반환한다.
4. lock·busy가 아닌 스키마, checksum, 직렬화, 무결성 오류는 숨기지 않는다.
5. 원본 읽기나 세션 구축 실패를 cache 정책으로 성공 처리하지 않는다.
6. UI는 실패 상태와 `거래 차트 다시 시도`를 명시해 빈 화면과 무거래를 구분한다.
7. zlib 압축은 사용자 응답 지연과 CPU 부하를 줄이는 수준 6을 사용하며 저장 bytes의 checksum을 계속 검증한다.

## 결과

- 외부 writer가 잠시 원장을 점유해도 완성된 거래 상세 재생은 표시된다.
- cache가 저장되지 않은 요청은 다음 조회에서 다시 계산될 수 있다.
- 선택적 성능 cache가 원장 내구성과 LIVE 처리 우선순위를 침범하지 않는다.

## 검증

단위검사는 lock·busy에서 세션 반환, 비잠금 오류 전파, cache checksum 재사용을 검증한다. 실제 브라우저에서는 거래 행의 `재생`을 눌러 진입·SL·TP1·TP2·실제 종료·비용·보유시간을 확인한다.
