# 변경기록

이 파일은 사용자가 알아야 할 중요한 변화만 짧게 기록한다. 세부 구현 이력은 Git commit, 검증 증거와 GitHub Release에 보존하고 과거 소스 복사본은 현재 트리에 두지 않는다.

형식은 [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/)의 취지를 따르며, 버전 번호는 [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html)을 제품의 사용자 동작·저장 schema·로컬 API 계약에 적용한다. `-paper`는 실제 주문이 없는 PAPER 전용 제품임을 뜻한다.

## 아직 배포하지 않음

- 계획 WebSocket 교체 때 REST depth snapshot 준비 중 쌓인 오래된 delta를 실행 가능한 호가로 다시 내보내 약 99초 임계지연이 생기던 문제를 수정했다. stale warmup delta는 sequence continuity에만 적용하고 첫 신선한 depth 전까지 기존 신규진입 안전잠금을 유지한다.
- 반복 시작·명시적 새 Run·사용자 pause·자동 안전잠금·전략 설정을 idempotency key, CAS revision, actor, reason과 재시작 가능한 감사 이력으로 분리했다.
- 기록·분석 화면에 main/전략리그, Run, BASE/STRESS, 전략 버전과 sample type 범위를 추가하고 1m·3m·5m·15m·30m·1h·4h를 단일 timeframe registry로 연결했다.
- 연구 manifest, 시간순 Train·Validation·OOS, horizon별 purge·embargo, walk-forward, PBO, DSR와 deterministic bootstrap을 추가했다.
- Strategy Governor가 RESEARCH·SHADOW·CHALLENGER·ACTIVE·QUARANTINED·RETIRED, manual lock, 원자 champion 교체, rollback과 감사 근거를 관리하되 source·임계값은 변경하지 않게 했다.
- canonical completed candle과 연구 전용 multi-timeframe 엔진을 추가하고 180개 사전등록 ORIGINAL·기계적 미러·별도 역가설 후보를 실제 bid·ask와 BASE/STRESS 비용으로 비교한다. 연구 결과는 Registry를 자동 변경하지 않는다.
- 전략 수와 BASE/STRESS 독립계좌 수를 Registry에서 동적으로 계산하고 10·20 표시 하드코딩을 제거했다.
- 각 런타임 전략에 horizon, 예상 보유, 신호 반감기, 사용 시간구간, exit model, 최대 안전보유와 비용버전을 선언하고 한국어 상세 화면에 공개했다.
- 종료 중 ASGI 취소가 persistence worker를 함께 취소하지 않게 저장 완료를 shield하고, macOS 서비스가 최신 미종료 PAPER Run의 LIVE/DEMO 의도를 읽기 전용으로 복구하되 모든 오류는 READY로 fail-closed하게 했다.
- 서비스 재시작과 공개시장 재연결 성공이 사용자가 누른 신규진입 일시정지를 덮어쓰던 경로를 수정했다. 수동 정지와 재개 의도는 같은 Run 복구 뒤에도 각각 유지되고 자동 안전잠금과 별도로 표시된다.
- 전략 상태를 RESEARCH·SHADOW·CHALLENGER·ACTIVE·QUARANTINED·RETIRED로 분리하고, 작은 승률 표본이 아니라 비용후 OOS·강건성·다중검정·자연표본·cooldown을 요구하는 보수적 Governor를 추가했다.
- 자동 격리는 기술 결함 또는 전체·최근 OOS의 두 평가 주기 연속 악화에만 허용하고, champion 교체를 원자적으로 적용하며 사용자 manual lock을 우선한다.
- 전략 설정 CAS, 주체·이유·근거 audit, 재시작 이력 복구와 새 revision rollback을 API·SQLite·한국어 UI에 연결했다.
- D OFI 눌림은 시간순 저장 train 4건에 이어 더 늦은 자연 LIVE_PUBLIC BASE 2건도 모두 비용후 손실이어서 기본 OFF로 내렸다. B만 ACTIVE, C/F/G/I/J는 SHADOW, A/D/E/H는 OFF이며 전략 코드·과거 거래·20개 독립계좌·LONG/SHORT·수동 재활성화는 보존한다.
- LIVE 전략 성과와 전략·종목 분석 API가 활성 SQLite를 매 요청마다 다시 읽어 13.7~16.0초 대기하던 경로를 시작 시 검증한 현재-version 거래 cache와 현재 Run 메모리 거래의 고유 ID 병합으로 바꿨다. Replay·비LIVE 분석은 기존 불변 원장 읽기를 유지한다.
- macOS 서비스의 READY 부팅 뒤 새 LIVE 시작이 이전 Run 종료를 건너뛰어 열린 Run 행이 누적되던 문제를 수정했다. 새 Run 직전에 평평한 과거 행을 삭제 없이 `preserved` 종료하고, 최근 복구 snapshot에 PAPER pending·position이 있으면 새 Run을 차단한다.
- 실제 A~J 런타임 evaluator를 시간순 저장 `LIVE_PUBLIC` train·holdout에 그대로 적용해 A의 비용전·비용후 방향성이 모두 실패함을 확인하고 A를 기본 OFF로 내렸다. B만 ACTIVE, C/D/F/G/I/J는 SHADOW, A/E/H는 OFF이며 과거 거래·20개 독립계좌·LONG/SHORT·수동 재활성화 제어는 삭제하지 않는다.
- 실행 감사의 후보 시각이 진입·관리·청산 이벤트에 반복되던 문제를 수정해 각 실제 호가·체결 시각을 기록한다. 자연 PAPER 거래에서 후보→진입 520ms, 진입→관리결정 28.430초, 관리결정→청산 538ms와 원장 보유시간 28.968초가 일치함을 확인했다.
- 원시 Binance depth delta를 모두 호가장에 적용한 뒤 종목별 마지막 완성 snapshot만 500ms마다 전달하고 aggregate trade도 500ms로 합쳐, 4,096건 provider queue 포화와 오래된 표시지연을 제거했다. sequence span과 fail-closed 안전검사는 유지한다.
- 전략 화면은 `감시`, `검증 중지`, `문제`, `실제 주문`을 분리해 의도적으로 OFF인 전략을 고장처럼 보이지 않게 표시한다.
- 공개시장 Parquet 작성과 archive manifest·종목통계·캔들의 `synchronous=FULL` 원자 커밋 전체를 시장 처리 Python 프로세스 밖의 background I/O process로 격리했다. 별도 연결도 WAL·FULL·자동 checkpoint 0을 유지하고 실패 시 두 버퍼 복원과 새 PAPER 진입 안전잠금을 적용한다.
- SQLite 기본 1,000-page 자동 WAL checkpoint를 COMMIT 경로에서 끄고, 8회 저장마다 별도 process의 비차단 PASSIVE checkpoint로 옮겼다. 부분 checkpoint는 재시도하고 WAL이 64MiB까지 커진 채 실패하면 새 PAPER 진입을 안전잠금한다.
- 공개시장 Parquet 저장 뒤 archive manifest·종목별 통계·캔들을 외장 SQLite의 한 `synchronous=FULL` 커밋으로 원자 저장해 연속 FULL 커밋을 제거했다. 충돌이나 저장 실패는 전체 롤백·버퍼복구·신규 PAPER 진입 안전잠금으로 처리한다.
- 고급진단의 별도 manifest·candle 시간은 `원장 통합 커밋 ms`로 교체했다. 56,260 이벤트의 초기 표본은 최장 1.506초였지만 같은 Run의 159,663 이벤트 후속 표본에서 자동 WAL checkpoint가 포함된 커밋이 15.520초로 재발해, 초기 결과를 지속 개선 증거에서 제외하고 분리 checkpoint로 후속 수정했다.
- READY의 과거 거래통계를 백그라운드 query-only SQLite 연결로 준비해 안전 복구와 첫 화면·시작 버튼을 막지 않게 했다. 부팅 단계, 통계 준비상태, 저장 flush의 Parquet·통합 원장 단계와 최대 이벤트 수신 공백시각을 고급진단에 추가했다.
- 현재 PAPER 목록과 차트에 같은 종목·전략·BASE라도 `공동계좌`와 `전략 독립계좌`를 구분해 중복 오류처럼 보이지 않게 했다.
- 종료된 PAPER 포지션의 진입 알림이 같은 LIVE Run 화면에 남지 않도록 종료 안내로 바꾼 뒤 15초 후 자동 정리한다.
- 실제 호가 임계지연 사건의 시작·복구·지속시간, 연속 이벤트 수신 공백과 2초 이상 시장 저장 flush 발생시각을 고급진단에 추가했다.
- 공식 호가장 복원력 연구에서 도출한 유동성 재충전 실패 추세 후보는 저장 LIVE_PUBLIC train·holdout 모두 BASE/STRESS 비용을 넘지 못해 신규 전략으로 추가하지 않았다.
- 차트 위에 현재 선택 종목의 PAPER 방향·전략·BASE/STRESS·entry·TP1·SL을 표시하고, 시장 화면에서 모든 진행 포지션을 바로 선택할 수 있게 했다.
- 전략 A~J 각각에 `정상 감시 중`, `준비 중`, `PAPER 진입 중`, `안전 대기`, `확인 필요`, `꺼짐`과 최근 조건 대기 이유·평가경로 수를 표시해 조용한 정상 감시를 오류와 구분한다.
- 실제 bid·ask 실행호가 p95, 공개 체결 p95와 50종목 wide scanner p95를 분리했다. 500ms를 넘긴 늦은 aggregate trade는 archive에는 보존하되 candle·전략 피처에는 넣지 않고 신선한 체결까지 해당 종목을 fail-closed한다.
- 시스템 화면은 실제 호가·체결 지연을 함께 보여주고 wide scanner 지연은 진입판정이 아닌 넓은 관찰이라고 명시한다.
- 실시간 전략 성과를 0.5초마다 전체 재계산하던 경로를 새 독립 PAPER 거래가 완료될 때만 갱신하는 cache로 바꿨다.
- 광은 감시 50개는 유지하고 정밀 분석은 제품 요구범위 8~12개의 상한인 12개로 제한해 단일 실행 루프의 지속 포화를 막았다.
- 실행과 복구용 10,000건 이벤트는 유지하되 실시간 대시보드 계산에는 최신 512건만 사용해 화면 갱신 부하를 제한했다.
- 공개시장 Parquet 공간만 확인하던 저장 안전장치를 활성 SQLite 원장 볼륨까지 확장해, 어느 한쪽이 부족해도 새 PAPER 진입을 자동 차단한다.
- 상태를 바꾸지 않는 진입 거절 감사마다 전체 포트폴리오와 20개 계정을 중복 저장하던 문제를 제거하고, 상태 변경 때 영향받은 계정만 복구 스냅샷과 함께 기록한다.
- 실시간 차트의 모든 시간구간은 그대로 제공하면서 SQLite에는 원본 1초봉과 리플레이 기준 3분봉만 영구 저장해 파생 캔들 중복 증가를 막는다.
- 외장 APFS에서 실행하는 macOS 서비스의 활성 원장을 같은 외장 볼륨의 전용 런타임 폴더에 두고, 내장 실행환경·로그와 대용량 PAPER 원장을 분리한다.
- 성과 화면에서 이번 Run의 독립계좌 자산과 현재 전략 버전 전체 Run의 거래 통계를 같은 범위처럼 보이던 문구를 각 열·요약에 명시해 오해를 막았다.
- 최대 2,000개 진입계획 거부 기록도 500개씩 일괄 삭제하지 않고 고정길이 queue에서 한 건씩 교체하도록 바꿨다.
- 메모리 이벤트가 10,000개를 넘을 때 과거 2,500개 객체를 한꺼번에 해제하던 지연 경계를 고정길이 queue의 1건씩 교체로 바꿔 일괄 정지 구간을 제거했다.
- 2,000건 Parquet 저장의 JSON·checksum·압축·fsync가 같은 Python 프로세스에서 공개시장 처리를 1.5초 이상 밀던 경로를 별도 worker process로 격리하고, 이미 계산한 batch checksum을 파일 digest로 재사용했다.
- 같은 snapshot에서 10개 전략이 동일한 방향·청산형식의 entry·TP·SL·비용 계획을 최대 32번 반복 계산하던 경로를 결과 변경 없이 최대 4번으로 공유해 공개시장 수신 우선권을 높였다.
- 공식 호가장 연구를 근거로 top10 가격거리 대비 깊이의 매수·매도 기울기 비대칭을 1초 확인하는 J 전략을 SHADOW 전용으로 추가해 10전략·20개 독립 PAPER 계좌로 확장했다.
- J 전략은 최소 32개 과거표본, 얇은 반대호가·두꺼운 지지호가, OFI·체결·microprice·가격반응과 비용후 순손익비를 모두 요구하며 자연신호를 만들기 위해 임계값을 낮추지 않는다.
- 거래소 시각을 고정 wall-clock 오프셋이 아니라 monotonic 기준점으로 추적해 macOS 시각 보정 뒤 정상 데이터가 2초 이상 지연된 것으로 오인되던 문제를 수정했다.
- 계획 WebSocket 교체는 준비 시작 전에 재연결·신규 진입 잠금으로 전환하고 공개 연결 종료 대기를 제한해, 새 정상 호가 뒤 빠르게 자동 복구한다. 비계획 재연결도 공개 metadata와 거래소 시각을 다시 검증한다.
- 서비스 재시작·새 Run 전환 뒤 이전 Run의 PAPER 진입 알림과 집중 포지션 상태가 화면에 남지 않도록 초기화했다.
- 깊이보정 OFI와 실제 prefix 3초 수익률이 같은 방향으로 1초 이상 이어질 때만 평가하는 I 전략을 SHADOW 전용으로 추가해 9전략·18개 BASE/STRESS PAPER 계좌로 확장했다.
- I 전략은 spread·robust z·다중 OFI·microprice·가격반응·비용후 순손익비를 모두 통과해야 하며, 자연신호를 만들기 위해 임계값을 낮추지 않는다.
- 수십만 건 저장 Run replay와 거래 재생 화면이 LIVE 수신을 밀던 문제를 모든 읽기·재처리·checksum 단계의 별도 `nice(19)` process와 구간별 5% CPU 예산으로 수정했다.
- replay 협력 양보 지점을 시장 입력 16건·checksum 128건마다 적용했다. 15,045건 replay와 LIVE를 병행한 225초 표본에서 critical lag·진입잠금·비정상 재연결·gap·drop은 0이었다.
- replay checksum을 이벤트·결정경로 length-prefix streaming digest로 바꿔 전체 canonical JSON 복제와 누적 CPU sleep 빚을 제거했다. 중간 규모 85,714건 두 실행은 checksum과 집계가 일치했고 LIVE critical lag·비정상 재연결·gap·drop·진입잠금은 0이었다.
- 긴 replay 중 다른 timeline·거래 재생 요청은 대기열에 매달리지 않고 `REPLAY_BUSY` 재시도 안내를 즉시 표시한다.
- 필터형 Parquet 조회도 배치 전체 checksum을 먼저 검증해 일부 row가 잘린 저장 배치가 정상처럼 통과하지 못하게 했다.
- 거래 집중 replay를 실제 거래 시간창으로 제한하고 checksum 검증 압축 캐시를 추가해, 첫 검증 뒤 동일 거래를 빠르게 다시 열 수 있게 했다.
- 로컬 시각이 거래소보다 느릴 때 데이터 지연이 0ms로 숨던 문제를 인증 없는 Binance·Bybit 공개 시각의 최소 RTT 오프셋으로 보정하고 시스템 화면에 보정값과 상태를 표시한다.
- 현재 거래기록은 현재 전략 구현 버전의 공개시장 PAPER 거래만 표시하고, 교체 전 거래는 불변 원장에 보관한 채 제외 건수를 알린다.
- 전략 로직 교체 전·후의 독립 PAPER 거래를 한 승률·기대값에 섞던 문제를 수정하고, 현재 전략 구현 버전의 `LIVE_PUBLIC` 표본만 기본 성과로 집계한다. 과거 불변 원장은 삭제하지 않고 전략·계좌·종목별 제외 건수를 화면에 표시한다.
- DEMO와 REPLAY shadow 거래를 각 `DEMO_FIXTURE`·`REPLAY`로 분리하고, 성과표의 비용·낙폭이 현재 Run 계좌값과 저장된 현재버전 통계값을 혼합하지 않게 했다.
- Strategy Registry를 A~H 8개와 BASE·STRESS 16계좌로 확장하고, 신규 다중호가 공정가 G와 깊이보정 OFI H를 엄격한 SHADOW 전용으로 추가했다.
- G/H는 실제 공개 호가·체결에서 계산한 다중단계 공정가, 깊이보정 OFI, event-time 지속성, 기존 비용후 순손익비·TP1·TP2·SL 경로를 사용하며 자연신호를 만들기 위해 기준을 낮추지 않는다.
- LIVE 서비스에서 저장 Run replay가 CPU를 경쟁해 재연결과 임계 지연을 만들 수 있던 문제를 별도 저우선순위 프로세스와 동시 replay 잠금으로 격리했다.
- 전략 화면과 성과 화면을 8개 전략·16개 독립 PAPER 계좌로 확장하고 A/B ACTIVE, C~H SHADOW, 모든 LONG·SHORT 허용을 기본값으로 표시한다.
- 10시간 실행 뒤 커지던 호가 전체 정렬과 전략 과거창 반복 정렬을 정확한 증분 정렬 인덱스로 바꿔 장시간 처리지연 증가를 억제했다.
- 앱 내 브라우저에서도 차트 전체화면이 즉시 열리게 하고 모바일 가격 통계가 전체화면 버튼을 가로막거나 겹치던 문제를 수정했다.
- 체결 직후 단일 근거약화 신호가 800ms만 이어져도 1~2초 만에 종료되던 문제를 10초 유예, 복수 신호와 3초 지속 확인으로 수정했다. 초기 손절·익절과 데이터 안전정책은 즉시 유지한다.
- 전략 승률·기대값·종목별 성과에 공동계좌와 독립계좌 거래를 중복 집계하던 문제를 수정하고 승·패·보합과 승률 95% 범위를 추가했다.
- 모든 전략은 기본으로 켜되 A/B는 공동·독립 PAPER, C~F는 독립 PAPER로 유지하고 LONG·SHORT를 모두 허용한다.
- 자산·손익·비용·가격·수량·거래량과 1초대 보유시간을 화면에서 의미 있는 자릿수로 표시하며 원장 Decimal 값은 그대로 보존한다.
- 전략 A~F의 1차 비용 게이트와 최종 실행가능 호가·수수료·슬리피지 게이트가 모순돼 자연 적격신호가 전부 사라지던 문제를 수정했다.
- A/C 반전형과 B/D/E/F 추세형의 구조 stop 거리를 분할익절 후 순손익비 1.20을 유지하도록 각각 최소 0.80%, 0.30%로 정리하되 위험예산은 그대로 유지했다.
- A~D의 고정 시간값을 실제 event timestamp와 과거 가격경로 기반 refill·재진입·눌림·재가속 확인으로 교체하고 미래 표본 사용을 차단했다.
- A~F 각각의 롱·숏에 대해 PAPER 진입 직후 TP1·TP2·SL 생성과 익절·손절·비용 회계를 종단 시뮬레이션한다.
- 리플레이 후보 수를 main·BASE·STRESS 중복 행이 아닌 고유 후보로 집계하고, 현재 목록에 없는 과거 거래 상세가 화면에 남지 않게 했다.
- 시작 버튼을 누르면 `연결 중`과 `작동 중`을 크게 표시하고, 사용자 일시정지와 자동 안전 대기를 명확히 분리했다.
- 시장 관찰은 계속되지만 새 PAPER 진입만 잠긴 상태를 따로 표시하며, 데이터가 정상화되면 안전조건 확인 뒤 자동 복귀한다.
- 1,000단계 로컬 호가장의 상위 20단계를 정확히 캐시해 장시간 LIVE에서 반복 전체 정렬로 생기던 처리지연을 줄였다.
- 장시간 LIVE 처리지연 병목을 Run별 archive, 250ms 방향별 체결 VWAP 병합, 전략 통계 공유, 상위 호가 계산과 비동기 2,000건 저장으로 개선했다.
- 모바일 시작 버튼과 LIVE/샘플 PAPER 상태를 항상 보이게 하고 하위 메뉴가 거래 집중 제목을 가리던 반응형 겹침을 제거했다.
- DEMO가 LIVE 지연·wide/deep 수치를 상속하지 않게 했고, 완료 거래 replay의 진입·종료 PAPER 원장 전환을 항상 이동할 수 있게 했다.
- 첫 화면을 5개 메뉴의 compact 시장 작업공간으로 바꾸고 Binance USD-M 전체 catalog와 Upbit KRW 관찰 전용 catalog를 추가했다.
- 기본 3분봉 200개, 거래량 overlay, MA10·MA20, 동적 RSI·MACD pane과 고정 종목 rail을 구현했다.
- deep 20 안전 회전, 전략별 종목 성과, 실제 fill 기반 포지션 집중 3열 화면과 0.5~80배 거래 단위 replay를 연결했다.
- 태블릿·모바일에서 chart 폭을 유지하는 계획·손익 sheet와 비용 포함 순손익 rail을 추가했다.
- 실제 주문, private API, 인증 경로는 계속 0이며 Upbit는 PAPER 실행에 사용하지 않는다.

