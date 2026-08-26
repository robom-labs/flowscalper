# ADR-048. 현재 RSS와 프로세스 최고 RSS의 분리

## 상태

승인. 2026-08-26.

## 배경

장시간 PAPER 실행의 시스템 진단은 `process_memory_mb`를 현재 프로세스 메모리로 설명했지만 구현은 `resource.getrusage(...).ru_maxrss`를 사용했다. 이 값은 프로세스 생애의 최고 resident set size이며 현재 사용량이 아니다. 실제 실행 중 같은 관찰창에서 대시보드는 323.266MB를 표시했고 운영체제 현재 RSS는 299.484MB였다. 최고치는 메모리가 해제돼도 감소하지 않으므로 이 값을 현재치와 soak 증가량에 사용하면 누수·안정화 판단이 왜곡된다.

## 결정

1. `process_memory_mb`는 현재 resident memory만 뜻한다.
2. macOS는 `proc_pidinfo(PROC_PIDTASKINFO)`, Linux는 `/proc/self/statm`, Windows는 process memory counters의 working set을 사용한다.
3. 현재치 측정이 실패하면 최고치 fallback을 사용할 수 있으나 source를 `PEAK_MAX_RSS_FALLBACK`으로 명시해 현재 측정처럼 보이지 않게 한다.
4. `process_memory_peak_mb`와 `process_memory_peak_source`를 별도 제공한다. 지원 플랫폼의 최고치는 기존 `ru_maxrss` 또는 Windows peak working set을 사용한다.
5. soak의 `memory_growth_mb`는 현재 RSS의 기준선 대비 최대 증가량이다. `peak_memory_growth_mb`는 별도 고수위 진단이며 두 값을 합치거나 대체하지 않는다.
6. 고급진단 화면은 `현재 프로세스 메모리 RSS`와 `프로세스 최고 메모리 RSS`를 한국어로 분리한다.
7. 전략 임계값, PAPER 계획·체결·포지션·손익, Registry, Governor, 저장 원장과 실제주문 0 경계는 변경하지 않는다.

## 결과

- 메모리 해제 뒤 현재 RSS 감소를 관찰할 수 있고, 프로세스 생애 최고치는 별도로 보존된다.
- 짧은 테스트와 장시간 메모리 안정성 증거의 의미가 분리된다.
- 기존 API 소비자는 `process_memory_mb`를 계속 읽을 수 있지만 값의 의미가 문구와 일치하게 교정된다. 새 최고치 필드는 additive다.

## 검증 경계

플랫폼별 단위 테스트, 실제 프로세스 RSS 비교와 브라우저 표시는 구현 정확성을 검증한다. 6시간·24시간 메모리 안정성은 수정된 프로세스를 해당 시간 동안 실제 실행하기 전 `NOT_RUN`이다. 기존 수정 전 Run의 장시간 상태는 런타임 안전성 참고자료일 뿐 수정 후 메모리 지표의 장시간 PASS가 아니다.
