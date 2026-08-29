# ADR-102. localhost 접근 로그 제한과 연구 프로세스 하드 듀티사이클

## 상태

수용. 2026-08-29 WAVE116.

## 관찰

WAVE116 E06 비용충족 후보 검사는 LIVE 안전장치가 새 500ms 초과 이벤트 루프 지연을 감지해 결과를 쓰지 않고 중단했다. 해당 시점의 공개시장 이벤트 처리 최대값과 가까운 상관관계는 있었지만, 연구 자식이 이미 낮은 CPU 상태였으므로 연구가 단독 원인이라고 단정할 수 없다. 연구를 실행하지 않은 뒤에도 별도의 500ms 초과 지연이 관찰됐다.

동시에 설치 서비스의 접근 로그에서 `/api/dashboard` 반복 조회가 대부분을 차지했다. 여러 화면과 관찰기가 같은 캐시 대시보드를 조회할 때 응답 계산은 공유되지만 Uvicorn 접근 로그는 요청마다 서비스 로그 파일에 기록됐다. 이는 오류 조사에 필요한 시작·오류 로그와 달리 정상 운영에서 반복 디스크 쓰기만 늘린다.

또한 기존 협조 CPU 예산은 Python 이벤트 처리 체크포인트 뒤에는 양보했지만 DuckDB/Arrow의 첫 native scan처럼 체크포인트 전에 발생하는 순간 CPU 구간을 직접 제한하지 못했다.

## 결정

1. 지원 실행기 `scripts/run_server.py`는 `access_log=False`로 Uvicorn 요청 접근 로그를 끈다. 표준 출력의 시작 메시지와 표준 오류의 예외·traceback은 그대로 유지한다.
2. LIVE와 병행하는 전략 연구 자식은 macOS/POSIX에서 `SIGSTOP`과 `SIGCONT` 듀티사이클을 적용한다. 기본 연속 실행 구간은 최대 50ms이며 15% 목표 CPU 비율에서는 그 뒤 약 283ms를 멈춘다.
3. 취소·안전장치 종료·예외 경로는 정지된 자식을 반드시 재개한 뒤 종료한다. 중단된 연구는 결과 디렉터리를 채택하지 않고 append-only trial history에 `ABORTED`로 남긴다.
4. 이 변경은 500ms 지연의 단일 원인 판정이 아니다. 배포 후 연구가 없는 기준선과 하드 제한 연구를 분리해 관찰하고, 초과 지연 수가 증가하면 결과를 채택하지 않는다.
5. 실제 주문·private API·인증·secret 경로는 계속 0이다. 자연 신호를 늘리기 위해 진입 기준이나 비용 가정을 완화하지 않는다.

## 회귀 계약

- `test_supported_launcher_disables_per_request_access_log`가 정상 요청 접근 로그 비활성화를 고정한다.
- `test_hard_duty_cycle_caps_native_scan_before_cooperative_checkpoints`가 실행·정지 구간 계산을 고정한다.
- `test_hard_duty_cycle_always_resumes_cancelled_child`가 취소 중 정지된 자식의 재개를 고정한다.
- `config/regression_contracts.json`의 `HEAVY_RESEARCH_LIVE_CPU_PRIORITY`와 `LOCAL_SERVICE_BOUNDED_ACCESS_LOGGING` 계약이 이후 Wave에서 앵커 제거를 막는다.

## 증거 경계

표적 단위·회귀 테스트 통과는 부하 제한 코드의 계약만 증명한다. 설치 서비스에서 30분·6시간·24시간을 실제로 채우지 않은 안정성은 각각 `NOT_RUN`으로 유지하며, E06 후보의 수익성과 실거래 적합성은 독립 표본과 BASE·STRESS·시간순 OOS·강건성 gate 전까지 `NOT_PROVEN`이다.
