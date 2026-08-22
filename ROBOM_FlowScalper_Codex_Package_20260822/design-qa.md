# FlowScalper v0.2 디자인 QA

## 비교 대상

- 시각 기준 원본은 사용자가 제공한 `/var/folders/qn/2pmsy6s14ss1swss6c965cxc0000gn/T/codex-clipboard-4b8fba08-4404-4a4d-bad5-bf7c419c4a6b.png`다. 동일 자료는 업그레이드 ZIP의 `reference/screenshots/`에도 포함되어 있다.
- 구현 캡처는 `evidence/screenshots/wave06-dashboard-desktop.png`다.
- 전체화면 동시 비교 증거는 `evidence/screenshots/wave06-source-implementation-comparison.png`다.
- 핵심 작업영역 동시 비교 증거는 `evidence/screenshots/wave06-source-implementation-focused.png`다.
- 반응형 증거는 `evidence/screenshots/wave06-dashboard-tablet.png`와 `evidence/screenshots/wave06-dashboard-mobile.png`다.

## 정규화 조건

- 원본 픽셀 크기는 2806 × 1424이고 구현 픽셀 크기는 2816 × 1428이다.
- 원본은 약 1403 × 712 CSS px, 구현은 1408 × 714 CSS px로 해석했다. 두 캡처 모두 2배 밀도다.
- 전체 비교판에서는 두 이미지를 같은 열 너비로 정규화했다. 집중 비교판에서는 원본을 1403px, 구현을 1408px CSS 폭으로 표시하고 동일한 세로 구간을 잘라 비교했다.
- 구현 브라우저 viewport는 1408 × 714 CSS px, `deviceScaleFactor: 2`다.
- 비교 상태는 어두운 테마, PAPER, 로그인·API 키 불필요, 실제 주문 없음, 공개시장 LIVE가 아닌 OFFLINE DEMO다.
- 원본의 `라이브 PAPER 관찰` 문구는 상단 `OFFLINE FIXTURE`와 의미가 충돌한다. 구현은 사용자의 분리 수용기준에 맞춰 `오프라인 DEMO 관찰`로 명시했다.

## 필수 충실도 점검

- 글꼴과 타이포그래피는 시스템 한글 산세리프 fallback, 굵은 제목, 대문자 영문 kicker, 작은 보조문구 계층을 원본과 같은 방식으로 유지한다. 구현의 작은 진단문구도 잘리거나 겹치지 않는다.
- 간격과 레이아웃은 상단 안전 배지, 상태 띠, 탭, 자산 카드, 스캐너·차트·포지션의 3열 구조를 유지한다. v0.2의 종목·시간구간 선택은 별도 제어 띠로 추가해 원본의 주요 열 비율을 훼손하지 않는다.
- 색상과 토큰은 짙은 남청 배경, 청록 테두리, 민트 활성 상태, 황색 OFFLINE 경고, 적색 종료 상태를 일관되게 사용한다. 상태색은 텍스트 의미와 함께 제공한다.
- 이미지와 자산은 원본에 로고·사진·일러스트·비표준 아이콘이 없어서 누락된 시각 자산이 없다. 가격 시각화는 placeholder나 CSS 그림이 아니라 Lightweight Charts의 실제 캔들·호가 series다.
- 문구는 PAPER·실제 주문 없음·로그인 불필요·OFFLINE DEMO를 독립적으로 읽을 수 있게 썼다. 비전문가용 한국어와 고급진단 접기 상태를 분리했다.
- 아이콘은 기준 화면과 구현 모두 핵심 작업영역에 별도 아이콘 세트를 사용하지 않는다. 이모지·임의 SVG·CSS 장식으로 아이콘을 대체하지 않았다.
- 접근성은 키보드 접근 가능한 실제 button/select/details 요소, 활성 탭의 `aria-current`, 48px 이상 조작 높이, 명시적 label, 가로 overflow 무발생으로 확인했다.

## 화면과 상호작용 검증

- 데스크톱 1408 × 714, 태블릿 820 × 1180, 모바일 390 × 844에서 핵심 경로를 실제 브라우저로 실행했다.
- 라이브 일시정지·재개, 종목 선택, 1초·5초 시간구간 변경, 전략 ACTIVE·SHADOW 변경, LONG 토글, 거래 상세 열기·닫기, backend ReplayEngine 실행, 다음 이벤트, 성과·위험·시스템 탭, 고급진단 접기를 검증했다.
- 화면 전환 시 페이지가 최상단으로 이동하고, 모바일은 전체 문서 가로 overflow 없이 스캐너 표만 내부 스크롤한다.
- 브라우저 console error와 page error는 세 viewport 모두 0건이었다.

## 비교 이력

1. 첫 브라우저 점검에서 긴 거래·리플레이 화면을 본 뒤 탭을 바꾸면 이전 세로 스크롤 위치가 남는 P2 탐색 문제가 드러났다. `App.tsx`의 공통 화면 전환 함수가 다음 frame에서 최상단으로 이동하도록 고쳤다. 수정 후 `wave06-replay-desktop.png`와 나머지 탭 캡처에서 제목·제어영역이 처음부터 보인다.
2. 첫 밀도 점검에서 스캐너의 점수와 상세 상태가 별도 열로 겹칠 여지가 있고 모바일 폭이 과도해지는 P2 문제가 있었다. 점수와 실제 수용·거절 상태를 한 열에 묶고 표만 내부 스크롤하도록 고쳤다. 수정 후 데스크톱 집중 비교와 모바일 전체 캡처에서 포지션·차트와 충돌하지 않는다.
3. 계획 가격 표기가 entry·TP·SL 한 세트에 머물러 v0.2 수용기준의 TP1·TP2 구분이 보이지 않는 P2 정보 누락이 있었다. 차트 가격선과 포지션 표를 TP1·TP2·SL로 확장했다. 수정 후 집중 비교 구현 화면과 `wave06-dashboard-desktop.png`에서 네 가격 수준이 같은 상태로 표시된다.
4. 최종 전체·집중 비교에서는 조치가 필요한 P0·P1·P2 차이가 발견되지 않았다. 전략 탭 추가, 시간구간 선택, TP2, 실행 가능한 점수 상태, `OFFLINE DEMO` 명칭은 v0.2 기능·안전 요구를 위한 의도적 차이다.

## 잔여 P3

- 1408px보다 넓은 모니터에서 스캐너 설명 문구의 행간을 1px가량 넓히면 더 편하게 읽히지만, 현재도 잘림·겹침·대비 문제는 없다.
- 동적 시장 데이터에 따라 긴 거절 사유가 생기면 표 안에서 두 줄 이상이 될 수 있다. 현재는 줄바꿈과 내부 스크롤로 기능을 유지한다.

## 최종 결과

final result: passed
