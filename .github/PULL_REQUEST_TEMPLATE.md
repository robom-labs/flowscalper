## 변경 목적

- 사용자 요청과 해결할 문제를 적는다.

## 현재 구현 교체 여부

- [ ] 기존 진입점·component·route·copy·CSS·test를 검색했다.
- [ ] 교체 요청이면 더는 쓰지 않는 이전 구현과 참조를 같은 변경에서 제거했다.
- [ ] old·backup·copy·버전별 소스 폴더를 만들지 않았다.

## 데이터와 안전

- [ ] schema 변경에는 정방향 migration과 복구 test가 있다.
- [ ] 과거 Run을 현재 Run에 섞거나 재작성하지 않았다.
- [ ] 실제 주문·private API·인증 경로는 0이다.

## 검증

- [ ] `make repo-hygiene`.
- [ ] backend·frontend test.
- [ ] lint·typecheck·build·security.
- [ ] UI 변경은 현재 화면과 반응형 상태 확인.
- [ ] `CHANGELOG.md`와 필요한 문서·증거 갱신.
