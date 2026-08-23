# ADR-009. 단일 현재 소스와 Release 기반 과거 보존

- 상태는 Accepted다.
- 날짜는 2026-08-23이다.

## 배경

반복 업그레이드에서 구버전 source·UI·설정·생성물이 현재 구현 옆에 남으면 어떤 경로가 실행되는지 불명확해지고 AI도 낡은 파일을 현재 계약으로 오인할 수 있다. 운영 PAPER 원장은 보존해야 하지만 source history와 runtime data history는 같은 문제로 취급하면 안 된다.

## 결정

1. GitHub `main`은 현재 실행 가능한 source tree 한 벌만 가진다.
2. 과거 source는 Git history와 불변 tag로 보존한다.
3. 배포 ZIP·checksum·최종 증거는 해당 tag의 GitHub Release asset으로 보존한다.
4. `CHANGELOG.md`에는 사용자가 알아야 할 버전별 변화만 짧게 남긴다.
5. 기능·UI 교체 때 old/new 구현을 병렬로 두지 않고 같은 변경에서 이전 진입점·참조·test를 제거한다.
6. 운영 데이터는 Run·schema별로 보존하되 현재 runtime이 쓰지 않는 구형 데이터는 프로젝트 밖 migration archive로 이동하고 manifest를 남긴다.
7. CI의 repository hygiene 검사가 backup·version-copy·DB·ZIP·Parquet·build cache의 추적을 거부한다.

## 결과

- 다른 AI는 `main`을 현재 제품으로 해석할 수 있다.
- 사용자는 `CHANGELOG.md`에서 짧은 변화만 보고, 필요할 때 tag·Release에서 정확한 과거를 복원할 수 있다.
- 과거 실행데이터를 삭제하지 않으면서도 현재 source와 실행 경로를 깨끗하게 유지한다.
- 과거 버전 파일을 직접 수정하는 대신 새 버전을 만든다.

## 대안

버전별 전체 폴더를 `main`에 계속 쌓는 방식은 검색·빌드·AI 판단 경계를 흐려서 채택하지 않았다. 운영 데이터를 모두 GitHub에 올리는 방식은 용량·개인 실행상태·불변 원장 경계를 침해해 채택하지 않았다.