- Strategy Registry를 A-F 6개로 확장하고 A/B는 ACTIVE, C/D/E/F는 SHADOW로 시작한다.
- 전략별 BASE/STRESS 12개 1,000 USDT PAPER 계좌가 서로 다른 종목을 3개까지 독립 체결한다.
- 위험기반 최대 5배 상한, 1.5% 총 계획위험, 계좌별 손실, drawdown, cooldown 잠금을 추가했다.
- 호가 쏠림 E와 강한 체결 흐름 F는 실제 event timestamp 500ms 지속성을 확인한다.
- REVERSION 70/30, TREND 40/60 청산과 schema v2 다중 포지션 복구, v1 읽기 호환을 추가했다.
- 시작·새 Run을 즉시 `202` 응답하는 작업 상태로 바꾸고 중복·충돌·취소·재시도를 명확히 표시한다.
- 초보자용 홈, 6개 전략·12개 BASE/STRESS 계좌 리그, 진행 거래, 고급 터미널을 분리했다.
- 고정 스캐너와 상세 drawer, MA·EMA·VWAP·볼린저·RSI·MACD, 증분 차트 갱신, 현재로 돌아가기와 전체화면을 추가했다.
- 실제 주문, private API, 인증 경로는 계속 0이다.

## 0.2.0-paper — 2026-08-23

- 실제 공개시장 장시간 supervisor, 50개 wide·10개 deep 관찰, A/B/C/D Registry와 main·shadow PAPER 계좌를 연결했다.
- 보수적 bid·ask 체결, 불변 진입계획, 포지션·위험·SQLite v6·외장 Parquet·ReplayEngine·전략 성과를 종단 간 연결했다.
- 비전문가용 한국어 홈, 고정 scanner, 실제 candle·거래량·선택형 이동평균 chart와 macOS 자동복구를 구현했다.
- 다른 AI가 제품·요구·코드·검증을 파악할 수 있는 인계 메모와 업그레이드 요청 프롬프트를 추가했다.
- 단일 최신 소스, 짧은 변경기록, Git tag·Release 보존 원칙과 저장소 위생 자동검사를 추가하고 TypeScript 생성 파일을 제거했다.
- 실제 주문·private API·인증 경로는 계속 0이다.

## 0.1.0-paper — 2026-08-22

- credential 없는 로컬 PAPER 연구 도구의 안전 경계와 첫 기준선을 만들었다.
- 초기 fixture·공개시장 adapter·PAPER 원장·React 화면·검증 문서를 제공했다.
- 이후 기능과 UI는 0.2.0-paper의 현재 구현으로 대체됐다.
