"""기존 프로세스를 종료하지 않고 비어 있는 localhost 실행 포트를 선택한다."""

from __future__ import annotations

import os
import socket


def choose_local_port(preferred: int = 8870, *, attempts: int = 50) -> int:
    """preferred부터 제한된 범위만 확인하고 사용 가능한 첫 포트를 반환한다."""

    if not 1 <= preferred <= 65_535:
        raise ValueError("preferred 포트는 1..65535 범위여야 합니다.")
    if attempts <= 0 or preferred + attempts - 1 > 65_535:
        raise ValueError("포트 탐색 범위가 잘못됐습니다.")
    for port in range(preferred, preferred + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("사용 가능한 localhost 포트를 찾지 못했습니다.")


def main() -> None:
    preferred = int(os.environ.get("ROBOM_PORT_START", "8870"))
    print(choose_local_port(preferred))


if __name__ == "__main__":
    main()
