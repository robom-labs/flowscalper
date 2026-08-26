# ADR-050. 실행 서비스 비침습 장시간 관찰

## 상태

승인. 2026-08-26.

## 배경

기존 `scripts/soak_live.py`는 자체 `PaperRuntime`과 공개시장 WebSocket을 만드는 독립 자원 진단이다. 이 진단을 실제 8870 서비스와 동시에 실행하면 시장 연결, CPU, 저장장치를 추가로 소모한다. 따라서 그 결과는 실행 중인 LaunchAgent 프로세스와 Run의 30분·6시간·24시간 안정성 증거가 아니다.

또한 대시보드는 최근 전략 상태와 거래를 표시했지만, 프로세스 기동 후 전략 평가 경로가 장시간 계속 전진했는지를 누적 카운터로 확인할 수 없었다.

## 결정

1. `scripts/observe_running_service.py`는 이미 실행 중인 localhost `/api/dashboard`만 읽는다. 별도 시장 연결, Run, `PaperRuntime`, SQLite writer나 replay를 만들지 않는다.
2. 각 표본은 Run ID, 운영·시장·PAPER 상태, event·전략평가·적격신호 카운터, queue·실행호가·체결·wide 지연, 재연결·누락·유실, 저장 flush·WAL checkpoint, 현재·최고 RSS, 포지션 보호, 전략·독립계좌 구조와 PAPER 안전선을 포함한다.
3. `strategy_evaluation_count`와 `qualified_signal_count`를 대시보드 고급진단에 추가한다. 이 값은 성과나 수익성이 아니라 평가 경로 전진 여부만 뜻한다.
4. 전략 수와 계좌 수를 하드코딩하지 않는다. Registry의 전략 ID 전체에 대해 BASE·STRESS 쌍이 정확히 하나씩 있어야 한다.
5. 같은 Run·같은 프로세스에서 event와 전략 평가가 단조 증가해야 한다. 프로세스 재시작이나 카운터 역행은 실패다.
6. 계획 rotation·일시적 지연은 `RECONNECTING`·`SAFETY_WAITING`에서 신규 PAPER 진입이 잠기고 최종적으로 RUNNING·LIVE·PAPER로 복구될 때만 허용한다. 실행호가 임계 지연은 항상 fail-closed여야 한다.
7. wide scanner p95는 넓은 관찰용 지표로 저장하되 실행호가 진입 지연 판정에 섞지 않는다.
8. `make service-soak-30m`, `service-soak-6h`, `service-soak-24h`는 각각 실제 벽시계 1,800초, 21,600초, 86,400초를 채워야 한다. 짧은 표본으로 더 긴 검사를 PASS로 표시하지 않는다.
9. 태블릿·모바일의 요약, 주요 화면, 하위 화면 버튼은 최소 48×48px을 유지하고 root 가로 넘침을 만들지 않는다.
10. 전략 임계값, 비용, TP·SL, 위험예산, Registry·Governor, 원장과 실제주문 0 경계는 변경하지 않는다.

## 결과

- 독립 공개시장 부하 진단과 실제 서비스 장시간 수용검사를 증거 파일·명령 단계에서 구분한다.
- 거래가 없어도 전략 평가 경로가 실제로 전진하는지 확인할 수 있다.
- 재시작·카운터 역행·전략 ID 교체·독립계좌 누락·미보호 포지션·저장 정지를 각각 독립 실패로 보고한다.

## 검증 경계

단위·통합 테스트는 parser와 수용 판정을 검증한다. 실제 30분 검사는 해당 프로세스·Run의 30분 관찰만 입증한다. 전략 수익성과 6시간·24시간 안정성은 각각 충분한 자연 `LIVE_PUBLIC` 표본과 실제 경과시간을 채우기 전까지 `NOT_PROVEN`·`NOT_RUN`이다.
