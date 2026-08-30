# ADR-121. 대용량 연구 입력은 LIVE와 다른 물리 I/O 영역에서만 읽는다

- 상태. 채택
- 범위. 동결 공개시장 archive를 사용하는 100후보 PAPER screening과 bounded benchmark
- 제외. 실제 주문, private API, API Key, wallet, 입출금, 전략 기준 완화

## 문제

W126 전체 screening은 연구 archive와 spill의 `st_dev`가 달라 기존 검사에서는 서로 다른
장치로 보였다. 그러나 macOS `diskutil`과 `hdiutil`로 backing 장치를 추적하면 프로젝트의
APFS sparsebundle과 `One Touch` spill은 모두 같은 물리 USB `disk4`였다. 연구 reader와
LIVE 원장 writer가 같은 USB에서 경쟁한 구간에 event-loop 지연 1,548ms와 저장 flush
37.411초가 관찰됐고 안전감시가 screening을 중단했다. 부분 결과는 발행하지 않았다.

inode, ctime과 mtime은 같은 파일시스템의 원본 identity에는 적합하지만 RAM 또는 다른
물리장치로 복사한 동결 파일에는 그대로 유지되지 않는다. 복제본을 무조건 거부하면 I/O를
격리할 수 없고, identity 검사를 없애면 입력 변조를 놓친다.

## 결정

1. macOS에서는 mount의 `BusProtocol`과 parent whole disk를 읽는다. Disk Image이면
   `hdiutil`의 image path를 따라 sparsebundle의 실제 backing 장치까지 재귀적으로 추적한다.
2. `ram://` Disk Image는 별도 `RAM:<device>` I/O 영역으로 분류한다.
3. Disk Image의 backing 경로를 찾지 못하거나 순환하면 다른 장치라고 추정하지 않고
   fail closed한다.
4. 동결 파일 500개 이상의 screening은 원본과 경로가 다르고 물리 I/O 영역도 다른
   `--archive` 복제본만 허용한다.
5. 원본 archive는 기존 size·inode·mtime·ctime identity를 계속 검사한다. 다른 장치의
   portable 복제본은 manifest의 size와 파일별 SHA-256을 모두 검사한다.
6. 연구 spill도 동결 원본/LIVE의 물리 I/O 영역과 달라야 한다. 연구 복제본과 spill은 같은
   RAM 또는 내부 연구장치에 둘 수 있다.
7. full screening runner와 bounded benchmark 모두 같은 portable archive override를 사용한다.
8. 실패한 W125·W126 trial과 부분복제 상태는 삭제하지 않는다. Train 복제본만 완전한 동안
   Validation 또는 Final OOS 결과를 만들지 않는다.
9. 연구 인프라 변경으로 기존 사전등록 source checksum이 달라지면 우회 실행하지 않는다.
   새 코드 commit을 먼저 고정하고 새 manifest로 재사전등록한 뒤 실행한다.

## 수용기준

- 서로 다른 경로와 `st_dev`라도 같은 backing USB면 대용량 연구를 거부한다.
- sparsebundle은 실제 backing USB로, RAM Disk는 별도 RAM 영역으로 판별한다.
- 해석할 수 없는 Disk Image는 거부한다.
- portable 복제본 한 바이트가 달라져도 SHA-256 검사가 실패한다.
- Train·Validation 논리구간 21,341파일이 manifest size와 SHA-256을 모두 통과한다.
- Final OOS 3,466파일은 첫 단계 선택이 끝날 때까지 복사·읽기·평가하지 않는다.
- 연구가 없을 때 LIVE PAPER 이벤트·전략평가가 전진하고 실제 주문과 인증은 0을 유지한다.
- 부분 데이터, stale manifest와 중단 실행은 수익성 또는 승격 PASS로 기록하지 않는다.

## 결과

실제 장치 판별은 프로젝트와 `One Touch`를 모두 `USB:disk4`, 연구 RAM을 `RAM:disk10`,
내부 임시영역을 `Apple Fabric:disk3`으로 확인했다. 회귀 41건, Ruff, mypy 112 source와
diff 검사가 통과했다. 전체 backend 850건, PAPER build safety, 보안 148 source, 저장소
위생과 30개 누적 회귀계약도 통과했다. RAM의 Train 2,502파일과 Validation
18,839파일, 합계 21,341파일은 최종 재검사 4.030초에 size·SHA-256을 모두 통과했다.
첫 전수검사에서 과거 부분복제 파일 한 개의 크기 불일치를 잡아 해당 파일만 동결 원본에서
다시 복사한 뒤 전체를 재검증했다. Final OOS 3,466파일은 복사하거나 읽지 않았다.

수정 뒤 기존 서비스 45.020초 관찰은 event +2,988, 전략평가 +19,720, queue 최대 3,
처리·체결 p95 최대 24.511·68.025ms, 신규 500ms 초과 지연·비계획 재연결·gap·drop·저장
fault·buffer drop 0으로 PASS했다. 새 commit 기준 100개 trial과 4개 논리구간 manifest를
재사전등록했고, RAM Train에서 1,000-event bounded pilot은 13.783초·960회 후보평가로
PASS했다. 동시에 실행한 LIVE guard도 event +1,975·전략평가 +13,000, queue 최대 0,
처리·체결 p95 최대 23.457·72.496ms, 신규 500ms 초과·비계획 재연결·gap·drop·저장결함 0이었다.
pilot은 거래 0건이고 선택·승격·Final OOS 처리를 하지 않은 실행경로 진단일 뿐이다.

복사 유지관리 뒤 같은 Run을 재시작한 45.018초 검사는 event +3,112·전략평가 +19,740,
queue 최대 0, 처리·체결 p95 최대 23.535·47.198ms, 신규 500ms 초과·비계획 재연결·gap·drop·
저장결함 0으로 PASS했다. 적격신호와 신규 거래는 0이므로 수익성은 `NOT_PROVEN`, 실자금은
`NOT_READY`다. 전체 Train·Validation 100후보 screening은 아직 `NOT_RUN`이다.
