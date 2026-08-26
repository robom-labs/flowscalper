# Wave 48 대형 원장 무결성 증거

이 폴더는 2026-08-26에 2,842,066,944byte 현재 PAPER SQLite 원장을 검사하며 생성한 기계판독 증거다.

- `actual-cross-device-maintenance-integrity.json`은 유일한 최종 PASS다. 서비스를 정상 종료하고 WAL을 정리한 뒤 APFS `clonefile(2)`로 고정했다. 다른 device로 제한 전송하여 SHA-256을 대조한 사본에서 `quick_check=ok`와 외래키 위반 0을 확인했다.
- `online-backup-operator-abort.json`은 지속 writer 환경의 SQLite Online Backup이 완료 진행을 만들지 못해 운영자가 중단한 `ABORTED_OPERATOR`다.
- `online-backup-runtime-safety-abort.json`은 LIVE 안전감시가 온라인 backup을 중단한 `ABORTED_RUNTIME_SAFETY`다.
- `same-device-planned-rotation-abort.json`과 `same-device-probe-timeout-abort.json`은 감시 정책의 과민반응을 재현한 중단 증거다. 계획 회전의 중간 상태는 15초 유예하고, 단발 probe 오류는 연속 3회 전까지 증거에만 남기도록 수정했다.
- `same-device-lag-safety-abort.json`은 실제 실행 지연 p95가 500ms 상한을 넘어 정상적으로 fail-closed한 `ABORTED_RUNTIME_SAFETY`다.

중단된 경로를 PASS로 해석하면 안 된다. 각 JSON의 `status`, `error`, `runtime_monitor` 범위를 함께 읽어야 한다. 6시간·24시간 실시간 soak와 전략 수익성은 이 증거로 입증되지 않았다.
