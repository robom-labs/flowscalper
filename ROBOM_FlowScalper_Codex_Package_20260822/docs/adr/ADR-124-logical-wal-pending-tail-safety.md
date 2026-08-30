# ADR-124. WAL 전체 크기와 미확정 꼬리의 안전판정 분리

- 상태. `ACCEPTED`.
- 결정일. 2026-08-30.
- 범위. 설치 LIVE_PUBLIC PAPER 서비스의 background PASSIVE WAL checkpoint.
- 제외. SQLite 안전수준 완화, 원장 오류 무시, 실제 주문, 전략 임계값 변경.

## 재현한 결함

설치 commit `03a7a7fb065b97a6d0073c9397d84f37db0afc35`의 실제 서비스에서 공개시장 수신과
전략평가는 계속 전진했지만 신규 PAPER 진입이 저장 안전잠금에 걸렸다. checkpoint 결과는
전체 log frame 41,714개 중 41,507개가 이미 확정됐고 미확정 꼬리는 207개, 847,872 bytes였다.

기존 판정은 PASSIVE checkpoint가 한 번에 완전히 끝나지 않았을 때 전체 log frame
41,714개를 64MiB 환산 상한 16,384개와 비교했다. 이미 확정한 41,507개까지 미확정으로
계산해 `WAL_CHECKPOINT_INCOMPLETE_AND_WAL_TOO_LARGE` 영구 fault를 만들었다. 관찰 시점의
WAL 파일은 약 175MB였지만 실제 미확정 꼬리는 약 0.81MiB였다. 이 false positive 뒤 worker가
멈춰 market·candle buffer와 거래기록 최신화가 적체됐다.

## 결정

1. PASSIVE checkpoint가 정상 반환했지만 일부 frame만 남은 경우, 영구 저장 fault 여부는
   `max(0, log_frames - checkpointed_frames)`인 미확정 frame으로만 판정한다.
2. 미확정 frame이 64MiB 환산 상한에 도달하면 기존처럼 영구 fail-closed한다.
3. 미확정 꼬리가 상한보다 작으면 busy 진단을 남기고 다음 저장 flush 뒤 재시도한다.
4. checkpoint process 자체가 예외를 낸 경우에는 논리 frame을 신뢰할 수 없으므로 기존 WAL
   파일 크기 64MiB fail-closed를 유지한다.
5. SQLite FULL commit, archive 원자성, buffer 복원, 저장공간 잠금과 실제 주문 0 계약은
   바꾸지 않는다.

## 회귀 계약

- `41,714 / 41,507` 반환은 busy 1회와 재시도를 남기되 persistence fault를 만들지 않는다.
- 다음 checkpoint가 `41,714 / 41,714`이면 미확정 bytes는 0이 된다.
- `20,000 / 0`처럼 실제 미확정 frame이 상한을 넘으면 기존 영구 잠금을 유지한다.
- checkpoint 예외와 64MiB 이상 WAL 파일도 기존 영구 잠금을 유지한다.
- 수정 릴리스 설치 뒤 buffer·flush·checkpoint가 전진하고 실제 주문·인증은 0이어야 한다.

이 변경은 거래 기회를 복구하는 운영 결함 수정일 뿐 전략 수익성 증거가 아니다. 수익성과
실자금 준비는 계속 `NOT_PROVEN`, `NOT_READY`다.
